from pathlib import Path
import importlib.util

import pytest

from scheduling_agent.contracts import ModelAction
from scheduling_agent.repository import Repository

DATA = Path(__file__).parents[1] / "data"


def api():
    assert importlib.util.find_spec("scheduling_agent.agent") is not None, "Agent loop missing"
    from scheduling_agent.agent import run_agent
    from scheduling_agent.model import MockModel
    return run_agent, MockModel


def request(material="M01", request_id="RA"):
    return {"request_id": request_id, "channel": "short_video", "account_id": "account-demo",
            "timezone": "Asia/Shanghai", "items": [{"material_id": material, "requirement": {
                "mode": "EXACT", "requested_at": "2026-11-02T18:00:00+08:00"}}]}


def test_normal_agent_saves_real_draft(tmp_path):
    run, mock = api()
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run(request(), mock(), repo)
    assert result["items"][0]["task_status"] == "completed"
    assert result["items"][0]["draft"]["save_status"] == "saved"
    assert repo.counts() == {"drafts": 1, "draft_slots": 1}
    repo.close()


def test_conflict_changes_model_action_and_requires_confirmation(tmp_path):
    run, mock = api()
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    repo.add_existing_slot("short_video", "account-demo", "2026-11-02T18:00:00+08:00", "S18")
    model = mock()
    result = run(request(), model, repo)
    item = result["items"][0]
    assert item["task_status"] == "awaiting_confirmation"
    assert item["suggested_at"] == "2026-11-02T19:00:00+08:00"
    assert item["effective_at"] is None
    assert repo.counts() == {"drafts": 1, "draft_slots": 0}
    assert any(message["role"] == "tool" for message in model.last_messages)
    repo.close()


@pytest.mark.parametrize(("material", "status"), [("M02", "needs_input"), ("M03", "blocked"), ("M05", "needs_input")])
def test_unsafe_material_does_not_save(tmp_path, material, status):
    run, mock = api()
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run(request(material), mock(), repo)
    assert result["items"][0]["task_status"] == status
    assert repo.counts()["drafts"] == 0
    repo.close()


def test_save_failure_never_reported_as_completed(tmp_path):
    run, mock = api()
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run(request(), mock(), repo, faults={"save_schedule_draft": "before_commit"})
    assert result["items"][0]["draft"]["save_status"] == "failed"
    assert result["items"][0]["task_status"] == "failed"
    assert repo.counts() == {"drafts": 0, "draft_slots": 0}
    repo.close()


def test_loop_stops_at_tool_limit(tmp_path):
    run, _ = api()
    class LoopModel:
        backend = "fake"
        model_id = "loop-test"
        def next_action(self, messages, tools, timeout_seconds):
            return ModelAction("read_material", {"material_id": "M01", "decision_summary": "read"})
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run(request(), LoopModel(), repo, max_tool_calls=2)
    assert result["termination_reason"] == "TOOL_CALL_LIMIT"
    assert result["usage"]["tool_calls_used"] == 2
    assert result["items"][0]["task_status"] == "incomplete"
    repo.close()


def test_model_cannot_claim_success_without_tool_evidence(tmp_path):
    run, _ = api()
    class Liar:
        backend = "fake"
        model_id = "liar-test"
        def next_action(self, messages, tools, timeout_seconds):
            return ModelAction("finish", {"decision_summary": "Everything is saved and confirmed",
                                         "suggested_questions": []})
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run(request(), Liar(), repo)
    assert result["items"][0]["task_status"] == "incomplete"
    assert result["items"][0]["draft"]["save_status"] == "not_attempted"
    assert repo.counts()["drafts"] == 0
    repo.close()
