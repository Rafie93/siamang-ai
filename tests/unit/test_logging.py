import io
import json
import logging

import pytest

from siamang.config import LoggingConfig
from siamang.log import REDACTED, configure_logging, get_logger, log_context, redact


@pytest.fixture
def stream():
    s = io.StringIO()
    configure_logging(LoggingConfig(level="DEBUG"), stream=s)
    return s


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_all_levels(stream, level):
    get_logger("test").log(getattr(logging, level), "hello")
    line = stream.getvalue().strip()
    assert f" {level}" in line and "test: hello" in line


def test_level_filtering():
    s = io.StringIO()
    configure_logging(LoggingConfig(level="WARNING"), stream=s)
    log = get_logger("test")
    log.info("hidden")
    log.warning("shown")
    assert "hidden" not in s.getvalue()
    assert "shown" in s.getvalue()


def test_context_ids(stream):
    with log_context(request_id="req_1", task_id="tsk_1"):
        with log_context(event_id="evt_1"):
            get_logger("bus").info("inside")
    get_logger("bus").info("outside")
    inside, outside = stream.getvalue().strip().splitlines()
    assert "[request_id=req_1 task_id=tsk_1 event_id=evt_1]" in inside
    assert "request_id" not in outside


def test_extra_ids(stream):
    get_logger("x").info("msg", extra={"task_id": "tsk_9"})
    assert "task_id=tsk_9" in stream.getvalue()


def test_unknown_context_field():
    with pytest.raises(ValueError):
        with log_context(user="pian"):
            pass


def test_file_logging_json_lines(tmp_path):
    path = tmp_path / "logs" / "siamang.jsonl"
    configure_logging(LoggingConfig(level="INFO", console=False, file=path))
    with log_context(request_id="req_1", task_id="tsk_1", event_id="evt_1"):
        get_logger("storage.events").info("stored %s", "task.accepted")
    for h in logging.getLogger("siamang").handlers:
        h.flush()
    record = json.loads(path.read_text().strip())
    assert record["level"] == "INFO"
    assert record["component"] == "storage.events"
    assert record["message"] == "stored task.accepted"
    assert record["request_id"] == "req_1"
    assert record["task_id"] == "tsk_1"
    assert record["event_id"] == "evt_1"
    assert record["timestamp"].endswith("Z")


@pytest.mark.parametrize(
    "text",
    [
        "api_key=sk-abc123",
        "OPENAI_API_KEY: sk-abc123",
        "token=abc123",
        "password = hunter2",
        '{"secret": "abc123"}',
        "Authorization: Bearer abc123",
    ],
)
def test_redaction(text):
    out = redact(text)
    assert REDACTED in out
    assert "abc123" not in out and "hunter2" not in out


def test_secrets_never_logged(stream):
    log = get_logger("x")
    log.info("connecting with api_key=%s", "sk-live-999")
    log.info("plain", extra={"auth_token": "tok-999"})
    assert "999" not in stream.getvalue()


def test_configure_is_idempotent():
    configure_logging(LoggingConfig())
    configure_logging(LoggingConfig())
    assert len(logging.getLogger("siamang").handlers) == 1


# --- redaction stays linear (Phase 14 hardening) -----------------------------------


@pytest.mark.parametrize("text, expected", [
    ("a=password=hunter2", "a=password=[REDACTED]"),
    ('xxx"password"=1', 'xxx"password"=[REDACTED]'),
    ("x=1 token=2 secret=3", "x=1 token=[REDACTED] secret=[REDACTED]"),
    ("my_auth_token = zz", "my_auth_token = [REDACTED]"),
    ("tokenize this", "tokenize this"),
    ("key=value", "key=value"),
])
def test_redaction_edge_cases(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text", ["x" * 200_000, "token" * 40_000, "a-" * 100_000, "x " * 100_000,
                                  "password=" * 20_000])
def test_redaction_is_linear_time(text):
    import time

    started = time.perf_counter()
    redact(text)
    assert time.perf_counter() - started < 2.0  # the quadratic form took minutes on the first input
