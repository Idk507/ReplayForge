"""Durable operator-side ledger for one-shot Nebius image operations.

It records OCI imports and narrowly scoped prepared image operations. It is not
a run authorization, runtime capability attestation, or receipt of execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from services.persistence.store import Store, StoreError
from services.runner.import_diagnostics import ImportDiagnostic
from services.runner.one_shot_image_import import (
    ImageImportPlan,
    ImageImportResult,
)

ImportState = Literal[
    "INTENT",
    "SUBMISSION_UNKNOWN",
    "PROVIDER_REJECTED",
    "OPERATION_KNOWN",
    "TERMINAL",
]
EventKind = Literal[
    "INTENT",
    "DIAGNOSTIC",
    "UPLOAD_INTENT",
    "UPLOAD_KNOWN",
    "SPAWN_INTENT",
    "OPERATION_KNOWN",
    "TERMINAL",
]


class ImageImportEvent(BaseModel):
    """Integrity-checked append-only event for a provider import attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    sequence: int
    kind: EventKind
    payload: dict[str, object]
    recorded_at: AwareDatetime


class ImageImportAttempt(BaseModel):
    """Durable input, operation and result identities; no credential fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    attempt_id: UUID
    project_id: str
    source_reference: str
    source_digest: str
    request_digest: str
    state: ImportState
    operation_id: UUID | None
    result_image_uuid: UUID | None
    result_status: Literal["SUCCESS", "FAILED", "CANCELLED"] | None
    events: tuple[ImageImportEvent, ...]


class _AttemptRow(BaseModel):
    """Validated database projection for one import attempt."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: UUID
    project_id: str
    source_reference: str
    source_digest: str
    request_digest: str
    state: ImportState
    operation_id: UUID | None
    result_image_uuid: UUID | None
    result_status: Literal["SUCCESS", "FAILED", "CANCELLED"] | None


class _EventRow(BaseModel):
    """Validated database projection for one append-only event."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    sequence: int
    kind: EventKind
    payload: bytes
    digest: str
    recorded_at: AwareDatetime


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _source_digest(plan: ImageImportPlan) -> str:
    body = plan.request_body()
    registry = body.get("registry")
    source = registry.get("url") if isinstance(registry, dict) else None
    if not isinstance(source, str):
        raise StoreError("IMAGE_IMPORT_SOURCE_INVALID")
    match = re.search(r"@sha256:([a-f0-9]{64})$", source)
    if match is None:
        raise StoreError("IMAGE_IMPORT_SOURCE_INVALID")
    return match.group(1)


class ImageImportJournal:
    """Claim one immutable image operation per Nebius project and retain it.

    `claim` commits before any operation POST; `operation` commits its UUID
    before polling. A duplicate source digest is sticky, including after an
    uncertain submission, and cannot be retried via this journal.
    """

    def __init__(self, store: Store, project_id: str, plan: Any) -> None:
        self._store = store
        self._project_id = project_id
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", project_id):
            raise StoreError("IMAGE_IMPORT_PROJECT_INVALID")
        if isinstance(plan, ImageImportPlan):
            bound_plan = ImageImportPlan.model_validate(plan.model_dump())
            self._source_reference = bound_plan.source
            self._source_digest = _source_digest(bound_plan)
            self._request_digest = bound_plan.request_digest()
        else:
            # The same append-only image operation ledger also binds offline
            # preparation. This structural boundary avoids a second queue/table.
            try:
                bound_plan = type(plan).model_validate(plan.model_dump())
                self._source_reference = bound_plan.journal_source_reference
                self._source_digest = bound_plan.journal_source_digest
                self._request_digest = bound_plan.request_digest()
            except (AttributeError, TypeError, ValueError):
                raise StoreError("IMAGE_OPERATION_PLAN_INVALID") from None
            if (
                not isinstance(self._source_reference, str)
                or not 32 <= len(self._source_reference) <= 512
                or not re.fullmatch(r"[a-f0-9]{64}", self._source_digest)
                or not re.fullmatch(r"[a-f0-9]{64}", self._request_digest)
            ):
                raise StoreError("IMAGE_OPERATION_PLAN_INVALID")
        self._plan = bound_plan
        self._attempt_id: UUID | None = None
        self._operation_id: UUID | None = None
        self._state: ImportState | None = None
        self._uploaded_files: dict[str, UUID] = {}
        self._pending_upload: str | None = None
        self._spawn_intent = False

    @property
    def project_id(self) -> str:
        """Provider project bound to this journal."""
        return self._project_id

    @property
    def request_digest(self) -> str:
        """The exact source-plus-timeout request body digest."""
        return self._request_digest

    @property
    def operation_id(self) -> UUID | None:
        """Known provider operation, populated on write or verified recovery."""
        return self._operation_id

    @property
    def state(self) -> ImportState | None:
        """Persisted lifecycle state, or None before loading/claiming an attempt."""
        return self._state

    def claim(self, request_digest: str) -> bool:
        """Commit an immutable intent before POST; duplicate digest stays denied."""
        if request_digest != self._request_digest or self._attempt_id is not None:
            raise StoreError("IMAGE_IMPORT_BINDING_DENIED")
        attempt_id = uuid4()
        payload: dict[str, object] = {
            "project_id": self._project_id,
            "source_digest": self._source_digest,
            "request_digest": self._request_digest,
            "source_reference": self._source_reference,
        }
        if hasattr(self._plan, "journal_intent_metadata"):
            payload["operation_metadata"] = self._plan.journal_intent_metadata
        encoded = _json_bytes(payload)
        with self._store._connection() as conn:
            row = conn.execute(
                "INSERT INTO image_import_attempts "
                "(id,project_id,source_reference,source_digest,request_digest,state) "
                "VALUES(%s,%s,%s,%s,%s,'INTENT') "
                "ON CONFLICT(project_id,source_digest) DO NOTHING RETURNING id",
                (
                    attempt_id,
                    self._project_id,
                    self._source_reference,
                    self._source_digest,
                    self._request_digest,
                ),
            ).fetchone()
            if row is None:
                return False
            self._attempt_id = attempt_id
            self._append(conn, attempt_id, "INTENT", encoded)
        self._state = "INTENT"
        return True

    def _preparation_wheels(self) -> tuple[dict[str, object], ...] | None:
        metadata = getattr(self._plan, "journal_intent_metadata", None)
        dependencies = (
            metadata.get("dependencies") if isinstance(metadata, dict) else None
        )
        if not isinstance(dependencies, list):
            return None
        if any(not isinstance(item, dict) for item in dependencies):
            raise StoreError("IMAGE_IMPORT_INTEGRITY")
        return tuple(dependencies)

    def upload_intent(self, filename: str) -> None:
        """Persist one exact wheel upload intent before its provider POST."""
        attempt_id = self._require_attempt(self._request_digest)
        wheels = self._preparation_wheels()
        if (
            wheels is None
            or self._state != "INTENT"
            or self._pending_upload is not None
            or self._spawn_intent
            or len(self._uploaded_files) >= len(wheels)
        ):
            raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
        wheel = wheels[len(self._uploaded_files)]
        if wheel.get("filename") != filename:
            raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
        payload = _json_bytes(
            {
                "filename": filename,
                "sha256": wheel.get("sha256"),
                "size_bytes": wheel.get("size_bytes"),
            }
        )
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT state FROM image_import_attempts WHERE id=%s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            events = conn.execute(
                "SELECT kind FROM image_import_events WHERE attempt_id=%s "
                "ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            if row is None or row["state"] != "INTENT":
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
            if (
                len(events) != 1 + (2 * len(self._uploaded_files))
                or not events
                or events[-1]["kind"] not in {"INTENT", "UPLOAD_KNOWN"}
            ):
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
            self._append(conn, attempt_id, "UPLOAD_INTENT", payload)
        self._pending_upload = filename

    def upload_complete(self, filename: str, file_id: UUID, digest: str) -> None:
        """Persist the verified provider file UUID and digest after upload."""
        attempt_id = self._require_attempt(self._request_digest)
        wheels = self._preparation_wheels()
        wheel = None
        for item in wheels or ():
            if item.get("filename") == filename:
                wheel = item
                break
        if (
            wheels is None
            or self._state != "INTENT"
            or self._pending_upload != filename
            or not isinstance(file_id, UUID)
            or wheel is None
            or wheel.get("sha256") != digest
        ):
            raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
        payload = _json_bytes(
            {
                "filename": filename,
                "sha256": digest,
                "size_bytes": wheel.get("size_bytes"),
                "file_uuid": str(file_id),
            }
        )
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT state FROM image_import_attempts WHERE id=%s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            latest = conn.execute(
                "SELECT kind,payload FROM image_import_events WHERE attempt_id=%s "
                "ORDER BY sequence DESC LIMIT 1",
                (attempt_id,),
            ).fetchone()
            if row is None or row["state"] != "INTENT":
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
            latest_payload = latest.get("payload") if latest is not None else None
            if not isinstance(latest_payload, (bytes, bytearray, str)):
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
            try:
                latest_filename = json.loads(latest_payload).get("filename")
            except (ValueError, TypeError):
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT") from None
            if (
                latest is None
                or latest["kind"] != "UPLOAD_INTENT"
                or latest_filename != filename
            ):
                raise StoreError("IMAGE_IMPORT_UPLOAD_CONFLICT")
            self._append(conn, attempt_id, "UPLOAD_KNOWN", payload)
        self._uploaded_files[filename] = file_id
        self._pending_upload = None

    def spawn_intent(self, uploaded: Mapping[str, UUID]) -> None:
        """Persist exact file UUID bindings before the retained spawn POST."""
        attempt_id = self._require_attempt(self._request_digest)
        wheels = self._preparation_wheels()
        if (
            wheels is None
            or self._state != "INTENT"
            or self._pending_upload is not None
            or self._spawn_intent
            or len(self._uploaded_files) != len(wheels)
            or dict(uploaded) != self._uploaded_files
        ):
            raise StoreError("IMAGE_IMPORT_SPAWN_CONFLICT")
        files = [
            {
                "filename": item["filename"],
                "sha256": item["sha256"],
                "file_uuid": str(self._uploaded_files[str(item["filename"])]),
            }
            for item in wheels
        ]
        payload = _json_bytes({"request_digest": self._request_digest, "files": files})
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT state FROM image_import_attempts WHERE id=%s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            events = conn.execute(
                "SELECT kind FROM image_import_events WHERE attempt_id=%s "
                "ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            if row is None or row["state"] != "INTENT":
                raise StoreError("IMAGE_IMPORT_SPAWN_CONFLICT")
            if (
                len(events) != 1 + (2 * len(wheels))
                or not events
                or events[-1]["kind"] != "UPLOAD_KNOWN"
            ):
                raise StoreError("IMAGE_IMPORT_SPAWN_CONFLICT")
            self._append(conn, attempt_id, "SPAWN_INTENT", payload)
        self._spawn_intent = True

    def operation(self, operation_id: UUID, request_digest: str) -> None:
        """Persist the accepted provider UUID immediately after HTTP 201."""
        attempt_id = self._require_attempt(request_digest)
        if self._preparation_wheels() is not None and not self._spawn_intent:
            raise StoreError("IMAGE_IMPORT_SPAWN_INTENT_REQUIRED")
        if not isinstance(operation_id, UUID):
            raise StoreError("IMAGE_IMPORT_OPERATION_INVALID")
        payload = _json_bytes(
            {"operation_id": str(operation_id), "request_digest": request_digest}
        )
        with self._store._connection() as conn:
            row = conn.execute(
                "UPDATE image_import_attempts SET state='OPERATION_KNOWN',"
                "operation_id=%s,updated_at=clock_timestamp() "
                "WHERE id=%s AND state='INTENT' AND operation_id IS NULL "
                "RETURNING id",
                (operation_id, attempt_id),
            ).fetchone()
            if row is None:
                raise StoreError("IMAGE_IMPORT_OPERATION_CONFLICT")
            self._append(conn, attempt_id, "OPERATION_KNOWN", payload)
        self._operation_id = operation_id
        self._state = "OPERATION_KNOWN"

    def diagnostic(self, record: ImportDiagnostic) -> None:
        """Persist only the structured safe diagnostic, never raw provider text."""
        record = ImportDiagnostic.model_validate(record.model_dump())
        attempt_id = self._attempt_id
        if attempt_id is None or record.request_sha256 != self._request_digest:
            raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_BINDING_DENIED")
        if record.outcome == "POLL_ERROR" and record.operation_id is None:
            raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_BINDING_DENIED")
        if record.outcome != "POLL_ERROR" and record.operation_id is not None:
            raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_BINDING_DENIED")
        payload = _json_bytes(record.model_dump(mode="json"))
        if len(payload) > 4096:
            raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_LIMIT")
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT state,operation_id FROM image_import_attempts "
                "WHERE id=%s FOR UPDATE",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise StoreError("IMAGE_IMPORT_NOT_FOUND")
            state = row["state"]
            operation_id = row["operation_id"]
            if record.outcome in {"SUBMISSION_UNKNOWN", "PROVIDER_REJECTED"}:
                if state != "INTENT" or operation_id is not None:
                    raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_CONFLICT")
                target_state = record.outcome
                updated = conn.execute(
                    "UPDATE image_import_attempts SET state=%s,"
                    "updated_at=clock_timestamp() WHERE id=%s AND state='INTENT' "
                    "AND operation_id IS NULL RETURNING id",
                    (target_state, attempt_id),
                ).fetchone()
                if updated is None:
                    raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_CONFLICT")
            elif state != "OPERATION_KNOWN" or operation_id != record.operation_id:
                raise StoreError("IMAGE_IMPORT_DIAGNOSTIC_CONFLICT")
            self._append(conn, attempt_id, "DIAGNOSTIC", payload)
        if record.outcome == "SUBMISSION_UNKNOWN":
            self._state = "SUBMISSION_UNKNOWN"
        elif record.outcome == "PROVIDER_REJECTED":
            self._state = "PROVIDER_REJECTED"

    def complete(self, result: ImageImportResult) -> None:
        """Persist a terminal provider image UUID/status under the claimed request."""
        result = ImageImportResult.model_validate(result.model_dump())
        attempt_id = self._require_attempt(result.request_sha256)
        if (result.status == "SUCCESS") != (result.image_uuid is not None):
            raise StoreError("IMAGE_IMPORT_RESULT_INVALID")
        payload = _json_bytes(result.model_dump(mode="json"))
        with self._store._connection() as conn:
            row = conn.execute(
                "UPDATE image_import_attempts SET state='TERMINAL',result_status=%s,"
                "result_image_uuid=%s,updated_at=clock_timestamp() "
                "WHERE id=%s AND state='OPERATION_KNOWN' AND operation_id=%s "
                "RETURNING id",
                (result.status, result.image_uuid, attempt_id, result.operation_id),
            ).fetchone()
            if row is None:
                raise StoreError("IMAGE_IMPORT_RESULT_CONFLICT")
            self._append(conn, attempt_id, "TERMINAL", payload)
        self._state = "TERMINAL"

    def _require_attempt(self, request_digest: str) -> UUID:
        if self._attempt_id is None or request_digest != self._request_digest:
            raise StoreError("IMAGE_IMPORT_ATTEMPT_REQUIRED")
        return self._attempt_id

    @staticmethod
    def _append(
        conn: psycopg.Connection[dict[str, object]],
        attempt_id: UUID,
        kind: EventKind,
        payload: bytes,
    ) -> None:
        if len(payload) > 4096:
            raise StoreError("IMAGE_IMPORT_EVENT_LIMIT")
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 AS next FROM image_import_events "
            "WHERE attempt_id=%s",
            (attempt_id,),
        ).fetchone()
        next_sequence = row.get("next") if row is not None else None
        if type(next_sequence) is not int:
            raise StoreError("IMAGE_IMPORT_EVENT_INTEGRITY")
        conn.execute(
            "INSERT INTO image_import_events(attempt_id,sequence,kind,payload,digest) "
            "VALUES(%s,%s,%s,%s,%s)",
            (
                attempt_id,
                next_sequence,
                kind,
                payload,
                hashlib.sha256(payload).hexdigest(),
            ),
        )

    def history(self) -> ImageImportAttempt:
        """Recover a claimed attempt and verify all event payload checksums."""
        if self._attempt_id is None:
            with self._store._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM image_import_attempts WHERE project_id=%s "
                    "AND source_digest=%s",
                    (self._project_id, self._source_digest),
                ).fetchone()
            if row is None:
                raise StoreError("IMAGE_IMPORT_NOT_FOUND")
            attempt_id = row.get("id")
            if not isinstance(attempt_id, UUID):
                raise StoreError("IMAGE_IMPORT_INTEGRITY")
            self._attempt_id = attempt_id
        with self._store._connection() as conn:
            raw_attempt = conn.execute(
                "SELECT id,project_id,source_reference,source_digest,request_digest,"
                "state,operation_id,result_image_uuid,result_status "
                "FROM image_import_attempts WHERE id=%s FOR SHARE",
                (self._attempt_id,),
            ).fetchone()
            rows = conn.execute(
                "SELECT sequence,kind,payload,digest,recorded_at "
                "FROM image_import_events "
                "WHERE attempt_id=%s ORDER BY sequence LIMIT 64",
                (self._attempt_id,),
            ).fetchall()
        if raw_attempt is None or len(rows) > 63:
            raise StoreError("IMAGE_IMPORT_INTEGRITY")
        try:
            attempt = _AttemptRow.model_validate(raw_attempt)
        except ValidationError:
            raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
        events: list[ImageImportEvent] = []
        for index, raw_event in enumerate(rows, start=1):
            try:
                event_row = _EventRow.model_validate(raw_event)
            except ValidationError:
                raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
            payload = event_row.payload
            if (
                event_row.sequence != index
                or hashlib.sha256(payload).hexdigest() != event_row.digest
            ):
                raise StoreError("IMAGE_IMPORT_INTEGRITY")
            try:
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError
                events.append(
                    ImageImportEvent(
                        sequence=index,
                        kind=event_row.kind,
                        payload=value,
                        recorded_at=event_row.recorded_at,
                    )
                )
            except (ValueError, ValidationError):
                raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
        self._validate_event_chain(attempt, events)
        if (
            attempt.project_id != self._project_id
            or attempt.source_digest != self._source_digest
            or attempt.request_digest != self._request_digest
            or attempt.source_reference != self._source_reference
            or not events
            or events[0].kind != "INTENT"
        ):
            raise StoreError("IMAGE_IMPORT_BINDING_DENIED")
        operation_id = attempt.operation_id
        if attempt.state in {"OPERATION_KNOWN", "TERMINAL"} and operation_id is None:
            raise StoreError("IMAGE_IMPORT_INTEGRITY")
        self._state = attempt.state
        self._operation_id = operation_id
        self._restore_preparation_progress(events)
        return ImageImportAttempt(
            attempt_id=attempt.id,
            project_id=attempt.project_id,
            source_reference=attempt.source_reference,
            source_digest=attempt.source_digest,
            request_digest=attempt.request_digest,
            state=attempt.state,
            operation_id=operation_id,
            result_image_uuid=attempt.result_image_uuid,
            result_status=attempt.result_status,
            events=tuple(events),
        )

    def _restore_preparation_progress(self, events: list[ImageImportEvent]) -> None:
        """Restore only checksummed wheel references, never a pending POST."""
        self._uploaded_files = {}
        self._pending_upload = None
        self._spawn_intent = False
        for event in events:
            if event.kind == "UPLOAD_INTENT":
                self._pending_upload = str(event.payload["filename"])
            elif event.kind == "UPLOAD_KNOWN":
                self._uploaded_files[str(event.payload["filename"])] = UUID(
                    str(event.payload["file_uuid"])
                )
                self._pending_upload = None
            elif event.kind == "SPAWN_INTENT":
                self._spawn_intent = True

    def _validate_event_chain(
        self,
        attempt: _AttemptRow,
        events: list[ImageImportEvent],
    ) -> None:
        """Validate lifecycle order and payload identities against the row."""
        if not events or events[0].kind != "INTENT":
            raise StoreError("IMAGE_IMPORT_INTEGRITY")
        expected_intent: dict[str, object] = {
            "project_id": self._project_id,
            "source_digest": self._source_digest,
            "request_digest": self._request_digest,
            "source_reference": self._source_reference,
        }
        if hasattr(self._plan, "journal_intent_metadata"):
            expected_intent["operation_metadata"] = self._plan.journal_intent_metadata
        if events[0].payload != expected_intent:
            raise StoreError("IMAGE_IMPORT_INTEGRITY")

        state = "INTENT"
        operation_id: UUID | None = None
        result_status: str | None = None
        result_image_uuid: UUID | None = None
        preparation_wheels = self._preparation_wheels()
        uploaded: dict[str, UUID] = {}
        pending_upload: str | None = None
        spawn_intent = False
        for event in events[1:]:
            if event.kind == "UPLOAD_INTENT":
                if (
                    preparation_wheels is None
                    or state != "INTENT"
                    or pending_upload is not None
                    or spawn_intent
                    or len(uploaded) >= len(preparation_wheels)
                ):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                expected = preparation_wheels[len(uploaded)]
                if event.payload != {
                    "filename": expected.get("filename"),
                    "sha256": expected.get("sha256"),
                    "size_bytes": expected.get("size_bytes"),
                }:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                pending_upload = str(expected["filename"])
            elif event.kind == "UPLOAD_KNOWN":
                if preparation_wheels is None or pending_upload is None:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                expected = None
                for item in preparation_wheels:
                    if item.get("filename") == pending_upload:
                        expected = item
                        break
                try:
                    file_uuid = UUID(str(event.payload["file_uuid"]))
                except (KeyError, ValueError, TypeError):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
                if expected is None or event.payload != {
                    "filename": pending_upload,
                    "sha256": expected.get("sha256"),
                    "size_bytes": expected.get("size_bytes"),
                    "file_uuid": str(file_uuid),
                }:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                uploaded[pending_upload] = file_uuid
                pending_upload = None
            elif event.kind == "SPAWN_INTENT":
                if (
                    preparation_wheels is None
                    or state != "INTENT"
                    or pending_upload is not None
                    or spawn_intent
                    or len(uploaded) != len(preparation_wheels)
                ):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                expected_files = [
                    {
                        "filename": item["filename"],
                        "sha256": item["sha256"],
                        "file_uuid": str(uploaded[str(item["filename"])]),
                    }
                    for item in preparation_wheels
                ]
                if event.payload != {
                    "request_digest": self._request_digest,
                    "files": expected_files,
                }:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                spawn_intent = True
            elif event.kind == "OPERATION_KNOWN":
                if state != "INTENT":
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                if preparation_wheels is not None and not spawn_intent:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                payload = event.payload
                try:
                    operation_id = UUID(str(payload["operation_id"]))
                except (KeyError, ValueError, TypeError):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
                if (
                    payload.get("request_digest") != self._request_digest
                    or operation_id != attempt.operation_id
                ):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                state = "OPERATION_KNOWN"
            elif event.kind == "DIAGNOSTIC":
                try:
                    record = ImportDiagnostic.model_validate(event.payload)
                except ValidationError:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
                if record.request_sha256 != self._request_digest:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                if record.outcome == "POLL_ERROR":
                    if (
                        state != "OPERATION_KNOWN"
                        or record.operation_id != operation_id
                    ):
                        raise StoreError("IMAGE_IMPORT_INTEGRITY")
                elif state != "INTENT" or record.operation_id is not None:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                else:
                    state = record.outcome
            elif event.kind == "TERMINAL":
                if state != "OPERATION_KNOWN":
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                try:
                    result = ImageImportResult.model_validate(event.payload)
                except ValidationError:
                    raise StoreError("IMAGE_IMPORT_INTEGRITY") from None
                if (
                    result.request_sha256 != self._request_digest
                    or result.operation_id != operation_id
                    or (result.status == "SUCCESS") != (result.image_uuid is not None)
                ):
                    raise StoreError("IMAGE_IMPORT_INTEGRITY")
                result_status = result.status
                result_image_uuid = result.image_uuid
                state = "TERMINAL"
            else:
                raise StoreError("IMAGE_IMPORT_INTEGRITY")

        if (
            state != attempt.state
            or operation_id != attempt.operation_id
            or result_status != attempt.result_status
            or result_image_uuid != attempt.result_image_uuid
        ):
            raise StoreError("IMAGE_IMPORT_INTEGRITY")
