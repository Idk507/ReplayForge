"""Durable collection-to-triage handoff; synthetic inference only."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.contracts.run import Stage
from packages.contracts.source import SourceFile, SourceManifest
from packages.providers.nebius import NebiusConfig, ProbeError
from services.orchestrator.collection import CollectionInputs, run_collection
from services.orchestrator.worker import tick
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_postgres import collection_fixture
from tests.unit.test_model_dispatch import policy
from tests.unit.test_triage_gateway import response_for_any_context

store = test_postgres.store


def handoff(store):
    owner, state, lease, snapshot = collection_fixture(store)
    grant = store.repository(state.repository_id, owner)
    grant = grant.model_copy(update={"approved_paths": ("src/", "tests/")})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
            (Jsonb(grant.model_dump(mode="json")), grant.repository_id),
        )
    snapshot = replace(
        snapshot,
        commit_identity="GITHUB_HTTPS_METADATA",
        evidence_origin="TEST_TRANSPORT",
    )
    manifest = SourceManifest(
        repository_id=state.repository_id,
        source_commit=state.source_commit,
        root_tree="b" * 40,
        evidence_origin="TEST_TRANSPORT",
        observed_at=datetime.now(UTC),
        trace_id=state.trace_id,
        files=tuple(
            SourceFile(
                path=f.path, blob_id=f.blob_id, sha256=f.sha256, size_bytes=f.size_bytes
            )
            for f in snapshot.files
        ),
    )
    result = run_collection(
        store,
        owner,
        lease,
        queue_triage=True,
        input_loader=lambda *args: CollectionInputs(
            snapshot, ("src/a.py",), source_manifest=manifest
        ),
    )
    return owner, state, result


@pytest.mark.parametrize("failure", [False, True])
def test_durable_triage_worker_handoff_and_restart(store, failure):
    from services.orchestrator.worker import tick_triage

    owner, state, collected = handoff(store)
    assert collected.current_stage == Stage.TRIAGING
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT status,token,attempt_count FROM job_leases WHERE run_id=%s",
            (state.run_id,),
        ).fetchone() == ("queued", None, 0)
    assert (
        tick(
            store, uuid4(), lambda *args: pytest.fail("collection reclaimed triage")
        ).status
        == "IDLE"
    )
    calls = []
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})

    def respond(request):
        calls.append(request)
        return httpx.Response(503) if failure else response_for_any_context()

    transport = httpx.MockTransport(respond)
    if failure:
        with pytest.raises(ProbeError, match="M_UNAVAILABLE"):
            tick_triage(store, uuid4(), config, policy(config), transport=transport)
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "UPDATE job_leases SET leased_until=clock_timestamp()-"
                "interval '1 second' WHERE run_id=%s",
                (state.run_id,),
            )
        with pytest.raises(StoreError, match="TRIAGE_SUBMISSION_UNCERTAIN"):
            tick_triage(
                Store(store.dsn), uuid4(), config, policy(config), transport=transport
            )
    else:
        result = tick_triage(
            Store(store.dsn), uuid4(), config, policy(config), transport=transport
        )
        assert result.stage == Stage.REPRODUCING
        assert store.get(state.run_id, owner).reproduction_test_ref
        assert (
            tick_triage(
                store, uuid4(), config, policy(config), transport=transport
            ).status
            == "IDLE"
        )
        with psycopg.connect(store.dsn) as conn:
            assert conn.execute(
                "SELECT status,token FROM job_leases WHERE run_id=%s", (state.run_id,)
            ).fetchone() == ("done", None)
    assert len(calls) == 1


def test_triage_queue_handoff_rolls_back_with_context_audit(store, monkeypatch):
    owner, state, lease, snapshot = collection_fixture(store)
    # Reach collection before injecting failure, so only the handoff is tested.
    for stage in (Stage.VALIDATING, Stage.QUEUED, Stage.COLLECTING):
        current = store.get(state.run_id, owner)
        store.advance(lease, stage, expected_version=current.version)
    before = store.get(state.run_id, owner)

    def fail(*args):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(Store, "_event", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        run_collection(store, owner, lease, snapshot, ("src/a.py",), queue_triage=True)
    assert store.get(state.run_id, owner) == before
    assert store.claimed_run(lease)[0] == owner
