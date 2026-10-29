"""Persistence assertions exercise real SQLite state, never a mocked repository."""
from pathlib import Path
import importlib.util
import json
import sqlite3

import pytest

DATA = Path(__file__).parents[1] / "data"


def repository_class():
    spec = importlib.util.find_spec("scheduling_agent.repository")
    assert spec is not None, "Persistent repository has not been implemented"
    from scheduling_agent.repository import Repository
    return Repository


def request(request_id="R1", material_id="M01", hour=18):
    return {
        "request_id": request_id, "channel": "short_video",
        "account_id": "account-demo", "timezone": "Asia/Shanghai",
        "items": [{"material_id": material_id, "requirement": {
            "mode": "EXACT", "requested_at": f"2026-11-02T{hour:02d}:00:00+08:00"}}],
    }


def prepared(tmp_path, request_data=None):
    repo = repository_class().initialize(tmp_path / "test.sqlite", DATA)
    req = request_data or request()
    repo.register_request(req)
    evidence = repo.collect_save_evidence(req, "M01", "2026-11-02T18:00:00+08:00")
    return repo, req, evidence


def test_save_and_repeat_have_one_committed_draft(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    first = repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00", evidence=evidence)
    repeated = repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00", evidence=evidence)
    assert first["draft_id"] == repeated["draft_id"]
    assert repeated["save_status"] == "already_exists"
    assert repo.counts() == {"drafts": 1, "draft_slots": 1}
    repo.close()


def test_same_key_different_input_is_rejected(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    from scheduling_agent.contracts import DomainError
    with pytest.raises(DomainError) as error:
        repo.register_request(request(hour=20))
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert repo.counts()["drafts"] == 0
    repo.close()


def test_pending_draft_has_no_slot_and_confirmation_promotes_same_id(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    repo.add_existing_slot("short_video", "account-demo", "2026-11-02T18:00:00+08:00", "S18")
    proposal = "2026-11-02T19:00:00+08:00"
    evidence = repo.collect_save_evidence(req, "M01", proposal)
    pending = repo.save_draft(req, "M01", proposal, evidence=evidence)
    assert pending["reservation_status"] == "pending"
    assert repo.counts() == {"drafts": 1, "draft_slots": 0}
    confirmation = repo.confirmation(pending["draft_id"], 1, proposal)
    confirmed = repo.save_draft(req, "M01", proposal, expected_revision=1,
                                evidence=evidence, confirmation=confirmation)
    assert confirmed["draft_id"] == pending["draft_id"]
    assert confirmed["revision"] == 2
    assert confirmed["reservation_status"] == "reserved"
    assert repo.counts()["draft_slots"] == 1
    repo.close()


def test_save_failure_rolls_back_draft_and_slot(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    from scheduling_agent.contracts import DomainError
    with pytest.raises(DomainError) as error:
        repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00",
                        evidence=evidence, fault="before_commit")
    assert error.value.code == "SAVE_FAILED"
    assert repo.counts() == {"drafts": 0, "draft_slots": 0}
    repo.close()


def test_save_checks_new_conflict_after_query(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    repo.add_existing_slot("short_video", "account-demo", "2026-11-02T18:00:00+08:00", "S18")
    from scheduling_agent.contracts import DomainError
    with pytest.raises(DomainError) as error:
        repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00", evidence=evidence)
    assert error.value.code == "SLOT_CONFLICT"
    assert repo.counts() == {"drafts": 0, "draft_slots": 0}
    repo.close()


def test_new_process_connection_reuses_existing_draft(tmp_path):
    repo, req, evidence = prepared(tmp_path)
    first = repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00", evidence=evidence)
    db = repo.path
    repo.close()
    second_repo = repository_class()(db)
    second_repo.register_request(req)
    second = second_repo.save_draft(req, "M01", "2026-11-02T18:00:00+08:00",
                                   evidence=second_repo.collect_save_evidence(
                                       req, "M01", "2026-11-02T18:00:00+08:00"))
    assert first["draft_id"] == second["draft_id"]
    assert second_repo.counts() == {"drafts": 1, "draft_slots": 1}
    second_repo.close()


def test_primary_findings_retain_source_id(tmp_path):
    repo = repository_class().initialize(tmp_path / 'source.sqlite', DATA)
    assert repo.review('M03')['findings'][0]['source_id'] == 'synthetic-main-03'
    repo.close()


def test_pending_revision_invalidates_old_confirmation(tmp_path):
    from scheduling_agent.contracts import DomainError
    repo, req, _ = prepared(tmp_path)
    at19, at20 = '2026-11-02T19:00:00+08:00', '2026-11-02T20:00:00+08:00'
    first = repo.save_draft(req, 'M01', at19, evidence=repo.collect_save_evidence(req, 'M01', at19))
    old_confirmation = repo.confirmation(first['draft_id'], 1, at19)
    second = repo.save_draft(req, 'M01', at20, expected_revision=1, evidence=repo.collect_save_evidence(req,'M01',at20))
    assert second['draft_id'] == first['draft_id'] and second['revision'] == 2
    with pytest.raises(DomainError) as error:
        repo.save_draft(req,'M01',at20,expected_revision=2,evidence=repo.collect_save_evidence(req,'M01',at20),confirmation=old_confirmation)
    assert error.value.code == 'CONFIRMATION_MISMATCH'
    assert repo.counts() == {'drafts':1,'draft_slots':0}
    repo.close()


@pytest.mark.parametrize('material_id,code', [('M02','REVIEW_REQUIRED'),('M03','RULE_BLOCKED'),('M05','REVIEW_REQUIRED')])
def test_risk_gate_blocks_writes(tmp_path,material_id,code):
    from scheduling_agent.contracts import DomainError
    req = request(material_id=material_id)
    repo = repository_class().initialize(tmp_path / 'risk.sqlite', DATA)
    repo.register_request(req)
    at = '2026-11-02T18:00:00+08:00'
    with pytest.raises(DomainError) as error:
        repo.save_draft(req,material_id,at,evidence=repo.collect_save_evidence(req,material_id,at))
    assert error.value.code == code
    assert repo.counts() == {'drafts':0,'draft_slots':0}
    repo.close()


def test_expiry_must_cover_proposed_time_exclusively(tmp_path):
    from scheduling_agent.contracts import DomainError
    repo,req,_ = prepared(tmp_path)
    material = repo.read_material('M01')['metadata']
    material['authorization_expires_at'] = '2026-11-02T18:00:00+08:00'
    repo.connection.execute('UPDATE materials SET metadata_json=? WHERE material_id=?',(json.dumps(material),'M01'))
    at='2026-11-02T18:00:00+08:00'
    with pytest.raises(DomainError) as error: repo.save_draft(req,'M01',at,evidence=repo.collect_save_evidence(req,'M01',at))
    assert error.value.code == 'RULE_BLOCKED'
    assert repo.counts()['drafts']==0
    repo.close()


@pytest.mark.parametrize('evidence,code', [(None,'MISSING_REVIEW_EVIDENCE'), ({'read':True,'review':{'review_status':'pass'}},'MISSING_SLOT_EVIDENCE')])
def test_missing_observation_evidence_rejects_save(tmp_path,evidence,code):
    from scheduling_agent.contracts import DomainError
    repo,req,_=prepared(tmp_path)
    with pytest.raises(DomainError) as error: repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
    assert error.value.code==code
    assert repo.counts()['drafts']==0
    repo.close()


def test_response_loss_preserves_committed_readback(tmp_path):
    from scheduling_agent.contracts import DomainError
    repo,req,evidence=prepared(tmp_path)
    with pytest.raises(DomainError) as error: repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence,fault='after_commit')
    assert error.value.code=='SAVE_OUTCOME_UNKNOWN'
    assert repo.read_draft('R1','M01')['reservation_status']=='reserved'
    assert repo.counts()=={'drafts':1,'draft_slots':1}
    repo.close()


def test_concurrent_connections_preserve_one_draft_and_slot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repo,req,_=prepared(tmp_path)
    db=repo.path; repo.close()
    barrier=Barrier(2)
    def worker():
        connection=repository_class()(db)
        try:
            connection.register_request(req)
            evidence=connection.collect_save_evidence(req,'M01','2026-11-02T18:00:00+08:00')
            barrier.wait(timeout=5)
            return connection.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
        finally: connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(lambda _:worker(),range(2)))
    assert first['draft_id']==second['draft_id']
    assert {first['save_status'],second['save_status']}=={'saved','already_exists'}
    reopened=repository_class()(db)
    assert reopened.counts()=={'drafts':1,'draft_slots':1}
    reopened.close()


def test_non_lock_operational_failure_is_not_reported_as_busy(tmp_path):
    from scheduling_agent.contracts import DomainError
    repo,req,evidence=prepared(tmp_path)
    repo.connection.execute('DROP TABLE slots')
    with pytest.raises(DomainError) as error: repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
    assert error.value.code=='SAVE_FAILED'
    assert error.value.retryable is False
    repo.close()


def test_real_sqlite_lock_is_retryable_busy(tmp_path):
    from scheduling_agent.contracts import DomainError
    repo,req,evidence=prepared(tmp_path)
    blocker=sqlite3.connect(repo.path,isolation_level=None)
    repo.connection.execute('PRAGMA busy_timeout=1')
    blocker.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(DomainError) as error:
            repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
        assert error.value.code=='DB_BUSY'
        assert error.value.retryable is True
        assert repo.counts()=={'drafts':0,'draft_slots':0}
    finally:
        blocker.rollback(); blocker.close(); repo.close()


@pytest.mark.parametrize('failure',[sqlite3.OperationalError('readback storage failure'), RuntimeError('readback decoding failure')])
def test_post_commit_readback_failure_reports_unknown_and_can_be_recovered(tmp_path,monkeypatch,failure):
    from scheduling_agent.contracts import DomainError
    repo,req,evidence=prepared(tmp_path)
    original=repo.read_draft
    calls=0
    def fail_after_commit(*args):
        nonlocal calls
        calls+=1
        if calls==2:
            assert not repo.connection.in_transaction
            raise failure
        return original(*args)
    monkeypatch.setattr(repo,'read_draft',fail_after_commit)
    with pytest.raises(DomainError) as error:
        repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
    assert error.value.code=='SAVE_OUTCOME_UNKNOWN'
    assert error.value.retryable is True
    assert repo.counts()=={'drafts':1,'draft_slots':1}
    assert repo.read_draft('R1','M01')['reservation_status']=='reserved'
    repo.close()


def test_independent_readback_failure_does_not_remove_committed_state(tmp_path,monkeypatch):
    repo,req,evidence=prepared(tmp_path)
    saved=repo.save_draft(req,'M01','2026-11-02T18:00:00+08:00',evidence=evidence)
    def fail(_): raise sqlite3.OperationalError('readback unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(repo,'_draft',fail)
        with pytest.raises(sqlite3.OperationalError): repo.readback()
    assert repo.read_draft('R1','M01')['draft_id']==saved['draft_id']
    assert repo.counts()=={'drafts':1,'draft_slots':1}
    repo.close()
