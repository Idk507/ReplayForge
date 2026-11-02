"""One JSON object per line with a closed set of correlation fields.

Only the fields in ``FIELDS`` can be emitted, so a caller cannot accidentally log a
lease token, session cookie, API key, raw prompt, source text or traceback. String
values must match a conservative pattern and are length-bounded; anything else is
dropped and counted in ``dropped_fields``. Importing this module configures nothing
and performs no I/O; ``configure`` attaches a stream handler explicitly.
"""

import json
import logging
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import IO, Any
from uuid import UUID

LOGGER_NAME = "replayforge"
FIELDS = frozenset(
    {
        "user_id",
        "repository_id",
        "installation_id",
        "run_id",
        "job_id",
        "operation_id",
        "worker_id",
        "source_commit",
        "stage",
        "attempt",
        "status",
        "duration_ms",
        "error_class",
        "trace_id",
        "action",
        "operator",
    }
)
_SAFE_TEXT = re.compile(r"[A-Za-z0-9_.:@/\- ]{1,96}")
_SECRET = re.compile(
    r"(?i)(bearer\s+\S+|ghp_\S+|ghs_\S+|github_pat_\S+|nvapi-\S+|tvly-\S+|"
    r"sk-[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)
_EVENT = re.compile(r"[a-z][a-z0-9_.]{2,63}")
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())  # no default I/O


def _clean(value: Any) -> Any | None:
    """Return a loggable scalar or None when the value is not provably safe."""
    if isinstance(value, bool):
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, int):
        return value if -(2**53) < value < 2**53 else None
    if isinstance(value, str):
        if _SECRET.search(value) or not _SAFE_TEXT.fullmatch(value):
            return None
        return value
    if hasattr(value, "value") and isinstance(value.value, str):  # enums such as Stage
        return _clean(value.value)
    return None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        fields: dict[str, Any] = getattr(record, "rf_fields", {})
        body.update(fields)
        dropped = getattr(record, "rf_dropped", ())
        if dropped:
            body["dropped_fields"] = sorted(dropped)
        return json.dumps(body, sort_keys=True, separators=(",", ":"))


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one event; unknown or unsafe fields are dropped, never raised."""
    if not _EVENT.fullmatch(event):
        return  # event names are code constants; refuse to log free text
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.isEnabledFor(level):
        return
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in fields.items():
        cleaned = _clean(value) if name in FIELDS else None
        if cleaned is None:
            dropped.append(name)
        else:
            kept[name] = cleaned
    logger.log(level, event, extra={"rf_fields": kept, "rf_dropped": tuple(dropped)})


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log ``<event>.ok`` or ``<event>.error`` with duration; re-raises unchanged.

    The yielded dict lets the caller add fields (such as the final stage) before
    exit. Exceptions are recorded by class name only: no message, no traceback.
    """
    extra: dict[str, Any] = {}
    start = time.monotonic()
    try:
        yield extra
    except BaseException as error:
        log_event(
            event + ".error",
            level=logging.WARNING,
            duration_ms=int((time.monotonic() - start) * 1000),
            error_class=type(error).__name__,
            **{**fields, **extra},
        )
        raise
    log_event(
        event + ".ok",
        duration_ms=int((time.monotonic() - start) * 1000),
        **{**fields, **extra},
    )


def configure(stream: IO[str] | None = None, level: int = logging.INFO) -> None:
    """Attach one JSON handler to the replayforge logger; safe to call repeatedly."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_rf_json", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._rf_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
