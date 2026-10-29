"""PostgreSQL recovery tests for the operator-only image import journal."""

import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest

from services.persistence.image_imports import ImageImportJournal
from services.persistence.store import Store, StoreError
from services.runner.image_import_controller import ImageImportController
from services.runner.image_preparation import ImagePreparationPlan
from services.runner.import_diagnostics import ImportDiagnostic
from services.runner.one_shot_image_import import (
    ImageImportConfig,
    ImageImportPlan,
    ImageImportResult,
    OneShotNebiusImageImporter,
)
from tests.integration import test_postgres

store = test_postgres.store
PROJECT = "aiproject-journal-test"
SOURCE = "docker://docker.io/library/python@sha256:" + ("b" * 64)


def plan(timeout: int = 30, source: str | None = None) -> ImageImportPlan:
    reference = source or (
        "docker://docker.io/library/python@sha256:" + (uuid4().hex + uuid4().hex)
    )
    return ImageImportPlan(source=reference, timeout_seconds=timeout)


def preparation_plan() -> ImagePreparationPlan:
    return ImagePreparationPlan.from_wheelhouse(
        manifest_path=Path("docs/evidence/nebius-pytest-wheelhouse.json"),
        wheelhouse=Path("artifacts/nebius-pytest-wheelhouse"),
    )


def diagnostic(
    outcome: str, request_sha256: str, operation_id=None
) -> ImportDiagnostic:
    return ImportDiagnostic(
        outcome=outcome,
        observed_at="2026-10-26T10:00:00Z",
        method="GET" if outcome == "POLL_ERROR" else "POST",
        path=(
            f"/sandboxes/v1/operations/{operation_id}"
            if outcome == "POLL_ERROR" and operation_id
            else "/sandboxes/v1/images/import"
        ),
        operation_id=operation_id,
        request_sha256=request_sha256,
        sdk_version="0.3.6",
        error_class="ApiTimeoutError",
    )


def test_uncertain_submission_is_sticky_across_restart(store: Store) -> None:
    original_plan = plan()
    journal = ImageImportJournal(store, PROJECT, original_plan)
    assert journal.claim(journal.request_digest) is True
    journal.diagnostic(diagnostic("SUBMISSION_UNKNOWN", journal.request_digest))

    restarted = ImageImportJournal(Store(store.dsn), PROJECT, original_plan)
    recovered = restarted.history()
    assert recovered.state == "SUBMISSION_UNKNOWN"
    assert [event.kind for event in recovered.events] == ["INTENT", "DIAGNOSTIC"]
    # A new timeout/request hash still cannot repeat an import for this digest.
    changed_request = ImageImportJournal(
        Store(store.dsn), PROJECT, plan(60, original_plan.source)
    )
    assert changed_request.claim(changed_request.request_digest) is False


def test_provider_rejection_is_durable_and_does_not_retry(store: Store) -> None:
    original_plan = plan()
    journal = ImageImportJournal(store, PROJECT, original_plan)
    assert journal.claim(journal.request_digest) is True
    journal.diagnostic(diagnostic("PROVIDER_REJECTED", journal.request_digest))

    recovered = ImageImportJournal(Store(store.dsn), PROJECT, original_plan).history()
    assert recovered.state == "PROVIDER_REJECTED"
    assert recovered.operation_id is None
    assert (
        ImageImportJournal(Store(store.dsn), PROJECT, original_plan).claim(
            journal.request_digest
        )
        is False
    )


def test_preparation_plan_uses_existing_durable_journal(store: Store) -> None:
    operation = uuid4()
    prep = preparation_plan()
    journal = ImageImportJournal(store, PROJECT, prep)
    assert journal.claim(journal.request_digest) is True
    uploads = {}
    for wheel in prep.wheels:
        file_id = uuid4()
        journal.upload_intent(wheel.filename)
        journal.upload_complete(wheel.filename, file_id, wheel.sha256)
        uploads[wheel.filename] = file_id
    journal.spawn_intent(uploads)
    journal.operation(operation, journal.request_digest)

    recovered = ImageImportJournal(Store(store.dsn), PROJECT, prep)
    attempt = recovered.history()
    assert attempt.source_reference == prep.journal_source_reference
    assert attempt.source_digest == prep.journal_source_digest
    assert attempt.request_digest == prep.request_digest()
    assert attempt.operation_id == operation
    assert attempt.state == "OPERATION_KNOWN"
    intent = attempt.events[0].payload["operation_metadata"]
    assert intent["base_image_uuid"] == str(prep.base_image)
    assert [item["sha256"] for item in intent["dependencies"]] == [
        wheel.sha256 for wheel in prep.wheels
    ]
    assert intent["preparation"]["networking"] == {"enabled": False}


def test_preparation_upload_receipts_recover_before_spawn(store: Store) -> None:
    prep = preparation_plan()
    project = PROJECT + "-upload-recovery"
    journal = ImageImportJournal(store, project, prep)
    assert journal.claim(journal.request_digest) is True
    uploaded = {}
    for wheel in prep.wheels:
        file_id = uuid4()
        journal.upload_intent(wheel.filename)
        journal.upload_complete(wheel.filename, file_id, wheel.sha256)
        uploaded[wheel.filename] = file_id
    journal.spawn_intent(uploaded)

    recovered = ImageImportJournal(Store(store.dsn), project, prep).history()
    kinds = [event.kind for event in recovered.events]
    assert kinds == [
        "INTENT",
        *[kind for _ in prep.wheels for kind in ("UPLOAD_INTENT", "UPLOAD_KNOWN")],
        "SPAWN_INTENT",
    ]
    known = [
        event.payload for event in recovered.events if event.kind == "UPLOAD_KNOWN"
    ]
    assert [item["filename"] for item in known] == [
        wheel.filename for wheel in prep.wheels
    ]
    assert [item["sha256"] for item in known] == [wheel.sha256 for wheel in prep.wheels]
    assert [item["file_uuid"] for item in known] == [
        str(uploaded[wheel.filename]) for wheel in prep.wheels
    ]
    assert (
        ImageImportJournal(Store(store.dsn), project, prep).claim(
            journal.request_digest
        )
        is False
    )


def test_upload_intent_without_result_cannot_be_repeated_after_restart(
    store: Store,
) -> None:
    prep = preparation_plan()
    project = PROJECT + "-upload-pending"
    journal = ImageImportJournal(store, project, prep)
    assert journal.claim(journal.request_digest) is True
    first = prep.wheels[0]
    journal.upload_intent(first.filename)

    recovered = ImageImportJournal(Store(store.dsn), project, prep)
    attempt = recovered.history()
    assert [event.kind for event in attempt.events] == ["INTENT", "UPLOAD_INTENT"]
    with pytest.raises(StoreError, match="IMAGE_IMPORT_UPLOAD_CONFLICT"):
        recovered.upload_intent(first.filename)
    with pytest.raises(StoreError, match="IMAGE_IMPORT_SPAWN_CONFLICT"):
        recovered.spawn_intent({})


def test_uncertain_upload_is_durable_and_never_reposted(store: Store) -> None:
    prep = preparation_plan()
    project = PROJECT + "-upload-uncertain"
    journal = ImageImportJournal(store, project, prep)
    assert journal.claim(journal.request_digest) is True
    journal.upload_intent(prep.wheels[0].filename)
    journal.diagnostic(diagnostic("SUBMISSION_UNKNOWN", journal.request_digest))

    recovered = ImageImportJournal(Store(store.dsn), project, prep)
    attempt = recovered.history()
    assert attempt.state == "SUBMISSION_UNKNOWN"
    assert [event.kind for event in attempt.events] == [
        "INTENT",
        "UPLOAD_INTENT",
        "DIAGNOSTIC",
    ]
    with pytest.raises(StoreError, match="IMAGE_IMPORT_UPLOAD_CONFLICT"):
        recovered.upload_intent(prep.wheels[0].filename)
    assert (
        ImageImportJournal(Store(store.dsn), project, prep).claim(
            journal.request_digest
        )
        is False
    )


def test_operation_and_terminal_result_recover_exact_binding(store: Store) -> None:
    original_plan = plan()
    journal = ImageImportJournal(store, PROJECT, original_plan)
    assert journal.claim(journal.request_digest) is True
    operation_id, image_id = uuid4(), uuid4()
    journal.operation(operation_id, journal.request_digest)

    restarted = ImageImportJournal(Store(store.dsn), PROJECT, original_plan)
    before_poll = restarted.history()
    assert before_poll.state == "OPERATION_KNOWN"
    assert before_poll.operation_id == operation_id
    restarted.complete(
        ImageImportResult(
            operation_id=operation_id,
            request_sha256=journal.request_digest,
            status="SUCCESS",
            image_uuid=image_id,
            observed_at="2026-10-26T10:00:01Z",
        )
    )
    recovered = ImageImportJournal(Store(store.dsn), PROJECT, original_plan).history()
    assert recovered.state == "TERMINAL"
    assert recovered.result_image_uuid == image_id
    assert recovered.result_status == "SUCCESS"
    assert [event.kind for event in recovered.events] == [
        "INTENT",
        "OPERATION_KNOWN",
        "TERMINAL",
    ]


def test_wrong_operation_and_wrong_result_binding_are_rejected(store: Store) -> None:
    original_plan = plan()
    journal = ImageImportJournal(store, PROJECT, original_plan)
    assert journal.claim(journal.request_digest) is True
    operation_id = uuid4()
    with pytest.raises(StoreError, match="IMAGE_IMPORT_ATTEMPT_REQUIRED"):
        ImageImportJournal(Store(store.dsn), PROJECT, original_plan).operation(
            operation_id, journal.request_digest
        )
    journal.operation(operation_id, journal.request_digest)
    with pytest.raises(StoreError, match="IMAGE_IMPORT_ATTEMPT_REQUIRED"):
        journal.complete(
            ImageImportResult(
                operation_id=operation_id,
                request_sha256="0" * 64,
                status="FAILED",
                observed_at="2026-10-26T10:00:02Z",
            )
        )


def test_event_corruption_is_detected_on_recovery(store: Store) -> None:
    original_plan = plan()
    journal = ImageImportJournal(store, PROJECT, original_plan)
    assert journal.claim(journal.request_digest) is True
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE image_import_events SET payload=%s "
            "WHERE attempt_id=%s AND sequence=1",
            (b"{}", journal.history().attempt_id),
        )
    with pytest.raises(StoreError, match="IMAGE_IMPORT_INTEGRITY"):
        ImageImportJournal(Store(store.dsn), PROJECT, original_plan).history()


def test_runtime_role_cannot_rewrite_import_identity_or_event_history(
    store: Store,
) -> None:
    journal = ImageImportJournal(store, PROJECT, plan())
    assert journal.claim(journal.request_digest) is True
    attempt_id = journal.history().attempt_id
    with psycopg.connect(store.dsn, autocommit=True) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(
                "UPDATE image_import_attempts SET source_digest=%s WHERE id=%s",
                ("0" * 64, attempt_id),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(
                "DELETE FROM image_import_events WHERE attempt_id=%s",
                (attempt_id,),
            )


@pytest.mark.anyio
async def test_importer_callbacks_recover_operation_and_result(store: Store) -> None:
    original_plan = plan()
    operation_id, image_id = uuid4(), uuid4()
    calls: list[str] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                201, json={"uuid": str(operation_id)}, request=request
            )
        return httpx.Response(
            200,
            json={
                "uuid": str(operation_id),
                "kind": "image_import",
                "status": "SUCCESS",
                "result": {"image": str(image_id)},
            },
            request=request,
        )

    config = ImageImportConfig(
        api_key="test-key-never-used-on-network", project_id=PROJECT
    )
    journal = ImageImportJournal(store, PROJECT, original_plan)
    async with OneShotNebiusImageImporter(
        config, transport=httpx.MockTransport(provider)
    ) as importer:
        accepted = await importer.submit(
            original_plan,
            journal=journal,
        )
    restarted = ImageImportJournal(Store(store.dsn), PROJECT, original_plan)
    assert restarted.history().operation_id == accepted.operation_id == operation_id

    async with OneShotNebiusImageImporter(
        config,
        transport=httpx.MockTransport(provider),
        known_operation=operation_id,
        known_request_hash=accepted.request_sha256,
    ) as recovered_importer:
        result = await recovered_importer.wait(
            operation_id,
            journal=restarted,
            max_polls=1,
            poll_interval_seconds=0,
        )
    restarted.complete(result)
    assert calls == ["POST", "GET"]
    assert (
        ImageImportJournal(Store(store.dsn), PROJECT, original_plan)
        .history()
        .result_image_uuid
        == image_id
    )


@pytest.mark.anyio
async def test_operator_controller_recovers_pending_operation_with_get_only(
    store: Store,
) -> None:
    original_plan = plan()
    operation_id, image_id = uuid4(), uuid4()
    first_calls: list[str] = []

    async def pending_provider(request: httpx.Request) -> httpx.Response:
        first_calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                201, json={"uuid": str(operation_id)}, request=request
            )
        return httpx.Response(
            200,
            json={
                "uuid": str(operation_id),
                "kind": "image_import",
                "status": "PENDING",
            },
            request=request,
        )

    config = ImageImportConfig(
        api_key="test-key-never-used-on-network", project_id=PROJECT
    )
    first = ImageImportController(
        store, config, transport=httpx.MockTransport(pending_provider)
    )
    pending = await first.run(
        original_plan,
        allow_live=True,
        max_polls=1,
        poll_interval_seconds=0,
    )
    assert pending.status == "PENDING"
    assert pending.operation_id == operation_id
    assert first_calls == ["POST", "GET"]

    recovery_calls: list[str] = []

    async def terminal_provider(request: httpx.Request) -> httpx.Response:
        recovery_calls.append(request.method)
        return httpx.Response(
            200,
            json={
                "uuid": str(operation_id),
                "kind": "image_import",
                "status": "SUCCESS",
                "result": {"image": str(image_id)},
            },
            request=request,
        )

    recovery = ImageImportController(
        Store(store.dsn), config, transport=httpx.MockTransport(terminal_provider)
    )
    succeeded = await recovery.run(
        original_plan,
        allow_live=True,
        max_polls=1,
        poll_interval_seconds=0,
    )
    assert succeeded.status == "SUCCESS"
    assert succeeded.image_uuid == image_id
    assert recovery_calls == ["GET"]
    cached = await recovery.run(original_plan, allow_live=True)
    assert cached.observed_at == succeeded.observed_at
    assert cached.image_uuid == image_id
    assert recovery_calls == ["GET"]


@pytest.mark.anyio
async def test_operator_controller_never_reposts_uncertain_attempt(
    store: Store,
) -> None:
    original_plan = plan()
    attempts = 0

    async def uncertain_provider(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("secret=not-recorded")

    config = ImageImportConfig(
        api_key="test-key-never-used-on-network", project_id=PROJECT
    )
    first = ImageImportController(
        store, config, transport=httpx.MockTransport(uncertain_provider)
    )
    report = await first.run(original_plan, allow_live=True)
    assert report.status == "SUBMISSION_UNKNOWN"
    assert report.diagnostic is not None
    assert attempts == 1

    second_requests: list[str] = []

    async def should_not_be_called(request: httpx.Request) -> httpx.Response:
        second_requests.append(request.method)
        return httpx.Response(500, request=request)

    second = ImageImportController(
        Store(store.dsn), config, transport=httpx.MockTransport(should_not_be_called)
    )
    replay = await second.run(original_plan, allow_live=True)
    assert replay.status == "SUBMISSION_UNKNOWN"
    assert replay.observed_at == report.observed_at
    assert replay.error_code == "RECONCILIATION_REQUIRED_NO_REPOST"
    assert second_requests == []
