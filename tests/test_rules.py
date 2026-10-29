import json
from pathlib import Path
import pytest
from scheduling_agent.contracts import DomainError, parse_time
from scheduling_agent.policy import evaluate_rules, merge_material

DATA = Path(__file__).parents[1] / 'data'
def load(name): return json.loads((DATA / (name + '.json')).read_text(encoding='utf-8'))

@pytest.mark.parametrize('mid,status', [('M01','pass'), ('M02','needs_information'), ('M03','blocked'), ('M05','needs_review')])
def test_data_driven_rules(mid,status):
    row = next(m for m in load('materials') if m['material_id']==mid)
    result = evaluate_rules(merge_material(row, []),load('rules'),parse_time('2026-11-01T09:00:00+08:00'))
    assert result['review_status']==status
    if status != 'pass': assert result['findings'][0]['rule_id']

def test_supplement_recovers_missing_authorization_and_conflict_rejected():
    materials = {m['material_id']:m for m in load('materials')}
    supplements = load('supplements')
    merged = merge_material(materials['M04'], [s for s in supplements if s['material_id']=='M04'])
    assert evaluate_rules(merged,load('rules'),parse_time('2026-11-01T09:00:00+08:00'))['review_status']=='pass'
    with pytest.raises(DomainError) as err: merge_material(materials['M06'],[s for s in supplements if s['material_id']=='M06'])
    assert err.value.code == 'SOURCE_CONFLICT'

def test_expiry_is_exclusive_and_rejected_authorization_blocks():
    material = dict(title='synthetic', duration_seconds=30, copyright_status='authorized',authorization_expires_at='2026-11-02T18:00:00+08:00',claims=[],source_id='s')
    result=evaluate_rules(material,load('rules'),parse_time('2026-11-02T18:00:00+08:00'))
    assert result['review_status']=='blocked'
    material['copyright_status']='rejected'
    assert evaluate_rules(material,load('rules'),parse_time('2026-11-01T09:00:00+08:00'))['review_status']=='blocked'
