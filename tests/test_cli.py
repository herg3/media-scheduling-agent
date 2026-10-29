import json, os, subprocess, sys
from pathlib import Path


def test_cli_subprocess(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'src'))
    result = subprocess.run([sys.executable, '-m', 'scheduling_agent', 'init-db', '--db', str(tmp_path/'db.sqlite'), '--data-dir', 'data'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    output = tmp_path/'run'
    request = tmp_path/'input.json'
    request.write_text(json.dumps({'request_id':'CLI-1','channel':'short_video','account_id':'account-demo','timezone':'Asia/Shanghai','items':[{'material_id':'M01','requirement':{'mode':'EXACT','requested_at':'2026-11-02T18:00:00+08:00'}}]}))
    command = [sys.executable,'-m','scheduling_agent','run','--input',str(request),'--backend','mock','--db',str(tmp_path/'db.sqlite'),'--output-dir',str(output)]
    result = subprocess.run(command,env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    original = (output/'output.json').read_bytes()
    assert subprocess.run(command,env=env,capture_output=True).returncode == 3
    assert (output/'output.json').read_bytes() == original


def test_invalid_budget(tmp_path, monkeypatch):
    from scheduling_agent.cli import main
    monkeypatch.setenv('MAX_RUNTIME_SECONDS','nan')
    assert main(['run','--input','missing.json','--backend','mock','--db','missing.sqlite','--output-dir',str(tmp_path/'out')]) == 3
    assert json.loads((tmp_path/'out/output.json').read_text(encoding='utf-8'))['error']['code'] == 'INVALID_INPUT'


def test_missing_input_is_invalid(tmp_path):
    from scheduling_agent.cli import main
    output=tmp_path/'out'
    assert main(['run','--input',str(tmp_path/'missing'),'--backend','mock','--db',str(tmp_path/'missing.sqlite'),'--output-dir',str(output)]) == 3

def test_invalid_live_request_precedes_model_configuration(tmp_path,monkeypatch):
    from scheduling_agent.cli import main
    from scheduling_agent.repository import Repository
    for key in ('LLM_API_KEY','DASHSCOPE_API_KEY','LLM_BASE_URL','LLM_MODEL'): monkeypatch.delenv(key,raising=False)
    db=tmp_path/'db.sqlite'; Repository.initialize(db,'data').close()
    request=tmp_path/'bad.json';request.write_text('{}')
    out=tmp_path/'out'
    assert main(['run','--input',str(request),'--backend','live','--db',str(db),'--output-dir',str(out)]) == 3
    assert json.loads((out/'output.json').read_text(encoding='utf-8'))['error']['code']=='INVALID_INPUT'
