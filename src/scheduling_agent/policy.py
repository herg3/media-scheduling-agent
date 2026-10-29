"""Pure business policies shared by observations and transactional save gates."""
from copy import deepcopy
from datetime import datetime, timedelta
import re
from .contracts import DomainError, SHANGHAI, parse_time, utc_time


def merge_material(main: dict, supplements: list[dict]) -> dict:
    merged = deepcopy(main.get('metadata', main))
    merged['source_id'] = main.get('source_id', merged.get('source_id', 'unknown'))
    sources = {key: merged['source_id'] for key in merged if key != 'source_id'}
    for supplement in supplements:
        for key, value in supplement.get('fields', {}).items():
            if value is None or value == '' or value == []: continue
            existing = merged.get(key)
            if existing is not None and existing != '' and existing != [] and existing != value:
                raise DomainError('SOURCE_CONFLICT', f'Conflicting source values for {key}')
            merged[key] = deepcopy(value)
            sources[key] = supplement.get('source_id', 'unknown')
    merged['_field_sources'] = sources
    return merged


def evaluate_rules(material: dict, rules: list[dict], now: datetime) -> dict:
    findings = []
    expires = material.get('authorization_expires_at')
    valid_until = utc_time(expires) if expires else None
    def add(rule, field, message=None, severity=None):
        findings.append(dict(rule_id=rule['rule_id'], severity=severity or rule.get('severity','BLOCK'), field=field,
                             message=message or rule.get('message',rule['kind']), source_id=material.get('_field_sources',{}).get(field,material.get('source_id','unknown'))))
    for rule in rules:
        if not rule.get('enabled', True): continue
        kind = rule['kind']
        if kind == 'required_fields':
            for name in rule['fields']:
                value = material.get(name)
                if value is None or value == '' or value == [] or (name == 'duration_seconds' and (not isinstance(value,(int,float)) or isinstance(value,bool) or value <= 0)):
                    add(rule,name)
        elif kind == 'copyright_expired':
            if material.get('copyright_status') not in (None,'','authorized'):
                add(rule,'copyright_status','Authorization is rejected or not valid')
            if expires and parse_time(expires) <= parse_time(now): add(rule,'authorization_expires_at')
        elif kind in ('forbidden_claim','review_claim'):
            claims = material.get('claims', [])
            text = ' '.join(claims) if isinstance(claims,list) else str(claims)
            if re.search(rule['pattern'],text,re.IGNORECASE): add(rule,'claims')
        else:
            raise DomainError('INVALID_INPUT',f'Unknown enabled rule kind: {kind}')
    severities = {f['severity'].upper() for f in findings}
    status = 'blocked' if 'BLOCK' in severities else 'needs_information' if severities & {'MISSING','NEEDS_INFORMATION'} else 'needs_review' if findings else 'pass'
    return dict(review_status=status,findings=findings,valid_until=valid_until)


def is_originally_authorized(requirement, proposed_at: datetime) -> bool:
    if requirement is None: return False
    requirement = requirement.model_dump() if hasattr(requirement,'model_dump') else requirement
    proposed = parse_time(proposed_at)
    if requirement['mode'] == 'EXACT': return proposed == parse_time(requirement['requested_at'])
    return parse_time(requirement['window_start']) <= proposed < parse_time(requirement['window_end'])


def validate_proposed_time(proposed, now) -> None:
    try:
        value = parse_time(proposed)
        local = value.astimezone(SHANGHAI)
        if value.utcoffset() != timedelta(hours=8) or local.minute or local.second or local.microsecond or not 10 <= local.hour <= 21:
            raise ValueError('Expected +08:00 hourly slot between 10:00 and 21:00')
    except (ValueError, TypeError) as exc:
        raise DomainError('INVALID_INPUT',str(exc)) from exc
    if value <= parse_time(now): raise DomainError('TIME_IN_PAST','Proposed time must be in the future')
