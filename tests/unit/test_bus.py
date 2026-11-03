import pytest

from siamang.bus import ALL, EventBus
from siamang.contracts import Envelope, EventType, new_event


def _ev(etype="task.progress", seq=1, payload=None):
    return new_event(etype, payload or {"summary": "x"}, request_id="req_1", task_id="tsk_1", seq=seq)


def test_publish_subscribe():
    bus, got = EventBus(), []
    bus.subscribe(EventType.TASK_PROGRESS, got.append)
    ev = _ev()
    assert bus.publish(ev) == 1
    assert got == [ev]


def test_type_filtering():
    bus, got = EventBus(), []
    bus.subscribe("task.started", got.append)
    assert bus.publish(_ev()) == 0
    assert got == []


def test_wildcard():
    bus, got = EventBus(), []
    bus.subscribe(ALL, got.append)
    bus.publish(_ev())
    bus.publish(_ev("task.started", 2))
    assert [str(e.type) for e in got] == ["task.progress", "task.started"]


def test_unsubscribe():
    bus, got = EventBus(), []
    sub = bus.subscribe("task.progress", got.append)
    assert bus.unsubscribe(sub) is True
    assert bus.unsubscribe(sub) is False
    assert bus.publish(_ev()) == 0
    assert got == []


def test_deterministic_registration_order():
    bus, order = EventBus(), []
    bus.subscribe("task.progress", lambda e: order.append("a"))
    bus.subscribe(ALL, lambda e: order.append("b"))
    bus.subscribe("task.progress", lambda e: order.append("c"))
    bus.publish(_ev())
    assert order == ["a", "b", "c"]


def test_handler_error_is_fail_fast():
    bus, got = EventBus(), []

    def boom(e):
        raise RuntimeError("store down")

    bus.subscribe(ALL, boom)
    bus.subscribe(ALL, got.append)
    with pytest.raises(RuntimeError, match="store down"):
        bus.publish(_ev())
    assert got == []


def test_unsubscribe_during_dispatch_is_safe():
    bus, got = EventBus(), []
    holder = {}
    holder["sub"] = bus.subscribe(ALL, lambda e: bus.unsubscribe(holder["sub"]))
    bus.subscribe(ALL, got.append)
    bus.publish(_ev())
    assert len(got) == 1


def test_rejects_non_envelope_and_unknown_type():
    bus = EventBus()
    with pytest.raises(TypeError):
        bus.publish({"type": "task.progress"})
    with pytest.raises(ValueError):
        bus.subscribe("task.unknown", print)
    with pytest.raises(TypeError):
        bus.subscribe("task.progress", "not callable")


def test_event_serialize_deserialize_through_bus():
    bus, got = EventBus(), []
    bus.subscribe(ALL, lambda e: got.append(Envelope.from_json(e.to_json())))
    ev = _ev()
    bus.publish(ev)
    assert got == [ev]
