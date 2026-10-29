"""Whitelisted tools with trusted per-run context and evidence ledger."""
from dataclasses import dataclass, field
from datetime import timedelta

from .contracts import DomainError, display_time, parse_time, utc_time
from .repository import Repository, request_model


PROPERTIES = {
    "material_id": {"type": "string"},
    "decision_summary": {"type": "string", "minLength": 1, "maxLength": 300},
    "fields": {"type": "array", "items": {"type": "string", "enum": [
        "title", "duration_seconds", "copyright_status", "authorization_expires_at", "claims", "content_tags"]}},
    "search_start": {"type": "string", "description": "完整+08:00整点起始时间"},
    "search_end": {"type": "string", "description": "不包含的整点终点，范围不超过24小时"},
    "proposed_at": {"type": "string"},
    "expected_revision": {"type": ["integer", "null"]},
    "suggested_questions": {"type": "array", "items": {"type": "string"}},
}
TOOL_FIELDS = {
    "read_material": ["material_id"],
    "read_material_supplement": ["material_id", "fields"],
    "check_rules": ["material_id"],
    "query_slots": ["material_id", "search_start", "search_end"],
    "get_draft": ["material_id"],
    "save_schedule_draft": ["material_id", "proposed_at", "expected_revision"],
    "finish": ["suggested_questions"],
}
DESCRIPTIONS = {
    "read_material": "读取实际合成元数据与缺失字段。",
    "read_material_supplement": "按缺失字段补查已有资料；可能无结果或有来源冲突。",
    "check_rules": "执行版本化审核规则；非pass不能排期。",
    "query_slots": "查询可用时段，不占位。观察后选择原要求或待确认替代时间。",
    "get_draft": "查询本请求素材现有草稿及revision；也用于核实保存未知结果。",
    "save_schedule_draft": "保存唯一草稿，程序决定pending或reserved；会重查审核、授权与冲突。",
    "finish": "结束当前素材，状态由实际工具证据确定；可提出具体补充/确认问题。",
}


def tool_schemas():
    return [{"type": "function", "function": {
        "name": name, "description": DESCRIPTIONS[name],
        "parameters": {"type": "object", "properties": {
            key: PROPERTIES[key] for key in [*fields, "decision_summary"]},
            "required": [*fields, "decision_summary"], "additionalProperties": False},
    }} for name, fields in TOOL_FIELDS.items()]


@dataclass
class ToolContext:
    repo: Repository
    request: object
    material_id: str
    confirmation: dict | None = None
    faults: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=lambda: {
        "read": False, "review": None, "use_supplements": False, "queried_slots": []})
    observations: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)
    suggested_at: str | None = None
    last_save: dict | None = None
    last_error: dict | None = None
    save_error: dict | None = None


def execute_tool(call, context: ToolContext):
    name, args = call.tool, call.arguments
    # A failed refresh cannot leave old observations authorizing another write.
    if name == "read_material":
        context.evidence["read"] = False
        context.evidence["review"] = None
    elif name == "check_rules":
        context.evidence["review"] = None
    elif name == "query_slots":
        context.evidence["queried_slots"] = []
    elif name == "save_schedule_draft" and context.last_save:
        context.last_save = {**context.last_save, "is_current": False}
    try:
        if name not in TOOL_FIELDS or name == "finish":
            raise DomainError("INVALID_MODEL_ACTION", "工具不在白名单内。")
        allowed = set(TOOL_FIELDS[name]) | {"decision_summary"}
        if set(args) != allowed or args.get("material_id") != context.material_id:
            raise DomainError("INVALID_MODEL_ACTION", "工具参数缺失、越界或包含未授权字段。")
        if not isinstance(args["decision_summary"], str) or not 1 <= len(args["decision_summary"]) <= 300:
            raise DomainError("INVALID_MODEL_ACTION", "需要简短公开动作依据。")
        repo, material = context.repo, context.material_id
        if name == "read_material":
            primary = repo.read_material(material)
            context.evidence["read"] = True
            review = repo.review(material)
            data = {**primary, "missing_fields": [
                finding["field"] for finding in review["findings"] if finding["severity"] == "MISSING"],
                "material_version": review["material_version"]}
        elif name == "read_material_supplement":
            allowed_fields = set(PROPERTIES["fields"]["items"]["enum"])
            if not isinstance(args["fields"], list) or not set(args["fields"]) <= allowed_fields:
                raise DomainError("INVALID_MODEL_ACTION", "补查字段不在允许范围。")
            context.evidence["use_supplements"] = True
            view, version, sources = repo.material_view(material, True)
            supplemental = repo.supplements(material)
            found = {key: view[key] for key in args["fields"]
                     if any(key in item["fields"] for item in supplemental) and view.get(key) is not None}
            data = {"found_fields": found, "missing_fields": [key for key in args["fields"] if key not in found],
                    "material_version": version, "source_ids": sources}
            if found:
                context.evidence["review"] = None
        elif name == "check_rules":
            data = repo.review(material, context.evidence["use_supplements"])
            context.evidence["review"] = data
        elif name == "query_slots":
            if context.faults.get(name) == "once":
                context.faults.pop(name)
                raise DomainError("TOOL_UNAVAILABLE", "测试注入：时段工具暂不可用。", retryable=True)
            start, end = parse_time(args["search_start"]), parse_time(args["search_end"])
            for value in (start, end):
                if value.utcoffset() != timedelta(hours=8) or value.minute or value.second or value.microsecond:
                    raise DomainError("INVALID_INPUT", "时段查询必须为+08:00整点。")
            data = repo.query_slots(context.request, material, args["search_start"], args["search_end"])
            context.evidence["queried_slots"] = [utc_time(slot) for slot in data["available_slots"]]
        elif name == "get_draft":
            if context.faults.get(name) == "always":
                raise DomainError("TOOL_UNAVAILABLE", "测试注入：草稿查询不可用。", retryable=True)
            data = repo.read_draft(request_model(context.request).request_id, material)
            if not data:
                raise DomainError("DRAFT_NOT_FOUND", "本请求尚无该素材草稿。")
            if context.save_error and context.save_error["code"] == "SAVE_OUTCOME_UNKNOWN":
                context.last_save = {**data, "save_status": "already_exists", "is_current": True}
                context.last_error = None
                context.save_error = None
        else:
            context.suggested_at = display_time(args["proposed_at"])
            if args["expected_revision"] is not None and (type(args["expected_revision"]) is not int or args["expected_revision"] < 1):
                raise DomainError("INVALID_MODEL_ACTION", "草稿版本必须为正整数或null。")
            data = repo.save_draft(context.request, material, args["proposed_at"],
                                  args["expected_revision"], evidence=context.evidence,
                                  confirmation=context.confirmation, fault=context.faults.get(name))
            context.last_save = data
            context.last_error = None
            context.save_error = None
        observation = {"tool_call_id": call.call_id, "ok": True, "data": data, "error": None,
                       "evidence": {"observed_at": display_time(repo.now),
                                    "material_version": data.get("material_version"),
                                    "rules_version": data.get("rules_version"),
                                    "source_ids": data.get("source_ids", [])}}
    except (DomainError, ValueError, TypeError) as exc:
        error = exc if isinstance(exc, DomainError) else DomainError("INVALID_MODEL_ACTION", "工具参数格式错误。")
        context.last_error = {"code": error.code, "message": error.message, "retryable": error.retryable}
        if name == "save_schedule_draft":
            context.save_error = context.last_error
        observation = {"tool_call_id": call.call_id, "ok": False, "data": {}, "error": context.last_error,
                       "evidence": {"observed_at": display_time(context.repo.now)}}
    context.observations.append((name, observation))
    return observation
