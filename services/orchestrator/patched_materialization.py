"""One-shot remote preparation of a retained patched candidate workspace."""

from collections.abc import Callable
from typing import Literal
from uuid import UUID

import httpx

from packages.contracts.sandbox import RunnerError
from services.persistence.patched_materialization import (
    PatchedMaterializationIntent,
    PatchedMaterializationJournal,
)
from services.persistence.store import Lease, Store, StoreError
from services.runner.candidate_workspace import CandidateWorkspace
from services.runner.materialization import (
    MaterializationPlan,
    MaterializationResult,
    NebiusMaterializer,
)
from services.runner.remote import SandboxConfig


def materialize_patched_once(
    store: Store,
    lease: Lease,
    owner: UUID,
    workspace: CandidateWorkspace,
    phase: Literal["CANDIDATE", "CLEAN_REPLAY"],
    config: SandboxConfig,
    authorize: Callable[[], None],
    *,
    expected_version: int,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> MaterializationResult | None:
    """Submit one fresh patched workspace or collect its known operation.

    The caller must have reconstructed ``workspace`` through ``prepare_candidate``
    from current trusted source/test/patch artifacts. This function rechecks its
    immutable plan, lease, consent and patch fence in the phase journal before
    every remote action. ``None`` is a nonterminal known provider operation.
    """
    if transport is None and allow_live is not True:
        raise RunnerError("S_LIVE_NOT_AUTHORIZED")
    workspace = CandidateWorkspace.model_validate(workspace.model_dump())
    if workspace.upload.plan.image_uuid != config.image_uuid:
        raise StoreError("PATCH_WORKSPACE_CONFIG_DENIED")
    authorize()
    intent = PatchedMaterializationIntent(
        phase=phase, plan=workspace.upload.plan, patch_ref=workspace.patch_ref
    )
    journal = PatchedMaterializationJournal(
        store,
        phase,
        origin="TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE",
    )
    fresh = journal.claim(lease, owner, intent, expected_version=expected_version)
    known = journal.operation(lease, owner, intent)
    saved = journal.result(lease, owner, intent)
    if saved is not None:
        return saved
    if not fresh and known is None:
        raise StoreError("PATCH_WORKSPACE_SUBMISSION_UNKNOWN")

    def current() -> None:
        authorize()
        journal.authorize(lease, owner, intent)

    with NebiusMaterializer(
        config, allow_live=allow_live, transport=transport, known_operation=known
    ) as backend:
        if fresh:

            def claim(_plan: MaterializationPlan) -> bool:
                current()
                return fresh

            def retain(operation_id: UUID) -> None:
                journal.operation(lease, owner, intent, operation_id)

            known = backend.submit(workspace.upload, claim, retain)
        if known is None:
            raise StoreError("PATCH_WORKSPACE_SUBMISSION_UNKNOWN")
        try:
            observed = backend.collect(workspace.upload.plan, known, current)
        except RunnerError as error:
            if str(error) == "S_MATERIALIZATION_PENDING":
                return None
            raise
        return journal.result(lease, owner, intent, observed)
