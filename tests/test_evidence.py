import json
from pathlib import Path
import sqlite3

from scheduling_agent.agent import run_agent
from scheduling_agent.model import MockModel
from scheduling_agent.repository import Repository

DATA = Path(__file__).parents[1] / "data"
REQUEST = {"request_id": "EVIDENCE", "channel": "short_video", "account_id": "account-demo",
           "timezone": "Asia/Shanghai", "items": [{"material_id": "M01", "requirement": {
               "mode": "EXACT", "requested_at": "2026-11-02T18:00:00+08:00"}}]}


def test_manifest_records_source_fingerprint_and_command(tmp_path):
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    run_agent(REQUEST, MockModel(), repo, output_dir=tmp_path / "run")
    manifest = json.loads((tmp_path / "run/manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["source_tree_sha256"]) == 64
    assert isinstance(manifest["source_dirty"], bool)
    assert manifest["command"]
    repo.close()


def test_readback_failure_preserves_actual_result_file(tmp_path, monkeypatch):
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    def fail():
        raise sqlite3.OperationalError("readback offline")
    monkeypatch.setattr(repo, "readback", fail)
    result = run_agent(REQUEST, MockModel(), repo, output_dir=tmp_path / "run")
    assert result["items"][0]["draft"]["save_status"] == "saved"
    stored = json.loads((tmp_path / "run/readback.json").read_text(encoding="utf-8"))
    assert stored["unavailable"] is True
    assert json.loads((tmp_path / "run/output.json").read_text(encoding="utf-8"))["run_status"] == "completed"
    repo.close()


def test_existing_evidence_rejected_before_database_write(tmp_path):
    repo = Repository.initialize(tmp_path / "db.sqlite", DATA)
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "output.json").write_text("previous", encoding="utf-8")
    import pytest
    with pytest.raises(FileExistsError):
        run_agent(REQUEST, MockModel(), repo, output_dir=folder)
    assert repo.counts()["drafts"] == 0
    assert (folder / "output.json").read_text(encoding="utf-8") == "previous"
    repo.close()
