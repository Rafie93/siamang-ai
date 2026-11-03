"""Versioned, allowlisted artifact codec. Never pickle or import names from data."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from siamang.planner.plan import Plan
from siamang.planner.step import PlanStep
from siamang.analyst.analysis import SystemAnalysis
from siamang.analyst.component import Component
from siamang.analyst.finding import Finding
from siamang.programmer.patch import Patch
from siamang.programmer.change import ProposedChange
from siamang.qa.result import QAResult
from siamang.qa.finding import QAFinding
from siamang.reviewer.result import ReviewResult
from siamang.reviewer.finding import ReviewFinding
from siamang.loop.result import LoopResult
from siamang.loop.iteration import LoopIteration
from siamang.loop.feedback import LoopFeedback

TYPES = {c.__name__: c for c in (Plan, PlanStep, SystemAnalysis, Component, Finding, Patch, ProposedChange,
                                 QAResult, QAFinding, ReviewResult, ReviewFinding, LoopResult,
                                 LoopIteration, LoopFeedback)}


def encode(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        if type(value).__name__ not in TYPES or TYPES[type(value).__name__] is not type(value):
            raise ValueError('unsupported artifact')
        return {'type': type(value).__name__, 'fields': {f.name: encode(getattr(value, f.name))
                                                       for f in fields(value) if f.init}}
    if isinstance(value, Mapping):
        return {'mapping': {k: encode(v) for k, v in value.items()}}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if value is None or type(value) in (str, int, bool, float):
        return value
    raise ValueError('unsupported artifact value')


def decode(value, depth=0):
    if depth > 50:
        raise ValueError('artifact nesting limit')
    if isinstance(value, list):
        return tuple(decode(v, depth + 1) for v in value)
    if isinstance(value, dict):
        if set(value) == {'mapping'}:
            return {k: decode(v, depth + 1) for k, v in value['mapping'].items()}
        if set(value) != {'type', 'fields'} or value['type'] not in TYPES:
            raise ValueError('unknown artifact type')
        cls = TYPES[value['type']]
        allowed = {f.name for f in fields(cls) if f.init}
        if set(value['fields']) != allowed:
            raise ValueError('invalid artifact fields')
        return cls(**{k: decode(v, depth + 1) for k, v in value['fields'].items()})
    return value
