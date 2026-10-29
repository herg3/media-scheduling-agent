from pathlib import Path
import time

from scheduling_agent.agent import run_agent
from scheduling_agent.contracts import ModelAction
from scheduling_agent.model import MockModel
from scheduling_agent.repository import Repository

DATA = Path(__file__).parents[1] / "data"
REQUEST = {"request_id": "LIMITS", "channel": "short_video", "account_id": "account-demo",
           "timezone": "Asia/Shanghai", "items": [{"material_id": "M01", "requirement": {
               "mode": "EXACT", "requested_at": "2026-11-02T18:00:00+08:00"}}]}


def test_model_step_limit_is_explicit(tmp_path):
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run_agent(REQUEST, MockModel(), repo, max_model_steps=1)
    assert result["termination_reason"] == "MODEL_STEP_LIMIT"
    assert result["usage"]["model_steps_used"] == 1
    assert repo.counts()["drafts"] == 0
    repo.close()


def test_late_model_response_cannot_execute_write(tmp_path):
    class Slow:
        backend = "fake"
        def next_action(self, messages, tools, timeout_seconds):
            time.sleep(0.03)
            return ModelAction("save_schedule_draft", {"material_id": "M01",
                "proposed_at": "2026-11-02T18:00:00+08:00", "expected_revision": None,
                "decision_summary": "late write"})
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    result = run_agent(REQUEST, Slow(), repo, max_runtime_seconds=0.01)
    assert result["termination_reason"] == "DEADLINE_EXCEEDED"
    assert result["usage"]["tool_calls_used"] == 0
    assert repo.counts()["drafts"] == 0
    repo.close()


def test_empty_slots_have_explicit_uncompleted_reason(tmp_path):
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    for hour in range(10, 22):
        repo.add_existing_slot("short_video", "account-demo", f"2026-11-02T{hour:02d}:00:00+08:00", f"S{hour}")
    result = run_agent(REQUEST, MockModel(), repo)
    assert result["items"][0]["reason_code"] == "NO_AVAILABLE_SLOT"
    assert result["items"][0]["task_status"] == "incomplete"
    assert repo.counts()["drafts"] == 0
    repo.close()
