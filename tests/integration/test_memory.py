"""Real PostgreSQL memory/restart tests; all gate signatures are synthetic."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.contracts.run import RunState
from services.persistence.store import Store, StoreError
from services.persistence.verification import VerificationJournal
from tests.integration import test_postgres
from tests.integration.test_verification_journal import accept, prepared

store = test_postgres.store


def setup_memory(store, outcome="VERIFIED"):
    setup = prepared(store, outcome)
    accepted = accept(VerificationJournal(store), setup)
    owner = setup[1]
    current = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=accepted.repository_id,
        source_commit=accepted.source_commit,
        policy_version=accepted.policy_version,
        trace_id=uuid4(),
    )
    store.create(current, owner, uuid4(), "a" * 64)
    return owner, current, accepted


@pytest.mark.parametrize("outcome", ["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE"])
def test_memory_recall_survives_restart_without_reopening_run(store, outcome):
    from services.persistence.memory import RunMemory

    owner, current, previous = setup_memory(store, outcome)
    before = store.events(current.run_id, owner)
    recalled = RunMemory(Store(store.dsn)).recall(current.run_id, owner)
    assert recalled.status == "RECALLED"
    assert recalled.trace_id == current.trace_id
    assert len(recalled.lessons) == 1
    lesson = recalled.lessons[0]
    assert lesson.outcome == outcome and lesson.source_run_id == previous.run_id
    assert lesson.verification_ref == previous.verification_ref
    assert lesson.patch_ref == previous.patch_attempt_refs[-1]
    assert not recalled.can_authorize_execution
    assert not recalled.can_authorize_verification
    assert store.get(current.run_id, owner) == current
    assert store.get(previous.run_id, owner) == previous
    assert store.events(current.run_id, owner) == before


@pytest.mark.parametrize("fault", ["owner", "expired", "revoked", "policy", "corrupt"])
def test_memory_denies_or_excludes_unusable_history(store, fault):
    from services.persistence.memory import RunMemory

    owner, current, previous = setup_memory(store)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "expired":
            conn.execute(
                "UPDATE verification_results SET "
                "expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (previous.run_id,),
            )
        elif fault == "revoked":
            grant = store.repository(current.repository_id, owner)
            conn.execute(
                "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
                (
                    Jsonb(
                        grant.model_copy(update={"consent": False}).model_dump(
                            mode="json"
                        )
                    ),
                    current.repository_id,
                ),
            )
        elif fault == "policy":
            conn.execute(
                "UPDATE runs SET checkpoint=%s WHERE id=%s",
                (
                    Jsonb(
                        previous.model_copy(
                            update={"policy_version": "other"}
                        ).model_dump(mode="json")
                    ),
                    previous.run_id,
                ),
            )
        elif fault == "corrupt":
            conn.execute(
                "UPDATE verification_results SET payload=%s WHERE run_id=%s",
                (b"tampered", previous.run_id),
            )
    if fault in {"owner", "revoked", "corrupt"}:
        with pytest.raises(StoreError):
            RunMemory(store).recall(
                current.run_id, uuid4() if fault == "owner" else owner
            )
    else:
        assert RunMemory(store).recall(current.run_id, owner).lessons == ()


def test_memory_excludes_other_repository_and_current_run(store):
    from services.persistence.memory import RunMemory

    owner, current, previous = setup_memory(store)
    # Independently accepted history in another repository/owner must not leak.
    other_owner, other, _ = setup_memory(store)
    recalled = RunMemory(store).recall(current.run_id, owner)
    assert tuple(item.source_run_id for item in recalled.lessons) == (previous.run_id,)
    assert (
        RunMemory(store).recall(other.run_id, other_owner).repository_id
        == other.repository_id
    )
    # Reading an accepted run itself does not recall its own outcome.
    assert RunMemory(store).recall(previous.run_id, owner).lessons == ()


def test_memory_expires_with_underlying_bundle(store):
    from services.persistence.memory import RunMemory

    owner, current, previous = setup_memory(store)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE evidence_bundles SET "
            "expires_at=clock_timestamp()-interval '1 second' "
            "WHERE run_id=%s",
            (previous.run_id,),
        )
    assert RunMemory(store).recall(current.run_id, owner).status == "EMPTY"


def test_memory_is_included_in_exact_budgeted_triage_request(store):
    import hashlib
    import json

    import httpx

    from packages.contracts.context import ContextBundle
    from packages.contracts.run import Stage
    from packages.providers.nebius import NebiusConfig
    from services.orchestrator.repair_flow import run_triage
    from tests.unit.test_model_dispatch import policy
    from tests.unit.test_triage_gateway import response_for_any_context

    owner, current, previous = setup_memory(store)
    bundle = ContextBundle(
        source_commit=current.source_commit,
        sanitized_errors="synthetic duplicate charge",
    )
    body = Store._context_payload(bundle)
    digest = hashlib.sha256(body).hexdigest()
    run = current.model_copy(
        update={
            "current_stage": Stage.TRIAGING,
            "context_refs": ("sha256:" + digest,),
            "source_manifest_ref": previous.source_manifest_ref,
        }
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("UPDATE job_leases SET status='done'")
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
        conn.execute(
            "INSERT INTO context_artifacts(run_id,digest,payload) VALUES (%s,%s,%s)",
            (run.run_id, digest, body),
        )
        conn.execute(
            "UPDATE job_leases SET status='queued' WHERE run_id=%s", (run.run_id,)
        )
    lease = store.claim(uuid4(), triage_only=True)
    assert lease is not None
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        lessons = json.loads(context["advisory_memory"])
        assert lessons[0]["source_run_id"] == str(previous.run_id)
        assert lessons[0]["verification_ref"] == previous.verification_ref
        assert "historical outcomes only" in payload["messages"][0]["content"]
        with store._connection() as conn:
            intent = conn.execute(
                "SELECT dispatch_json FROM triage_intents WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
        assert (
            intent["dispatch_json"]["request"]["request_ref"]
            == "sha256:" + hashlib.sha256(request.content).hexdigest()
        )
        return response_for_any_context()

    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic-test-key"})
    completed = run_triage(
        store,
        lease,
        owner,
        config,
        cost_policy=policy(config),
        transport=httpx.MockTransport(handler),
    )
    assert completed.current_stage == Stage.REPRODUCING
    assert (
        run_triage(
            Store(store.dsn),
            lease,
            owner,
            config,
            transport=httpx.MockTransport(handler),
        )
        == completed
    )
    assert len(calls) == 1
