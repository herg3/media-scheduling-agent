"""Isolated fixture-based evaluations; expected assertions never enter model inputs."""
import json
from pathlib import Path
from .agent import run_agent
from .repository import Repository
from .contracts import DomainError


def evaluate(backend, cases, output_dir, *, case_ids=None, repeats=1, limits=None):
    from .cli import model_for, reserve_output
    if repeats < 1: raise ValueError('repeats must be positive')
    root = Path(output_dir); root.mkdir(parents=True,exist_ok=True)
    if (root/'summary.json').exists(): raise FileExistsError('Summary already exists')
    selected = case_ids or sorted(p.name for p in Path(cases).iterdir() if p.is_dir())
    rows = []
    for case_id in selected:
        case = Path(cases)/case_id
        request = json.loads((case/'input.json').read_text(encoding='utf-8-sig'))
        fixture = json.loads((case/'fixture.json').read_text(encoding='utf-8-sig'))
        expected = json.loads((case/'expected.json').read_text(encoding='utf-8-sig'))
        for repeat in range(1,repeats+1):
            run_root = root/f'{case_id}-attempt-{repeat:03d}'
            reserve_output(run_root)
            db = root/'.databases'/f'{case_id}-{repeat:03d}.sqlite'; db.parent.mkdir(exist_ok=True)
            repo = Repository.initialize(db,Path(__file__).resolve().parents[2]/'data')
            try:
                for hour in fixture.get('occupied_hours',[]):
                    repo.add_existing_slot('short_video','account-demo',f'2026-11-02T{hour:02d}:00:00+08:00',f'fixture-{hour}')
                runs = []
                changes = []
                count = fixture.get('subruns',1)
                if fixture.get('followup'): count = 2
                for subrun in range(count):
                    confirmation = None
                    if subrun and (fixture.get('occupy_after') or fixture.get('occupy_after_proposal')):
                        first_draft = repo.get_draft_by_id(runs[0]['items'][0]['draft']['draft_id'])
                        at = first_draft['proposed_at'] if fixture.get('occupy_after_proposal') else f"2026-11-02T{fixture['occupy_after']:02d}:00:00+08:00"
                        before = repo.readback()
                        repo.add_existing_slot('short_video','account-demo',at,'injected-after')
                        changes.append({'action':'occupy_proposed_slot','at':at,'draft_id':first_draft['draft_id'],'before':before,'after':repo.readback()})
                        (run_root/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
                    if subrun and fixture.get('followup') == 'confirm':
                        draft = repo.get_draft_by_id(runs[0]['items'][0]['draft']['draft_id'])
                        confirmation = repo.confirmation(draft['draft_id'],draft['revision'],draft['proposed_at'])
                    runs.append(run_agent(request,model_for(backend),repo,confirmation=confirmation,faults=fixture.get('faults'),output_dir=run_root/f'run-{subrun+1:02d}',case_id=case_id,**(limits or {})))
                actual = {'task_status':[i['task_status'] for i in runs[-1]['items']], 'save_status':[i['draft']['save_status'] for i in runs[-1]['items']],**repo.counts()}
                items = runs[-1]['items']
                for field in ('review_status', 'confirmation_status', 'effective_at', 'suggested_at'):
                    actual[field] = [item[field] for item in items]
                for field in ('reservation_status', 'revision'):
                    actual[field] = [item['draft'][field] for item in items]
                actual['original_requested_at'] = [(item.get('original_requirement') or {}).get('requested_at') for item in items]
                actual['first_suggested_at'] = [item['suggested_at'] for item in runs[0]['items']]
                actual['suggestion_changed'] = actual['first_suggested_at'] != actual['suggested_at']
                actual['not_completed'] = all(item['task_status'] != 'completed' for item in items)
                actual['risk_ids'] = [[risk['rule_id'] for risk in item['risks']] for item in items]
                if count > 1:
                    actual['same_draft_id'] = runs[0]['items'][0]['draft']['draft_id'] == runs[-1]['items'][0]['draft']['draft_id']
                assertions = [{'field':key,'expected':value,'actual':actual.get(key),'passed':actual.get(key)==value} for key,value in expected.items()]
                rows.append({'case_id':case_id,'repeat':repeat,'backend':backend,'passed':all(a['passed'] for a in assertions),'assertions':assertions,'actual':actual,'runs':[r['run_id'] for r in runs]})
            except Exception as error:
                code = error.code if isinstance(error, DomainError) else 'EVALUATION_UNAVAILABLE'
                rows.append({'case_id':case_id,'repeat':repeat,'backend':backend,'passed':False,'error':code})
            finally: repo.close()
    summary = {'backend':backend,'total':len(rows),'passed':sum(r['passed'] for r in rows),'failed':sum(not r['passed'] for r in rows),'results':rows}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary
