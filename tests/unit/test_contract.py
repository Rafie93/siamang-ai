import json

import pytest

from siamang.contracts import (
    INBOUND,
    INVALID_ENVELOPE,
    INVALID_PAYLOAD,
    OUTBOUND,
    PAYLOADS,
    UNKNOWN_EVENT_TYPE,
    UNSUPPORTED_CONTRACT_VERSION,
    ContractError,
    Envelope,
    EventType,
    is_supported,
    new_event,
    parse_version,
    validate_message,
)
from siamang.contracts.examples import example_names
from siamang.contracts.schema import check_spec
from tests.conftest import load_fixture

V01_TYPES = {
    "task.request",
    "task.accepted",
    "task.started",
    "task.progress",
    "task.waiting_approval",
    "task.completed",
    "task.failed",
    "task.cancel",
    "task.cancelled",
    "approval.required",
    "approval.granted",
    "approval.rejected",
}


# v0.2 (Phase 10) is additive: every v0.1 type is still there, plus these (all Siamang-produced).
V02_TYPES = {"task.waiting", "task.requeued", "task.retry_requested"}


def test_exactly_the_v01_event_types():
    assert {str(t) for t in EventType} == V01_TYPES | V02_TYPES
    assert {EventType(t) for t in V02_TYPES} <= OUTBOUND
    assert set(PAYLOADS) == set(EventType)
    assert INBOUND | OUTBOUND == set(EventType)
    assert not INBOUND & OUTBOUND


def test_payload_schemas_well_formed():
    for etype, spec in PAYLOADS.items():
        assert check_spec(spec, str(etype)) == []


# --- the 10 Phase 2 examples ---------------------------------------------


def test_ten_examples_bundled():
    assert len(example_names()) == 10


@pytest.mark.parametrize("name", example_names())
def test_example_parse_validate_roundtrip(examples, name):
    data = examples[name]
    validate_message(data)
    env = Envelope.from_dict(data)
    # serialize -> deserialize without losing information
    assert env.to_dict() == data
    text = env.to_json()
    assert json.loads(text) == data
    again = Envelope.from_json(text)
    assert again == env
    assert again.to_dict() == data


# --- valid / invalid -----------------------------------------------------


def test_create_event_with_defaults():
    env = new_event(EventType.TASK_STARTED, {"summary": "Mulai."}, request_id="req_1", task_id="tsk_1", seq=2)
    assert env.event_id.startswith("evt_")
    assert env.producer == "siamang"
    assert env.contract_version == "0.2"
    assert env.to_dict()["timestamp"].endswith("Z")


def test_payload_is_copied():
    payload = {"summary": "a"}
    env = new_event("task.started", payload, request_id="req_1", task_id="tsk_1", seq=1)
    payload["summary"] = "mutated"
    env.to_dict()["payload"]["summary"] = "mutated too"
    assert env.payload["summary"] == "a"


def _err(data) -> ContractError:
    with pytest.raises(ContractError) as info:
        validate_message(data)
    return info.value


def test_missing_payload_field():
    e = _err(load_fixture("invalid", "missing_payload_field.json"))
    assert e.code == INVALID_PAYLOAD
    assert e.violations[0].path == "payload.summary"


def test_missing_envelope_field():
    e = _err(load_fixture("invalid", "missing_envelope_field.json"))
    assert e.code == INVALID_ENVELOPE
    assert e.violations[0].path == "event_id"


def test_wrong_type():
    e = _err(load_fixture("invalid", "wrong_type_seq.json"))
    assert e.code == INVALID_ENVELOPE
    assert "seq" in str(e)


def test_unsupported_major_version():
    e = _err(load_fixture("invalid", "unsupported_major.json"))
    assert e.code == UNSUPPORTED_CONTRACT_VERSION


def test_bad_timestamp():
    e = _err(load_fixture("invalid", "bad_timestamp.json"))
    assert e.code == INVALID_ENVELOPE and "timestamp" in str(e)


def test_unknown_event_type_is_ignorable():
    e = _err(load_fixture("invalid", "unknown_event_type.json"))
    assert e.code == UNKNOWN_EVENT_TYPE
    assert e.ignorable


def test_unknown_optional_fields_ignored():
    data = load_fixture("valid", "unknown_optional_fields.json")
    env = Envelope.from_dict(data)  # newer minor (0.2) + unknown fields: accepted
    assert env.contract_version == "0.2"
    assert "future_field" not in env.to_dict()  # unknown envelope field ignored
    assert env.payload["eta_secs"] == 30  # payload preserved as-is


def test_rejection_is_deterministic():
    data = load_fixture("invalid", "missing_payload_field.json")
    assert str(_err(data)) == str(_err(data))


@pytest.mark.parametrize(
    "mutate, code, path",
    [
        (lambda d: d.pop("request_id"), INVALID_ENVELOPE, "request_id"),
        (lambda d: d.update(request_id=""), INVALID_ENVELOPE, "request_id"),
        (lambda d: d.update(task_id="bad id!"), INVALID_ENVELOPE, "task_id"),
        (lambda d: d.pop("task_id"), INVALID_ENVELOPE, "task_id"),
        (lambda d: d.pop("seq"), INVALID_ENVELOPE, "seq"),
        (lambda d: d.update(seq=0), INVALID_ENVELOPE, "seq"),
        (lambda d: d.update(seq=True), INVALID_ENVELOPE, "seq"),
        (lambda d: d.update(timestamp="2026-10-04T10:15:01+07:00"), INVALID_ENVELOPE, "timestamp"),
        (lambda d: d.update(producer=""), INVALID_ENVELOPE, "producer"),
        (lambda d: d.update(payload=[]), INVALID_ENVELOPE, "payload"),
        (lambda d: d.pop("type"), INVALID_ENVELOPE, "type"),
        (lambda d: d.update(contract_version=0.1), INVALID_ENVELOPE, "contract_version"),
        (lambda d: d.pop("contract_version"), INVALID_ENVELOPE, "contract_version"),
        (lambda d: d["payload"].update(status="running"), INVALID_PAYLOAD, "payload.status"),
        (lambda d: d["payload"].update(duplicate="yes"), INVALID_PAYLOAD, "payload.duplicate"),
    ],
)
def test_invalid_envelopes(examples, mutate, code, path):
    data = json.loads(json.dumps(examples["03_task_accepted.json"]))
    mutate(data)
    e = _err(data)
    assert e.code == code
    assert e.violations[0].path == path


@pytest.mark.parametrize(
    "name, mutate, path",
    [
        ("02_task_request.json", lambda p: p.update(source="telepathy"), "payload.source"),
        ("02_task_request.json", lambda p: p.update(instruction=""), "payload.instruction"),
        ("02_task_request.json", lambda p: p["context"].update(conversation="x"), "payload.context.conversation"),
        ("04_task_started.json", lambda p: p.update(plan_outline=["s"] * 8), "payload.plan_outline"),
        ("05_task_progress.json", lambda p: p.update(percent=101), "payload.percent"),
        ("05_task_progress.json", lambda p: p["step"].pop("title"), "payload.step.title"),
        ("07_approval_required.json", lambda p: p.update(risk="extreme"), "payload.risk"),
        ("07_approval_required.json", lambda p: p.update(expires_at="soon"), "payload.expires_at"),
        ("07_approval_required.json", lambda p: p.update(default_on_timeout="approve"), "payload.default_on_timeout"),
        ("08_approval_granted.json", lambda p: p.pop("channel"), "payload.channel"),
        ("09_task_completed.json", lambda p: p.update(outcome="maybe"), "payload.outcome"),
        ("09_task_completed.json", lambda p: p["result"]["tests"].update(passed=-1), "payload.result.tests.passed"),
        ("10_task_failed.json", lambda p: p["error"].pop("retryable"), "payload.error.retryable"),
    ],
)
def test_invalid_payloads(examples, name, mutate, path):
    data = json.loads(json.dumps(examples[name]))
    mutate(data["payload"])
    e = _err(data)
    assert e.code == INVALID_PAYLOAD
    assert e.violations[0].path == path


def test_progress_percent_may_be_null(examples):
    data = json.loads(json.dumps(examples["05_task_progress.json"]))
    data["payload"]["percent"] = None
    validate_message(data)


def test_inbound_events_need_no_seq():
    validate_message(
        {
            "contract_version": "0.1",
            "type": "task.cancel",
            "event_id": "evt_1",
            "request_id": "req_1",
            "task_id": "tsk_1",
            "timestamp": "2026-10-04T10:15:00Z",
            "producer": "web-client",
            "payload": {"reason": ""},
        }
    )


def test_cancelled_and_rejected_events():
    base = dict(request_id="req_1", task_id="tsk_1")
    new_event("task.cancelled", {"summary": "Dibatalkan.", "rolled_back": True}, seq=3, **base)
    new_event(
        "approval.rejected",
        {"approval_id": "apr_1", "decided_by": "dewi", "channel": "text", "reason": "nanti saja"},
        producer="web-client",
        **base,
    )


def test_task_request_needs_no_task_id(examples):
    assert "task_id" not in examples["02_task_request.json"]


def test_non_object_and_bad_json():
    assert _err([1, 2]).code == INVALID_ENVELOPE
    with pytest.raises(ContractError) as info:
        Envelope.from_json("{ nope")
    assert info.value.code == INVALID_ENVELOPE


def test_constructor_validates():
    with pytest.raises(ContractError):
        new_event("task.started", {}, request_id="req_1", task_id="tsk_1", seq=1)
    with pytest.raises(ContractError):
        new_event("task.nonsense", {"summary": "x"}, request_id="req_1", task_id="tsk_1", seq=1)


@pytest.mark.parametrize(
    "version, ok", [("0.1", True), ("0.0", True), ("0.9", True), ("1.0", False), ("2.3", False)]
)
def test_version_support(version, ok):
    assert is_supported(version) is ok


@pytest.mark.parametrize("bad", ["1", "1.0.0", "01.0", "a.b", "", None, 1.0])
def test_version_format(bad):
    with pytest.raises(ValueError):
        parse_version(bad)
