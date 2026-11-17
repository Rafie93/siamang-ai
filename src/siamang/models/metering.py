"""Who used a model, for how long, and how many tokens. Attribution only: nothing here decides anything.

A caller opens a :func:`scope` that names what the work belongs to (project, task, run, draft) and a sink that keeps
the records. :func:`activity` names the agent and stage doing the work and records how long it took, whether or not a
model was called in it. :class:`~siamang.models.provider.ModelProvider` reports every call through :func:`observe`,
success or failure, with the tokens the provider reported.

Outside a scope with a sink nothing is recorded. Measuring never breaks the work it measures: a sink that fails is
logged and ignored. A new thread starts outside every scope, so :func:`submit` carries the caller's scope into a pool.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any

from siamang.log import get_logger

log = get_logger("models.metering")

Sink = Callable[[str, Mapping[str, Any]], None]
"""Called with ``"call"`` or ``"activity"`` and one record. It is called from whichever thread did the work."""

COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


@dataclass(frozen=True)
class Scope:
    sink: Sink | None = None
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    draft_id: str | None = None
    agent: str | None = None
    stage: str | None = None
    package: str | None = None

    def attribution(self) -> dict[str, str | None]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "sink"}


ATTRIBUTION = frozenset(Scope().attribution())
_current: ContextVar[Scope] = ContextVar("siamang_metering_scope", default=Scope())


def current() -> Scope:
    return _current.get()


def stamp() -> str:
    """The API's timestamp form: UTC, milliseconds, ``Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@contextmanager
def scope(*, sink: Sink | None = None, **attribution: str | None) -> Iterator[Scope]:
    """Adds to the enclosing scope for the length of the block. A value left as ``None`` keeps the enclosing one."""
    unknown = set(attribution) - ATTRIBUTION
    if unknown:
        raise TypeError(f"unknown metering fields: {sorted(unknown)}")
    changes: dict[str, Any] = {k: v for k, v in attribution.items() if v is not None}
    if sink is not None:
        changes["sink"] = sink
    token = _current.set(replace(_current.get(), **changes))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


@contextmanager
def activity(agent: str, stage: str, *, package: str | None = None) -> Iterator[None]:
    """One stretch of an agent's work, recorded when it ends, however it ends. Model calls inside belong to it.

    ``outcome`` is ``done``, or ``failed`` when the block ended with an exception: an error, a stop, or a cancellation.
    """
    began, started_at, outcome = time.monotonic(), stamp(), "failed"
    with scope(agent=agent, stage=stage, package=package) as here:
        try:
            yield
            outcome = "done"
        finally:
            _emit(here, "activity", dict(outcome=outcome, started_at=started_at, finished_at=stamp(),
                                         duration_ms=elapsed_ms(began)))


def observe(*, request_id: str, purpose: Any, stage: Any, provider_id: str, model_id: str, outcome: str,
            usage: Any, started_at: str, duration_ms: int) -> None:
    """One model call has ended. ``usage`` is what the provider reported, or ``None`` when it reported nothing."""
    here = _current.get()
    if here.sink is None:
        return
    counts = {name: _count(getattr(usage, name, None)) for name in COUNTS}
    _emit(here, "call", dict(
        request_id=request_id, purpose=_label(purpose), stage=here.stage or _label(stage), provider_id=provider_id,
        model_id=model_id, outcome=outcome, **counts, started_at=started_at, finished_at=stamp(),
        duration_ms=max(0, int(duration_ms))))


def submit(pool: Executor, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
    """``pool.submit`` that runs ``fn`` in a copy of the caller's scope: a pool's threads start outside every scope."""
    return pool.submit(copy_context().run, fn, *args, **kwargs)


def elapsed_ms(began: float) -> int:
    """Milliseconds since a ``time.monotonic()`` reading."""
    return max(0, round((time.monotonic() - began) * 1000))


def _emit(here: Scope, kind: str, record: dict[str, Any]) -> None:
    if here.sink is None:
        return
    try:
        here.sink(kind, here.attribution() | record)
    except Exception as exc:  # noqa: BLE001 - measuring must never break the work it measures
        log.warning("dropped a %s usage record: %s", kind, type(exc).__name__)


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _label(value: Any) -> str | None:
    return value[:64] if isinstance(value, str) and value else None
