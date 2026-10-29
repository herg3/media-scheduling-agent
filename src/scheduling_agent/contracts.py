"""Strict business input contracts and deterministic normalization."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHANGHAI = timezone(timedelta(hours=8))


class DomainError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass
class ModelAction:
    tool: str
    arguments: dict
    call_id: str = field(default_factory=lambda: 'call-' + uuid4().hex[:12])


ToolObservation = dict


def parse_time(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Time must include an explicit UTC offset')
    return parsed


def utc_time(value: str | datetime) -> str:
    return parse_time(value).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def display_time(value: str | datetime) -> str:
    return parse_time(value).astimezone(SHANGHAI).isoformat()


def canonical_json(value) -> str:
    def encode(obj):
        if isinstance(obj, datetime): return utc_time(obj)
        if isinstance(obj, BaseModel): return obj.model_dump(mode='json')
        raise TypeError(f'Unsupported canonical JSON value: {type(obj).__name__}')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=encode, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _input_time(value, *, endpoint=False):
    parsed = parse_time(value)
    if parsed.utcoffset() != timedelta(hours=8): raise ValueError('Only +08:00 is supported')
    if parsed.minute or parsed.second or parsed.microsecond: raise ValueError('Time must be on an hourly grid')
    if not 10 <= parsed.hour <= (22 if endpoint else 21): raise ValueError('Time outside supported hours')
    return parsed


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExactRequirement(StrictModel):
    mode: Literal['EXACT']
    requested_at: datetime

    @field_validator('requested_at', mode='before')
    @classmethod
    def valid_time(cls, value): return _input_time(value)


class WindowRequirement(StrictModel):
    mode: Literal['WINDOW']
    window_start: datetime
    window_end: datetime

    @field_validator('window_start', mode='before')
    @classmethod
    def valid_start(cls, value): return _input_time(value)

    @field_validator('window_end', mode='before')
    @classmethod
    def valid_end(cls, value): return _input_time(value, endpoint=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.window_end <= self.window_start: raise ValueError('Window end must follow start')
        if self.window_end.date() != self.window_start.date(): raise ValueError('Window must be within one business day')
        return self


Requirement = Annotated[ExactRequirement | WindowRequirement, Field(discriminator='mode')]


class SchedulingItem(StrictModel):
    material_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    requirement: Requirement | None = None


class SchedulingRequest(StrictModel):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    channel: Literal['short_video']
    account_id: str = Field(pattern=r'^account-[A-Za-z0-9_-]{1,56}$')
    timezone: Literal['Asia/Shanghai']
    items: list[SchedulingItem] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique_items(self):
        if len({i.material_id for i in self.items}) != len(self.items): raise ValueError('Duplicate material_id')
        return self


def fingerprint_request(req: SchedulingRequest) -> str:
    data = req.model_dump(mode='json')
    for item in data['items']:
        requirement = item['requirement']
        if requirement:
            for key in ('requested_at', 'window_start', 'window_end'):
                if key in requirement: requirement[key] = utc_time(requirement[key])
    return digest(data)
