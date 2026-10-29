"""Regressions for save outcome provenance and failed evidence refreshes."""
from pathlib import Path

import pytest

from scheduling_agent.contracts import ModelAction
from scheduling_agent.evidence import item_result
from scheduling_agent.repository import Repository, request_model
from scheduling_agent.tools import ToolContext, execute_tool


@pytest.fixture
def context(tmp_path):
    repo = Repository.initialize(tmp_path / "db.sqlite", Path(__file__).parents[1] / "data")
    req = request_model({"request_id": "GUARD", "channel": "short_video", "account_id": "account-demo",
                         "timezone": "Asia/Shanghai", "items": [{"material_id": "M01", "requirement": {
                             "mode": "EXACT", "requested_at": "2026-11-02T18:00:00+08:00"}}]})
    repo.register_request(req)
    ctx = ToolContext(repo, req, "M01")
    yield ctx
    repo.close()


def call(ctx, tool, **kwargs):
    return execute_tool(ModelAction(tool, {"material_id": "M01", "decision_summary": "regression", **kwargs}), ctx)


def prepare(ctx):
    assert call(ctx, "read_material")["ok"]
    assert call(ctx, "check_rules")["ok"]
    assert call(ctx, "query_slots", search_start="2026-11-02T18:00:00+08:00",
                search_end="2026-11-02T20:00:00+08:00")["ok"]


def save(ctx, at="2026-11-02T18:00:00+08:00", revision=None):
    return call(ctx, "save_schedule_draft", proposed_at=at, expected_revision=revision)


@pytest.mark.parametrize("at,revision,code", [
    ("2026-11-02T20:00:00+08:00", 1, "MISSING_SLOT_EVIDENCE"),
    ("2026-11-02T19:00:00+08:00", 1, "CONFIRMED_DRAFT_IMMUTABLE"),
    ("bad-time", 1, "INVALID_MODEL_ACTION"),
    ("2026-11-02T19:00:00+08:00", "1", "INVALID_MODEL_ACTION"),
])
def test_failed_new_save_does_not_reuse_previous_success(context, at, revision, code):
    prepare(context)
    assert save(context)["ok"]
    old_id = context.last_save["draft_id"]
    assert save(context, at, revision)["error"]["code"] == code
    # An unrelated later error cannot erase the save failure.
    call(context, "read_material_supplement", fields=["invalid"])
    result = item_result(context, "FINISHED", [])
    assert result["task_status"] == "failed"
    assert result["draft"]["save_status"] == "failed"
    assert result["draft"]["draft_id"] == old_id
    assert result["draft"]["is_current"] is False
    assert result["effective_at"] is None


def test_unknown_save_survives_failed_reconciliation(context):
    prepare(context)
    context.faults.update(save_schedule_draft="after_commit", get_draft="always")
    assert save(context)["error"]["code"] == "SAVE_OUTCOME_UNKNOWN"
    assert call(context, "get_draft")["error"]["code"] == "TOOL_UNAVAILABLE"
    result = item_result(context, "FINISHED", [])
    assert result["task_status"] == "incomplete"
    assert result["draft"]["save_status"] == "unknown"
    assert result["draft"]["error_code"] == "SAVE_OUTCOME_UNKNOWN"
    assert context.repo.counts()["draft_slots"] == 1
    context.faults.pop("get_draft")
    assert call(context, "get_draft")["ok"]
    assert item_result(context, "FINISHED", [])["task_status"] == "completed"


def test_failed_query_refresh_cannot_authorize_save(context):
    prepare(context)
    context.faults["query_slots"] = "once"
    assert not call(context, "query_slots", search_start="2026-11-02T18:00:00+08:00",
                    search_end="2026-11-02T20:00:00+08:00")["ok"]
    assert save(context)["error"]["code"] == "MISSING_SLOT_EVIDENCE"
    assert context.repo.counts()["drafts"] == 0


@pytest.mark.parametrize("termination", ["MODEL_STEP_LIMIT", "TOOL_CALL_LIMIT", "MODEL_UNAVAILABLE", "DEADLINE_EXCEEDED"])
def test_global_termination_preserves_verified_saved_success(context, termination):
    prepare(context)
    assert save(context)["ok"]
    assert item_result(context, termination, [])["task_status"] == "completed"


@pytest.mark.parametrize("summary,questions", [(5, []), ("finish", [5])])
def test_invalid_finish_is_bounded_protocol_failure(context, summary, questions):
    from scheduling_agent.agent import run_agent
    class InvalidFinish:
        backend = "fake"
        def next_action(self, messages, tools, timeout_seconds):
            return ModelAction("finish", {"decision_summary": summary, "suggested_questions": questions})
    result = run_agent(context.request, InvalidFinish(), context.repo)
    assert result["termination_reason"] == "INVALID_MODEL_ACTION"
    assert result["usage"]["model_steps_used"] == 2
    assert result["usage"]["tool_calls_used"] == 0


def test_unsupported_finish_records_grounding_warning(context):
    from scheduling_agent.agent import run_agent
    class UnsupportedFinish:
        backend = "fake"
        def next_action(self, messages, tools, timeout_seconds):
            return ModelAction("finish", {"decision_summary": "done", "suggested_questions": []})
    result = run_agent(context.request, UnsupportedFinish(), context.repo)
    assert result["items"][0]["task_status"] == "incomplete"
    assert "OUTPUT_GROUNDED_BY_EVIDENCE" in result["warnings"]
