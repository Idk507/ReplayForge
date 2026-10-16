"""Real local PostgreSQL lifecycle recovery; no provider requests or execution."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from services.persistence.operations import OperationJournal
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture

store = test_postgres.store


def test_operation_journal_restart_and_concurrent_replay(store: Store) -> None:
    baseline, lease, owner, run, action, _, _, _ = baseline_journal_fixture(store)
    baseline.record_action(lease, owner, action, expected_version=0)
    baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    journal = OperationJournal(store)

    def unknown():
        return journal.record(
            lease,
            owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="SUBMISSION_UNKNOWN",
            expected_version=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: unknown(), range(2)))
    assert records[0] == records[1]
    assert OperationJournal(Store(store.dsn)).history(action.execution_id, owner) == (
        records[0],
    )
    operation_id = uuid4()
    known = journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        operation_id=operation_id,
        expected_version=1,
    )
    terminal = journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="COMPUTE_TERMINAL",
        operation_id=operation_id,
        expected_version=1,
    )
    assert journal.history(action.execution_id, owner) == (records[0], known, terminal)
    assert unknown() == records[0]  # Historical assertion, never resubmission.
    updated = store.get(run.run_id, owner)
    assert updated.version == 1
    assert (
        updated.remaining_budget.sandbox_commands
        == run.remaining_budget.sandbox_commands - 1
    )
    assert updated.baseline_result_ref is None
    with pytest.raises(StoreError, match="NOT_FOUND"):
        journal.history(action.execution_id, uuid4())
    with pytest.raises(StoreError, match="OPERATION_CONFLICT"):
        journal.record(
            lease,
            owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="OPERATION_KNOWN",
            operation_id=uuid4(),
            expected_version=1,
        )


@pytest.mark.parametrize(
    "fault",
    ["no_reservation", "order", "owner", "stale", "expired", "cancel", "completed"],
)
def test_operation_journal_denies_unsafe_start(store: Store, fault: str) -> None:
    baseline, lease, owner, run, action, signed, xml, verifier = (
        baseline_journal_fixture(store)
    )
    baseline.record_action(lease, owner, action, expected_version=0)
    if fault != "no_reservation":
        baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    if fault == "completed":
        baseline.retain(
            lease, owner, action.execution_id, signed, xml, verifier, expected_version=1
        )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "expired":
            conn.execute(
                "UPDATE job_leases SET leased_until="
                "clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
        if fault == "cancel":
            state = store.get(run.run_id, owner).model_dump(mode="json")
            state["cancellation_requested"] = True
            conn.execute(
                "UPDATE runs SET checkpoint=%s WHERE id=%s", (Jsonb(state), run.run_id)
            )
    with pytest.raises(StoreError):
        OperationJournal(store).record(
            lease,
            uuid4() if fault == "owner" else owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="OPERATION_KNOWN" if fault == "order" else "SUBMISSION_UNKNOWN",
            operation_id=uuid4() if fault == "order" else None,
            expected_version=0 if fault in {"no_reservation", "stale"} else 1,
        )


def test_operation_journal_cleanup_metadata_after_cancellation(store: Store) -> None:
    baseline, lease, owner, run, action, _, _, _ = baseline_journal_fixture(store)
    baseline.record_action(lease, owner, action, expected_version=0)
    baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    journal = OperationJournal(store)
    journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="SUBMISSION_UNKNOWN",
        expected_version=1,
    )
    state = store.get(run.run_id, owner).model_dump(mode="json")
    state["cancellation_requested"] = True
    state["remaining_budget"]["sandbox_commands"] = 0
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s", (Jsonb(state), run.run_id)
        )
    operation_id = uuid4()
    journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        operation_id=operation_id,
        expected_version=1,
    )
    terminal = journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="COMPUTE_TERMINAL",
        operation_id=operation_id,
        expected_version=1,
    )
    assert terminal.can_authorize_execution is False
    assert store.get(run.run_id, owner).model_dump(mode="json") == state


def test_operation_journal_provider_identity_unique(store: Store) -> None:
    operation_id = uuid4()
    for i in range(2):
        baseline, lease, owner, _, action, _, _, _ = baseline_journal_fixture(store)
        baseline.record_action(lease, owner, action, expected_version=0)
        baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
        journal = OperationJournal(store)
        journal.record(
            lease,
            owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="SUBMISSION_UNKNOWN",
            expected_version=1,
        )
        if i == 0:
            journal.record(
                lease,
                owner,
                action.execution_id,
                provider="TEST_TRANSPORT",
                phase="OPERATION_KNOWN",
                operation_id=operation_id,
                expected_version=1,
            )
        else:
            with pytest.raises(StoreError, match="DB_FAILURE_23505"):
                journal.record(
                    lease,
                    owner,
                    action.execution_id,
                    provider="TEST_TRANSPORT",
                    phase="OPERATION_KNOWN",
                    operation_id=operation_id,
                    expected_version=1,
                )
            assert len(journal.history(action.execution_id, owner)) == 1


def test_operation_journal_write_failure_and_permissions(store: Store) -> None:
    baseline, lease, owner, run, action, _, _, _ = baseline_journal_fixture(store)
    baseline.record_action(lease, owner, action, expected_version=0)
    baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    before = store.get(run.run_id, owner)
    journal = OperationJournal(store)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "ALTER TABLE baseline_operation_records ADD CONSTRAINT reject_operation "
            "CHECK(false) NOT VALID"
        )
    try:
        with pytest.raises(StoreError, match="DB_FAILURE_23514"):
            journal.record(
                lease,
                owner,
                action.execution_id,
                provider="TEST_TRANSPORT",
                phase="SUBMISSION_UNKNOWN",
                expected_version=1,
            )
        assert journal.history(action.execution_id, owner) == ()
        assert store.get(run.run_id, owner) == before
    finally:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                "ALTER TABLE baseline_operation_records "
                "DROP CONSTRAINT reject_operation"
            )
    with psycopg.connect(store.dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "DELETE FROM baseline_operation_records WHERE execution_id=%s",
                (action.execution_id,),
            )


def test_operation_journal_records_last_reserved_command(store: Store) -> None:
    baseline, lease, owner, run, action, _, _, _ = baseline_journal_fixture(store)
    baseline.record_action(lease, owner, action, expected_version=0)
    baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    state = store.get(run.run_id, owner).model_dump(mode="json")
    state["remaining_budget"]["sandbox_commands"] = 0
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s", (Jsonb(state), run.run_id)
        )
    record = OperationJournal(store).record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="SUBMISSION_UNKNOWN",
        expected_version=1,
    )
    assert record.can_authorize_execution is False
    assert store.get(run.run_id, owner).model_dump(mode="json") == state


@pytest.mark.parametrize("fault", ["expiry", "hash", "column"])
def test_operation_journal_read_integrity(store: Store, fault: str) -> None:
    baseline, lease, owner, _, action, _, _, _ = baseline_journal_fixture(store)
    baseline.record_action(lease, owner, action, expected_version=0)
    baseline.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    journal = OperationJournal(store)
    journal.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="SUBMISSION_UNKNOWN",
        expected_version=1,
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "expiry":
            conn.execute(
                "UPDATE baseline_actions SET expires_at=clock_timestamp() "
                "WHERE execution_id=%s",
                (action.execution_id,),
            )
        elif fault == "hash":
            conn.execute(
                "UPDATE baseline_operation_records SET digest=%s WHERE execution_id=%s",
                ("0" * 64, action.execution_id),
            )
        else:
            conn.execute(
                "UPDATE baseline_operation_records SET provider='NEBIUS_CONTREE' "
                "WHERE execution_id=%s",
                (action.execution_id,),
            )
    with pytest.raises(StoreError):
        journal.history(action.execution_id, owner)
