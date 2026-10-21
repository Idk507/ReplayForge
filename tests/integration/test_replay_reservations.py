"""Real PostgreSQL pair accounting; tiny synthetic reports, no remote compute."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.contracts.run import Stage
from services.orchestrator.replay_delivery import receive_replays
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared

store = test_postgres.store


def test_pair_reservation_is_atomic_idempotent_and_restart_safe(store):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)

    def reserve(_):
        return ReplayJournal(Store(store.dsn)).reserve(
            lease, owner, actions, expected_version=run.version
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert results[0] == results[1] == reserve(None)
    assert results[0].version == run.version + 1
    assert (
        results[0].remaining_budget.sandbox_commands
        == run.remaining_budget.sandbox_commands - 2
    )
    assert (
        results[0].remaining_budget.execution_seconds
        == run.remaining_budget.execution_seconds - 120
    )
    assert ReplayJournal(Store(store.dsn)).load(run.run_id, owner) == actions


@pytest.mark.parametrize(
    "resource,value", [("sandbox_commands", 1), ("execution_seconds", 119)]
)
def test_budget_exhaustion_leaves_no_partial_intent_or_debit(store, resource, value):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)
    budget = run.remaining_budget.model_copy(update={resource: value})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (
                Jsonb(
                    run.model_copy(update={"remaining_budget": budget}).model_dump(
                        mode="json"
                    )
                ),
                run.run_id,
            ),
        )
    with pytest.raises(StoreError, match="BUDGET_EXHAUSTED"):
        ReplayJournal(store).reserve(
            lease, owner, actions, expected_version=run.version
        )
    closed = store.get(run.run_id, owner)
    assert closed.current_stage == Stage.INCONCLUSIVE
    assert closed.failure_reason == "BUDGET_EXHAUSTED"
    assert closed.remaining_budget == budget
    with pytest.raises(StoreError, match="REPLAY_RESERVATION_REQUIRED"):
        ReplayJournal(store).load(run.run_id, owner)


@pytest.mark.parametrize("fault", ["owner", "lease", "version", "cancel", "rollback"])
def test_invalid_reservation_cannot_create_intent(store, fault, monkeypatch):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)
    if fault == "owner":
        owner = uuid4()
    elif fault == "lease":
        lease = lease.model_copy(update={"token": uuid4()})
    elif fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=run.version)
    elif fault == "rollback":

        def fail(*args):
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")

        monkeypatch.setattr(Store, "_event", staticmethod(fail))
    with pytest.raises(StoreError):
        ReplayJournal(store).reserve(
            lease, owner, actions, expected_version=run.version + (fault == "version")
        )
    with psycopg.connect(store.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM replay_reservations WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()[0]
            == 0
        )


def test_delivery_requires_exact_reserved_pair(store):
    lease, owner, run, actions, candidate, clean, verifier = prepared(
        store, reserve=False
    )
    with pytest.raises(StoreError, match="REPLAY_RESERVATION_REQUIRED"):
        receive_replays(
            store,
            lease,
            owner,
            actions,
            candidate,
            clean,
            verifier,
            workspace_sha256="a" * 64,
            expected_version=run.version,
        )
    journal = ReplayJournal(store)
    journal.reserve(lease, owner, actions, expected_version=run.version)
    changed = actions.model_copy(update={"patched_tree_ref": "sha256:" + "9" * 64})
    with pytest.raises(StoreError, match="REPLAY_RESERVATION_CONFLICT"):
        journal.reserve(lease, owner, changed, expected_version=run.version)
    with pytest.raises(StoreError, match="REPLAY_RESERVATION_CONFLICT"):
        receive_replays(
            store,
            lease,
            owner,
            changed,
            candidate,
            clean,
            verifier,
            workspace_sha256="a" * 64,
            expected_version=run.version + 1,
        )


def test_exact_delivery_committing_between_checkpoint_and_fence_reconciles(
    store, monkeypatch
):
    lease, owner, run, actions, candidate, clean, verifier = prepared(store)
    original = Store.claimed_run
    injected = False

    def raced(self, claim):
        nonlocal injected
        if not injected:
            injected = True
            receive_replays(
                store,
                lease,
                owner,
                actions,
                candidate,
                clean,
                verifier,
                workspace_sha256="a" * 64,
                expected_version=run.version,
            )
        return original(self, claim)

    monkeypatch.setattr(Store, "claimed_run", raced)
    result = receive_replays(
        store,
        lease,
        owner,
        actions,
        candidate,
        clean,
        verifier,
        workspace_sha256="a" * 64,
        expected_version=run.version,
    )
    assert result.version == run.version + 1


@pytest.mark.parametrize("fault", ["owner", "expiry", "tamper", "update", "delete"])
def test_private_immutable_storage(store, fault):
    _, owner, run, _, *_ = prepared(store)
    if fault in {"update", "delete"}:
        with psycopg.connect(store.dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE replay_reservations SET digest=digest"
                    if fault == "update"
                    else "DELETE FROM replay_reservations"
                )
        return
    if fault != "owner":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "expiry":
                conn.execute(
                    "UPDATE replay_reservations SET expires_at=clock_timestamp()-"
                    "interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
            else:
                conn.execute(
                    "UPDATE replay_reservations SET payload=%s WHERE run_id=%s",
                    (b"damaged", run.run_id),
                )
    with pytest.raises(StoreError):
        ReplayJournal(store).load(run.run_id, uuid4() if fault == "owner" else owner)
