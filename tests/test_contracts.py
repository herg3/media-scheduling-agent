import copy
import pytest
from pydantic import ValidationError
from scheduling_agent.contracts import SchedulingRequest, fingerprint_request, parse_time, utc_time, display_time
from scheduling_agent.policy import is_originally_authorized, validate_proposed_time


def request():
    return dict(request_id='REQ-1', channel='short_video', account_id='account-demo', timezone='Asia/Shanghai', items=[dict(material_id='M01', requirement=dict(mode='EXACT', requested_at='2026-11-02T18:00:00+08:00'))])


def test_fingerprint_is_stable_and_covers_business_input():
    data = request()
    a = SchedulingRequest.model_validate(data)
    assert fingerprint_request(a) == fingerprint_request(SchedulingRequest.model_validate(copy.deepcopy(data)))
    data['account_id'] = 'account-other'
    assert fingerprint_request(a) != fingerprint_request(SchedulingRequest.model_validate(data))
    assert utc_time(a.items[0].requirement.requested_at) == '2026-11-02T10:00:00Z'
    assert display_time('2026-11-02T10:00:00Z') == '2026-11-02T18:00:00+08:00'


@pytest.mark.parametrize('value', ['2026-11-02T18:30:00+08:00', '2026-11-02T18:00:00Z', '2026-11-02T18:00:00', '2026-11-02T22:00:00+08:00'])
def test_invalid_exact_times_rejected(value):
    data = request(); data['items'][0]['requirement']['requested_at'] = value
    with pytest.raises(ValidationError): SchedulingRequest.model_validate(data)


def test_count_duplicates_and_unknown_fields_rejected():
    for items in ([], request()['items'] * 2, [dict(material_id=f'M{i}') for i in range(4)]):
        data = request(); data['items'] = items
        with pytest.raises(ValidationError): SchedulingRequest.model_validate(data)
    data = request(); data['force'] = True
    with pytest.raises(ValidationError): SchedulingRequest.model_validate(data)


def test_window_half_open_and_exact_authorization():
    data = request(); data['items'][0]['requirement'] = dict(mode='WINDOW', window_start='2026-11-02T18:00:00+08:00', window_end='2026-11-02T21:00:00+08:00')
    req = SchedulingRequest.model_validate(data).items[0].requirement
    assert is_originally_authorized(req, parse_time('2026-11-02T18:00:00+08:00'))
    assert not is_originally_authorized(req, parse_time('2026-11-02T21:00:00+08:00'))
    data['items'][0]['requirement']['window_end'] = '2026-11-02T17:00:00+08:00'
    with pytest.raises(ValidationError): SchedulingRequest.model_validate(data)
    exact = SchedulingRequest.model_validate(request()).items[0].requirement
    assert not is_originally_authorized(exact, parse_time('2026-11-02T19:00:00+08:00'))


def test_proposed_past_time_rejected():
    from scheduling_agent.contracts import DomainError
    with pytest.raises(DomainError) as err:
        validate_proposed_time('2026-11-02T18:00:00+08:00', parse_time('2026-11-03T09:00:00+08:00'))
    assert err.value.code == 'TIME_IN_PAST'
