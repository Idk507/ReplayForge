"""Real durable stage handoff; tiny inert source and synthetic inference."""

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
from services.orchestrator.collection import CollectionInputs
from services.orchestrator.harness import serve_harness
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_postgres import collection_fixture
from tests.unit.test_model_dispatch import policy
from tests.unit.test_triage_gateway import response_for_any_context
from tests.unit.test_worker_polling import Stop

store = test_postgres.store


@pytest.mark.parametrize("failed_provider", [False, True])
def test_agent_harness_collects_triages_and_recovers_without_duplicate_request(
    store, failed_provider
):
    owner, state, lease, snapshot = collection_fixture(store)
    grant = store.repository(state.repository_id, owner)
    grant = grant.model_copy(update={"approved_paths": ("src/", "tests/")})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
            (Jsonb(grant.model_dump(mode="json")), grant.repository_id),
        )
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL WHERE run_id=%s",
            (state.run_id,),
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
    reads = []

    def load(*args):
        reads.append(1)
        return CollectionInputs(snapshot, ("src/a.py",), source_manifest=manifest)

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503) if failed_provider else response_for_any_context()

    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-test-key"})

    def execute():
        return serve_harness(
            Store(store.dsn),
            uuid4(),
            load,
            Stop(),
            model_config=config,
            cost_policy=policy(config),
            transport=httpx.MockTransport(respond),
            max_steps=4,
        )

    if failed_provider:
        with pytest.raises(ProbeError, match="M_UNAVAILABLE"):
            execute()
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "UPDATE job_leases SET "
                "leased_until=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (state.run_id,),
            )
        with pytest.raises(StoreError, match="TRIAGE_SUBMISSION_UNCERTAIN"):
            execute()
    else:
        result = execute()
        assert result.collected == result.triaged == 1
        assert result.processed == 2 and result.steps == 4
        completed = store.get(state.run_id, owner)
        assert completed.current_stage == Stage.REPRODUCING
        assert completed.reproduction_test_ref
        restarted = serve_harness(
            Store(store.dsn),
            uuid4(),
            load,
            Stop(),
            model_config=config,
            cost_policy=policy(config),
            transport=httpx.MockTransport(respond),
            max_steps=2,
        )
        assert restarted.processed == 0
        assert store.get(state.run_id, owner) == completed
    assert len(reads) == len(calls) == 1
