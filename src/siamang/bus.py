"""Internal in-process event bus.

* Synchronous on purpose: no consumer in Phase 3 needs async, and sync
  dispatch is deterministic and trivially testable.
* Carries only :class:`Envelope` objects, which are valid by construction.
* Handlers run in registration order (specific-type and wildcard
  subscriptions share one ordering).
* Fail-fast: a handler exception stops dispatch and propagates to the
  publisher. Subscribe the event store first so persistence failures
  surface before any other handler runs.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass

from siamang.contracts import Envelope, EventType
from siamang.log import get_logger, log_context

Handler = Callable[[Envelope], None]

ALL = "*"

log = get_logger("bus")


@dataclass(frozen=True)
class Subscription:
    id: int
    event_type: str
    handler: Handler


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._ids = itertools.count(1)

    def subscribe(self, event_type: EventType | str, handler: Handler) -> Subscription:
        """Subscribe to one event type, or ``"*"`` for all. Returns a token for :meth:`unsubscribe`."""
        if event_type != ALL:
            event_type = EventType(event_type)  # ValueError for unknown types
        if not callable(handler):
            raise TypeError("handler must be callable")
        sub = Subscription(next(self._ids), str(event_type), handler)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> bool:
        """Remove a subscription. Returns False if it was not registered."""
        for i, sub in enumerate(self._subs):
            if sub.id == subscription.id:
                del self._subs[i]
                return True
        return False

    def subscribers(self, event_type: EventType | str) -> list[Subscription]:
        return [s for s in self._subs if s.event_type in (ALL, str(event_type))]

    def publish(self, event: Envelope) -> int:
        """Dispatch ``event`` to matching handlers. Returns the number of handlers called."""
        if not isinstance(event, Envelope):
            raise TypeError(f"EventBus.publish expects Envelope, got {type(event).__name__}")
        targets = self.subscribers(event.type)  # snapshot: (un)subscribing mid-dispatch is safe
        with log_context(request_id=event.request_id, task_id=event.task_id, event_id=event.event_id):
            log.debug("publish %s seq=%s to %d handler(s)", event.type, event.seq, len(targets))
            for sub in targets:
                sub.handler(event)
        return len(targets)
