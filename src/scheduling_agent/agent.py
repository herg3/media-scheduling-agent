"""A bounded model -> tool -> observation -> model loop."""
from collections import Counter
import json
import time
import uuid
from pathlib import Path

from .contracts import DomainError, canonical_json, fingerprint_request
from .evidence import item_result, write_run
from .prompts import SYSTEM_PROMPT
from .repository import request_model
from .tools import ToolContext, execute_tool, tool_schemas


def run_agent(request, model, repo, *, confirmation=None, faults=None,
              max_model_steps=24, max_tool_calls=20, max_runtime_seconds=180,
              output_dir=None, case_id=None):
    if output_dir and Path(output_dir).exists() and any(Path(output_dir).iterdir()):
        raise FileExistsError("Evidence directory must be empty before execution.")
    request = request_model(request)
    repo.register_request(request)
    run_id = "RUN-" + uuid.uuid4().hex
    started = time.monotonic()
    usage = {"model_steps_used": 0, "tool_calls_used": 0}
    backend = model.backend
    events, message_groups, results = [], [], []
    unsupported_finish = False
    termination = "FINISHED"
    def emit(event_type, **data):
        events.append({"schema_version": 1, "run_id": run_id, "case_id": case_id,
                       "backend": backend, "provider": getattr(model, "provider", None),
                       "model_id": getattr(model, "model_id", None),
                       "event_index": len(events), "event_type": event_type,
                       "elapsed_ms": round((time.monotonic() - started) * 1000),
                       **usage, **data})
    emit("run_started", request_fingerprint=fingerprint_request(request),
         synthetic_clock=repo.manifest["synthetic_clock"])
    for item in request.items:
        item_confirmation = confirmation if confirmation and confirmation["material_id"] == item.material_id else None
        context = ToolContext(repo, request, item.material_id, item_confirmation, dict(faults or {}))
        payload = {"request_id": request.request_id, "channel": request.channel,
                   "account_id": request.account_id, "timezone": request.timezone,
                   "synthetic_clock": repo.manifest["synthetic_clock"],
                   "item": item.model_dump(mode="json"), "confirmation": item_confirmation}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": canonical_json(payload)}]
        questions, invalid = [], 0
        finished = False
        attempts = Counter()
        while termination == "FINISHED":
            remaining = max_runtime_seconds - (time.monotonic() - started)
            if remaining <= 0:
                termination = "DEADLINE_EXCEEDED"
                break
            if usage["model_steps_used"] >= max_model_steps:
                termination = "MODEL_STEP_LIMIT"
                break
            usage["model_steps_used"] += 1
            try:
                action = model.next_action(messages, tool_schemas(), min(30, remaining))
            except DomainError as error:
                emit("protocol_error", error_code=error.code, observation_summary=error.message)
                if error.code == "INVALID_MODEL_ACTION":
                    invalid += 1
                    messages.append({"role": "user", "content": "协议错误：每轮仅一个合法工具调用；请修正。"})
                    if invalid < 2:
                        continue
                termination = error.code
                break
            if time.monotonic() - started >= max_runtime_seconds:
                termination = "DEADLINE_EXCEEDED"
                break
            emit("model_action", step=usage["model_steps_used"], tool=action.tool,
                 tool_call_id=action.call_id, arguments=action.arguments,
                 decision_summary=action.arguments.get("decision_summary"))
            assistant = {"role": "assistant", "content": None, "tool_calls": [{
                "id": action.call_id, "type": "function", "function": {
                    "name": action.tool, "arguments": canonical_json(action.arguments)}}]}
            messages.append(assistant)
            if action.tool == "finish":
                args = action.arguments
                if (set(args) != {"decision_summary", "suggested_questions"}
                        or not isinstance(args["decision_summary"], str)
                        or not 1 <= len(args["decision_summary"]) <= 300
                        or not isinstance(args["suggested_questions"], list)
                        or not all(isinstance(question, str) for question in args["suggested_questions"])):
                    invalid += 1
                    observation = {"ok": False, "error": {"code": "INVALID_MODEL_ACTION",
                                   "message": "finish参数不合法。"}, "data": {}}
                    messages.append({"role": "tool", "name": "finish", "tool_call_id": action.call_id,
                                     "content": canonical_json(observation)})
                    if invalid >= 2:
                        termination = "INVALID_MODEL_ACTION"
                    continue
                questions = [question for question in args["suggested_questions"] if isinstance(question, str)][:5]
                messages.append({"role": "tool", "name": "finish", "tool_call_id": action.call_id,
                                 "content": '{"ok":true,"data":{"finished":true}}'})
                finished = True
                break
            if usage["tool_calls_used"] >= max_tool_calls:
                termination = "TOOL_CALL_LIMIT"
                break
            usage["tool_calls_used"] += 1
            key = (action.tool, canonical_json({key: value for key, value in action.arguments.items()
                                               if key != "decision_summary"}))
            attempts[key] += 1
            if attempts[key] > 2:
                termination = "TOOL_CALL_LIMIT"
                break
            observation = execute_tool(action, context)
            messages.append({"role": "tool", "name": action.tool, "tool_call_id": action.call_id,
                             "content": canonical_json(observation)})
            emit("tool_result", step=usage["model_steps_used"], tool=action.tool,
                 tool_call_id=action.call_id, arguments=action.arguments,
                 observation_summary=observation, error_code=observation["error"]["code"] if observation["error"] else None,
                 evidence_versions=observation.get("evidence"))
            if observation["error"] and observation["error"]["code"] == "INVALID_MODEL_ACTION":
                invalid += 1
                if invalid >= 2:
                    termination = "INVALID_MODEL_ACTION"
            if time.monotonic() - started >= max_runtime_seconds:
                termination = "DEADLINE_EXCEEDED"
        results.append(item_result(context, termination, questions))
        unsupported_finish |= finished and results[-1]["task_status"] == "incomplete"
        message_groups.append({"material_id": item.material_id, "messages": messages})
    completed = sum(item["task_status"] == "completed" for item in results)
    run_status = "completed" if completed == len(results) else "partial" if completed else "incomplete"
    result = {"schema_version": 1, "run_id": run_id, "backend": backend, "run_status": run_status,
              "termination_reason": termination, "items": results, "usage": {
                  **usage, "elapsed_ms": round((time.monotonic() - started) * 1000),
                  "provider_usage": getattr(model, "usage", {})},
              "trace_path": None, "warnings": ["仅合成资料审核与本地排期草稿；尚未发布。"]}
    if backend != "live":
        result["warnings"].append("离线Mock/Fake只证明流程，不证明真实模型决策。")
    if unsupported_finish:
        result["warnings"].append("OUTPUT_GROUNDED_BY_EVIDENCE")
    for item in results:
        for warning in item.get("warnings", []):
            if warning not in result["warnings"]:
                result["warnings"].append(warning)
    emit("run_finished", termination_reason=termination, run_status=run_status)
    if output_dir:
        write_run(output_dir, request.model_dump(mode="json"), result, events, message_groups, repo, model)
    return result
