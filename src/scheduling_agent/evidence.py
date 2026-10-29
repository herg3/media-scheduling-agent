"""Grounded outputs are derived from observed tool outcomes, not model claims."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import platform
import sqlite3
import subprocess

from .contracts import display_time, parse_time
from .policy import is_originally_authorized


def item_result(context, termination_reason, questions):
    req, material = context.request, context.material_id
    item = next(item for item in req.items if item.material_id == material)
    review = context.evidence.get("review") or {}
    status = review.get("review_status", "unknown")
    draft = context.repo.read_draft(req.request_id, material)
    save = context.last_save
    error = context.last_error
    is_current = bool(save and save.get("is_current"))
    save_status = save["save_status"] if save else "not_attempted"
    if error and error["code"] == "SAVE_OUTCOME_UNKNOWN":
        save_status = "unknown"
        is_current = False
    elif error and error["code"] in {"SAVE_FAILED", "DB_BUSY", "SLOT_CONFLICT", "REVISION_CONFLICT",
                                     "CONFIRMATION_MISMATCH", "FACTS_CHANGED"}:
        save_status = "failed"
        is_current = False
    if status == "blocked" or (error and error["code"] == "RULE_BLOCKED"):
        task_status = "blocked"
        is_current = False
    elif status in {"needs_information", "needs_review"} or item.requirement is None:
        task_status = "needs_input"
    elif error and error["code"] in {"MATERIAL_NOT_FOUND", "SOURCE_CONFLICT"}:
        task_status = "needs_input"
        is_current = False
    elif save_status == "failed":
        task_status = "failed"
    elif save_status == "unknown":
        task_status = "incomplete"
    elif is_current and draft and status == "pass":
        task_status = "completed" if draft["reservation_status"] == "reserved" else "awaiting_confirmation"
    else:
        task_status = "incomplete"
    proposed = context.suggested_at or (draft["proposed_at"] if draft else None)
    confirmation_status = "not_applicable"
    if proposed and status == "pass":
        if draft and draft["confirmation"] and is_current:
            confirmation_status = "confirmed"
        elif is_originally_authorized(item.requirement, parse_time(proposed)):
            confirmation_status = "not_required"
        else:
            confirmation_status = "pending"
    pending = questions.copy()
    if task_status == "awaiting_confirmation":
        pending = [f"是否同意将素材{material}的时间调整为{proposed}？"]
    elif task_status == "needs_input" and not pending:
        pending = ["请补充或人工核对：" + ", ".join(
            finding["field"] for finding in review.get("findings", []))] if review.get("findings") else ["请补充有效素材和发布时间信息。"]
    messages = {
        "completed": "排期草稿已核实并在本地占位，尚未发布。",
        "awaiting_confirmation": "替代时间草稿已保存，待用户明确确认；尚未占位或发布。",
        "blocked": "素材存在阻断风险，未完成排期；已有历史记录不代表当前可用。",
        "needs_input": "资料或审核结论不足，请补充或人工核对。",
        "failed": "本次草稿保存失败，未记为完成。",
        "incomplete": "任务未完成；保留已知实际状态，请查看原因与Trace。",
    }
    return {
        "material_id": material, "task_status": task_status, "review_status": status,
        "risks": review.get("findings", []),
        "original_requirement": item.requirement.model_dump(mode="json") if item.requirement else None,
        "suggested_at": proposed,
        "effective_at": draft["proposed_at"] if is_current and draft and draft["reservation_status"] == "reserved" else None,
        "confirmation_status": confirmation_status, "pending_questions": pending,
        "draft": {"draft_id": draft["draft_id"] if draft else None,
                  "revision": draft["revision"] if draft else None,
                  "reservation_status": draft["reservation_status"] if draft else "none",
                  "save_status": save_status, "error_code": error["code"] if error else None,
                  "is_current": is_current},
        "evidence_refs": [observation["tool_call_id"] for _, observation in context.observations],
        "message": messages[task_status],
    }


def exit_code(result):
    if result.get("error"):
        return 3 if result["error"]["code"] in {"INVALID_INPUT", "IDEMPOTENCY_CONFLICT", "CONFIRMATION_MISMATCH"} else 4
    if result["run_status"] == "completed":
        return 0
    if result["run_status"] == "partial":
        return 5
    if result["termination_reason"] != "FINISHED" or any(item["task_status"] in {"failed", "incomplete"} for item in result["items"]):
        return 4
    return 2


def write_run(directory, request, result, events, messages, repo, model):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing evidence run.
    if (directory / "manifest.json").exists():
        raise FileExistsError("Output directory already contains a run; select a new directory.")
    def write(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write("input.json", request)
    result["trace_path"] = "trace.jsonl"
    write("output.json", result)
    (directory / "trace.jsonl").write_text("".join(
        json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    summary = []
    for index, event in enumerate(events):
        if event["event_type"] == "tool_result":
            next_event = next((candidate for candidate in events[index + 1:]
                               if candidate["event_type"] in {"model_action", "run_finished"}), None)
            summary.append({**event, "next_action": {
                "event_index": next_event["event_index"], "tool": next_event.get("tool", "termination"),
                "decision_summary": next_event.get("decision_summary"),
            } if next_event else {"tool": "termination"}})
    write("trace-summary.json", summary)
    write("model-messages.json", messages)
    write("readback.json", repo.readback() if repo else {"unavailable": True})
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[2],
                                         text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    write("manifest.json", {
        "schema_version": 1, "run_id": result["run_id"], "backend": result["backend"],
        "provider": getattr(model, "provider", None), "model_id": getattr(model, "model_id", None),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "synthetic_clock": repo.manifest["synthetic_clock"] if repo else None,
        "fixture": repo.manifest if repo else None, "source_commit": commit,
        "exit_code": exit_code(result), "command": result.get("command"),
        "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in directory.iterdir() if path.is_file()},
    })
