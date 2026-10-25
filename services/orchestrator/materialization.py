"""Fenced one-shot remote materialization and GET-only restart recovery."""

from collections.abc import Callable, Mapping
from uuid import UUID

import httpx

from packages.contracts.sandbox import RunnerError
from services.persistence.materialization import MaterializationJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.materialization import (
    MaterializationResult,
    NebiusMaterializer,
    prepare_upload,
)
from services.runner.remote import SandboxConfig


def materialize_once(
    store: Store,
    lease: Lease,
    owner: UUID,
    config: SandboxConfig,
    files: Mapping[str, bytes],
    authorize: Callable[[], None],
    *,
    expected_version: int,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> MaterializationResult | None:
    """One admitted tick: submit once, or collect an already-owned operation.

    authorize must reload current authenticated repository access through the
    trusted source broker; it is never model/browser JSON. DB fences and consent
    are checked separately. None means a known operation is still pending. No
    inference, test execution, runner signing or verification transition occurs.
    """
    if transport is None and allow_live is not True:
        raise RunnerError("S_LIVE_NOT_AUTHORIZED")
    authorize()
    run = store.get(lease.run_id, owner)
    if run.source_manifest_ref is None:
        raise StoreError("MATERIALIZATION_SOURCE_REQUIRED")
    manifest = store.source_manifest(run.run_id, owner, run.source_manifest_ref)
    reproduction = ReproductionJournal(store).get(run.run_id, owner)
    upload = prepare_upload(config.image_uuid, manifest, files, reproduction)
    journal = MaterializationJournal(
        store, origin="TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
    )
    fresh = journal.claim(lease, owner, upload.plan, expected_version=expected_version)
    known = journal.operation(lease, owner, upload.plan)
    saved = journal.result(lease, owner, upload.plan)
    if saved is not None:
        return saved
    if not fresh and known is None:
        raise StoreError("MATERIALIZATION_SUBMISSION_UNKNOWN")

    def current() -> None:
        authorize()
        journal.authorize(lease, owner, upload.plan)

    with NebiusMaterializer(
        config, allow_live=allow_live, transport=transport, known_operation=known
    ) as backend:
        if fresh:

            def claim() -> bool:
                current()
                return fresh

            def retain(operation_id: UUID) -> None:
                journal.operation(lease, owner, upload.plan, operation_id)

            known = backend.submit(
                upload,
                lambda p: claim(),
                retain,
            )
        if known is None:
            raise StoreError("MATERIALIZATION_SUBMISSION_UNKNOWN")
        try:
            result = backend.collect(upload.plan, known, current)
        except RunnerError as error:
            if str(error) == "S_MATERIALIZATION_PENDING":
                return None
            raise
        return journal.result(lease, owner, upload.plan, result)
