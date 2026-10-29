def test_evaluate_core(tmp_path):
    from scheduling_agent.evaluation import evaluate
    summary = evaluate('mock', 'examples/cases', tmp_path/'evaluation', case_ids=['E01','E02','E03','E04','E05','E06'])
    assert summary['total'] == 6
    assert summary['failed'] == 0, summary

def test_evaluation_retains_mismatch(tmp_path):
    import json, shutil
    from scheduling_agent.evaluation import evaluate
    cases=tmp_path/'cases'
    shutil.copytree('examples/cases/E01',cases/'E01')
    (cases/'E01/expected.json').write_text(json.dumps({'drafts':999}))
    summary=evaluate('mock',cases,tmp_path/'result')
    assert summary['failed'] == 1
    assert summary['results'][0]['assertions'][0]['actual'] == 1
    assert (tmp_path/'result/E01-attempt-001/run-01/readback.json').exists()

def test_confirmation_assertion_evidence(tmp_path):
    from scheduling_agent.evaluation import evaluate
    summary=evaluate('mock','examples/cases',tmp_path/'result',case_ids=['E04','E07','E08','E09'])
    assert summary['failed']==0
    rows={row['case_id']:row['actual'] for row in summary['results']}
    assert rows['E04']['effective_at']==[None]
    assert rows['E07']['revision']==[2]
    assert rows['E08']['confirmation_status']==['confirmed']
    assert rows['E09']['reservation_status']==['pending']

def test_injection_occupies_actual_proposal(tmp_path,monkeypatch):
    import json, shutil
    from scheduling_agent import evaluation
    cases=tmp_path/'cases';shutil.copytree('examples/cases/E09',cases/'E09')
    fixture=json.loads((cases/'E09/fixture.json').read_text())
    fixture['occupy_after_proposal']=True
    fixture.pop('occupy_after',None)
    (cases/'E09/fixture.json').write_text(json.dumps(fixture))
    original=evaluation.run_agent
    def choose_twenty(request,model,repo,**kwargs):
        if not kwargs.get('confirmation'):
            repo.add_existing_slot('short_video','account-demo','2026-11-02T19:00:00+08:00','test-19')
        return original(request,model,repo,**kwargs)
    monkeypatch.setattr(evaluation,'run_agent',choose_twenty)
    summary=evaluation.evaluate('mock',cases,tmp_path/'result')
    changes=json.loads((tmp_path/'result/E09-attempt-001/changes.json').read_text())
    assert changes[0]['at']=='2026-11-02T20:00:00+08:00'
    assert summary['failed']==0, summary
