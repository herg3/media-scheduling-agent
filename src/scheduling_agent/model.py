"""Live tool-calling transport and explicitly synthetic offline policy."""
from datetime import timedelta
import json
import os

import httpx

from .contracts import DomainError, ModelAction, display_time, parse_time


class LiveModel:
    backend = "live"

    def __init__(self, api_key, base_url, model_id, *, transport=None):
        if not api_key or not base_url or not model_id:
            raise DomainError("MODEL_UNAVAILABLE", "live需要配置密钥、接口和模型，不会降级到Mock。")
        if not base_url.startswith("https://"):
            raise DomainError("INVALID_INPUT", "模型接口必须使用HTTPS。")
        self.api_key, self.base_url, self.model_id = api_key, base_url.rstrip("/"), model_id
        self.provider = "dashscope" if "dashscope" in base_url else "configured-compatible-api"
        self.transport = transport
        self.usage = {}

    @classmethod
    def from_env(cls):
        key = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        base = os.getenv("LLM_BASE_URL") or ("https://dashscope.aliyuncs.com/compatible-mode/v1"
                                           if os.getenv("DASHSCOPE_API_KEY") else None)
        model = os.getenv("LLM_MODEL") or ("qwen-plus" if os.getenv("DASHSCOPE_API_KEY") else None)
        return cls(key, base, model)

    def next_action(self, messages, tools, timeout_seconds):
        payload = {"model": self.model_id, "messages": messages, "tools": tools,
                   "tool_choice": "required", "temperature": 0}
        if self.provider == "dashscope":
            payload["enable_thinking"] = False
            payload["parallel_tool_calls"] = False
        try:
            with httpx.Client(transport=self.transport, timeout=timeout_seconds) as client:
                response = client.post(self.base_url + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.api_key}, json=payload)
            if response.status_code != 200:
                raise DomainError("MODEL_UNAVAILABLE", f"模型服务HTTP {response.status_code}，未降级Mock。")
            body = response.json()
            for key, count in body.get("usage", {}).items():
                if isinstance(count, int):
                    self.usage[key] = self.usage.get(key, 0) + count
            calls = body["choices"][0]["message"].get("tool_calls") or []
            if len(calls) != 1:
                raise DomainError("INVALID_MODEL_ACTION", "每轮必须返回恰好一个工具调用或finish。")
            call = calls[0]
            arguments = json.loads(call["function"]["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be object")
            return ModelAction(call["function"]["name"], arguments, call["id"])
        except httpx.HTTPError:
            raise DomainError("MODEL_UNAVAILABLE", "模型网络请求失败或超时，未降级Mock。") from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise DomainError("INVALID_MODEL_ACTION", "模型工具响应格式非法。") from None


class MockModel:
    """Deterministic test policy; this is not evidence of real model reasoning."""
    backend = "mock"
    provider = "offline"
    model_id = "observation-aware-mock-v1"
    usage = {}

    def next_action(self, messages, tools, timeout_seconds):
        self.last_messages = messages.copy()
        current = json.loads(messages[1]["content"])
        item = current["item"]
        material = item["material_id"]
        history = {}
        last_error = None
        for message in messages:
            if message["role"] == "tool":
                result = json.loads(message["content"])
                history.setdefault(message["name"], []).append(result)
                if not result["ok"]:
                    last_error = result["error"]
        def action(tool, reason, **kwargs):
            return ModelAction(tool, {"decision_summary": reason, **kwargs})
        def finish(reason, questions=None):
            return action("finish", reason, suggested_questions=questions or [])
        def latest(tool):
            return history[tool][-1]["data"]
        if not item.get("requirement"):
            return finish("缺少发布时间要求，需要用户补充。", ["请提供发布时间或允许的时间窗口。"])
        if "read_material" not in history:
            return action("read_material", "读取素材实际元数据。", material_id=material)
        if not history["read_material"][-1]["ok"]:
            return finish("素材无法读取，明确返回资料不足。", ["请提供存在的素材ID。"])
        if "get_draft" not in history:
            return action("get_draft", "检查既有草稿，防止重复并取得修订版本。", material_id=material)
        if "check_rules" not in history:
            return action("check_rules", "依据规则审核素材。", material_id=material)
        if not history["check_rules"][-1]["ok"]:
            return finish("资料来源冲突，需要人工核对。")
        review = latest("check_rules")
        if review["review_status"] == "needs_information":
            if "read_material_supplement" not in history:
                fields = list({f["field"] for f in review["findings"]})
                return action("read_material_supplement", "审核发现缺项，查询已有补充资料。",
                              material_id=material, fields=fields)
            supplement = history["read_material_supplement"][-1]
            if not supplement["ok"]:
                return finish("补充来源冲突，停止保存并请求人工核对。")
            if supplement["data"]["found_fields"] and len(history["check_rules"]) == 1:
                return action("check_rules", "补充资料已取得，重新审核。", material_id=material)
            return finish("补查仍不足，请用户提供必要资料。",
                          ["请补充：" + ", ".join(f["field"] for f in review["findings"])])
        if review["review_status"] != "pass":
            return finish("规则阻断或需要人工复核，不能保存排期。")
        requirement = item["requirement"]
        if "query_slots" not in history:
            start = requirement.get("requested_at") or requirement["window_start"]
            end = requirement.get("window_end") or display_time(parse_time(start).replace(hour=22))
            return action("query_slots", "审核通过，查询原要求及相邻时段。",
                          material_id=material, search_start=start, search_end=end)
        query = history["query_slots"][-1]
        if not query["ok"]:
            if len(history["query_slots"]) < 2 and query["error"]["retryable"]:
                args = next(json.loads(m["tool_calls"][0]["function"]["arguments"])
                            for m in reversed(messages) if m.get("tool_calls") and
                            m["tool_calls"][0]["function"]["name"] == "query_slots")
                return action("query_slots", "时段工具瞬时失败，有限重试。",
                              **{key: value for key, value in args.items() if key != "decision_summary"})
            return finish("无法核实时段，明确未完成。")
        slots = query["data"]["available_slots"]
        if not slots:
            return finish("查询范围内无可用时段，不保存无效排期。")
        desired = current.get("confirmation", {}).get("proposed_at_utc") if current.get("confirmation") else None
        proposed = next((slot for slot in slots if desired and parse_time(slot) == parse_time(desired)), slots[0])
        if review.get("valid_until"):
            slots = [slot for slot in slots if parse_time(slot) < parse_time(review["valid_until"])]
            if not slots:
                return finish("候选时间超出授权有效期，需调整要求。")
            if proposed not in slots:
                proposed = slots[0]
        saves = history.get("save_schedule_draft", [])
        if saves:
            last = saves[-1]
            if last["ok"]:
                return finish("草稿保存结果已核实；待确认的改期仍需用户同意。")
            if last["error"]["code"] == "SAVE_OUTCOME_UNKNOWN":
                if len(history.get("get_draft", [])) < 2:
                    return action("get_draft", "保存响应未知，读取实际记录核实。", material_id=material)
                return finish("依据读回记录结束，无法核实时保留未知。")
            if last["error"]["retryable"] and len(saves) < 2:
                previous = next(json.loads(m["tool_calls"][0]["function"]["arguments"])
                                for m in reversed(messages) if m.get("tool_calls") and
                                m["tool_calls"][0]["function"]["name"] == "save_schedule_draft")
                return action("save_schedule_draft", "保存瞬时失败，使用同一幂等请求有限重试。",
                              **{key: value for key, value in previous.items() if key != "decision_summary"})
            return finish("保存失败不能记为完成。")
        draft_observation = history["get_draft"][-1]
        revision = draft_observation["data"]["revision"] if draft_observation["ok"] else None
        reason = "原时段可用，保存草稿。" if query["data"].get("requested_slot_available") else "原时段冲突，保存替代建议并等待确认。"
        return action("save_schedule_draft", reason, material_id=material,
                      proposed_at=proposed, expected_revision=revision)
