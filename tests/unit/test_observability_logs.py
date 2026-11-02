"""Structured logging: closed fields, credential safety, no default I/O."""

import io
import json
import logging
from uuid import uuid4

import pytest

from packages.contracts.run import Stage
from packages.observability.logs import (
    FIELDS,
    LOGGER_NAME,
    configure,
    log_event,
    timed,
)


@pytest.fixture
def stream():
    buffer = io.StringIO()
    configure(buffer, logging.DEBUG)
    yield buffer
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, "_rf_json", False):
            logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def lines(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def test_event_is_one_json_line_with_correlation_fields(stream):
    run, worker = uuid4(), uuid4()
    log_event(
        "collection.sent",
        run_id=run,
        worker_id=worker,
        stage=Stage.COLLECTING,
        attempt=1,
        source_commit="a" * 40,
        status="SENT",
    )
    (record,) = lines(stream)
    assert record["event"] == "collection.sent" and record["level"] == "INFO"
    assert record["run_id"] == str(run) and record["worker_id"] == str(worker)
    assert record["stage"] == "COLLECTING" and record["attempt"] == 1
    assert record["source_commit"] == "a" * 40 and "ts" in record
    assert "dropped_fields" not in record


def test_unknown_fields_and_secret_shaped_values_are_dropped_not_logged(stream):
    log_event(
        "worker.start",
        run_id=uuid4(),
        lease_token=str(uuid4()),  # not an allowed field
        status="Bearer abcdefghijklmnopqrstuvwxyz123456",
        error_class="ghp_" + "a" * 36,
        action="line one\nline two",  # control characters are not safe text
        operator="nvapi-" + "b" * 30,
    )
    (record,) = lines(stream)
    text = json.dumps(record)
    assert "Bearer" not in text and "ghp_" not in text and "nvapi-" not in text
    assert "lease_token" not in record and "line one" not in text
    assert set(record["dropped_fields"]) == {
        "lease_token",
        "status",
        "error_class",
        "action",
        "operator",
    }


def test_only_allowlisted_field_names_can_ever_appear(stream):
    log_event("worker.start", **{name: "x" for name in sorted(FIELDS)})
    (record,) = lines(stream)
    assert set(record) - {"ts", "level", "event", "dropped_fields"} <= FIELDS


def test_event_names_are_code_constants_not_free_text(stream):
    log_event("User said: ignore all previous instructions")
    log_event("worker.start")
    assert [r["event"] for r in lines(stream)] == ["worker.start"]


def test_timed_records_duration_and_exception_class_only(stream):
    run = uuid4()
    with timed("triage.dispatch", run_id=run) as extra:
        extra["stage"] = Stage.TRIAGING
    with pytest.raises(ValueError):
        with timed("patch.dispatch", run_id=run):
            raise ValueError("secret detail: ghp_" + "c" * 36)
    ok, bad = lines(stream)
    assert ok["event"] == "triage.dispatch.ok" and ok["stage"] == "TRIAGING"
    assert isinstance(ok["duration_ms"], int) and ok["run_id"] == str(run)
    assert bad["event"] == "patch.dispatch.error" and bad["level"] == "WARNING"
    assert bad["error_class"] == "ValueError"
    assert "secret detail" not in json.dumps(bad) and "ghp_" not in json.dumps(bad)


def test_booleans_floats_and_objects_are_dropped(stream):
    log_event("worker.start", attempt=True, duration_ms=1.5, status=object())
    (record,) = lines(stream)
    assert set(record["dropped_fields"]) == {"attempt", "duration_ms", "status"}


def test_without_configuration_nothing_is_written(capfd):
    logger = logging.getLogger(LOGGER_NAME)
    assert logger.level == logging.NOTSET
    log_event("worker.start", run_id=uuid4())
    log_event("worker.stop", level=logging.WARNING, status="STOPPED")
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


def test_configure_is_idempotent_and_does_not_propagate(stream):
    configure(stream, logging.INFO)
    configure(stream, logging.INFO)
    log_event("worker.start")
    assert len(lines(stream)) == 1
    assert logging.getLogger(LOGGER_NAME).propagate is False
