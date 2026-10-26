"""Current-consent source handoff into baseline and patched upload workers."""

from typing import Literal
from uuid import UUID

import httpx

from packages.contracts.run import Stage
from packages.contracts.source import SourceManifest
from services.orchestrator.patched_materialization import materialize_patched_once
from services.orchestrator.source_broker import BrokerClient
from services.persistence.patch import PatchJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.candidate_workspace import (
    CandidateWorkspace,
    prepare_candidate,
    workspace_for_phase,
)
from services.runner.materialization import MaterializationResult, prepare_upload
from services.runner.remote import SandboxConfig


def fetch_authorized_source(
    store: Store,
    broker: BrokerClient,
    lease: Lease,
    owner: UUID,
    *,
    expected_version: int,
) -> tuple[SourceManifest, dict[str, bytes]]:
    """Read the run's exact manifest subset through current auth-service access."""
    run = store.get(lease.run_id, owner)
    if run.version != expected_version or run.source_manifest_ref is None:
        raise StoreError("MATERIALIZATION_SOURCE_BINDING_DENIED")
    manifest = store.source_manifest(run.run_id, owner, run.source_manifest_ref)
    files = broker.fetch_materialization_source(lease, expected_version, manifest)
    return manifest, files


def materialize_baseline_from_broker(
    store: Store,
    broker: BrokerClient,
    lease: Lease,
    owner: UUID,
    config: SandboxConfig,
    *,
    expected_version: int,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> MaterializationResult | None:
    """Fetch pinned files through auth, then commit and upload baseline source."""
    from services.orchestrator.materialization import materialize_once

    manifest, files = fetch_authorized_source(
        store, broker, lease, owner, expected_version=expected_version
    )
    reproduction = ReproductionJournal(store).get(lease.run_id, owner)
    # Validate the same exact archive before crossing the provider boundary.
    prepare_upload(config.image_uuid, manifest, files, reproduction)

    def authorize() -> None:
        current = store.get(lease.run_id, owner)
        broker.authorize_materialization_source(lease, current.version)

    return materialize_once(
        store,
        lease,
        owner,
        config,
        files,
        authorize,
        expected_version=expected_version,
        allow_live=allow_live,
        transport=transport,
    )


def materialize_candidate_from_broker(
    store: Store,
    broker: BrokerClient,
    lease: Lease,
    owner: UUID,
    config: SandboxConfig,
    phase: Literal["CANDIDATE", "CLEAN_REPLAY"],
    *,
    expected_version: int,
    allow_live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> MaterializationResult | None:
    """Rebuild exact candidate bytes from broker-fetched source and retained patch."""
    run = store.get(lease.run_id, owner)
    if run.version != expected_version or run.current_stage != Stage.VERIFYING:
        raise StoreError("PATCH_WORKSPACE_BINDING_DENIED")
    manifest, files = fetch_authorized_source(
        store, broker, lease, owner, expected_version=expected_version
    )
    grant = store.repository(run.repository_id, owner)
    reproduction = ReproductionJournal(store).get(run.run_id, owner)
    patch = PatchJournal(store).get(run.run_id, owner)
    workspace: CandidateWorkspace = prepare_candidate(
        config.image_uuid,
        manifest,
        files,
        reproduction,
        patch=patch,
        run=run,
        grant=grant,
        owner=owner,
    )
    workspace = workspace_for_phase(workspace, phase)

    def authorize() -> None:
        current = store.get(lease.run_id, owner)
        broker.authorize_materialization_source(lease, current.version)

    return materialize_patched_once(
        store,
        lease,
        owner,
        workspace,
        phase,
        config,
        authorize,
        expected_version=expected_version,
        allow_live=allow_live,
        transport=transport,
    )
