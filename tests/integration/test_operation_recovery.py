"""Real PostgreSQL recovery with synthetic HTTP only; never starts compute."""

import os
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql

from packages.contracts.sandbox import RunnerError
from services.persistence.operations import OperationJournal
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture
from tests.unit.test_sandbox_lifecycle import IMAGE, config

store = test_postgres.store


def prepared(store, *, known=True):
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
    operation_id = str(uuid4())
    if known:
        journal.record(
            lease,
            owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="OPERATION_KNOWN",
            expected_version=1,
            operation_id=operation_id,
        )
    return lease, owner, run, action, operation_id


@pytest.mark.parametrize(
    "status", ["PENDING", "ASSIGNED", "EXECUTING", "SUCCESS", "FAILED", "CANCELLED"]
)
def test_recover_known_operation_without_resubmission(store: Store, status) -> None:
    from services.orchestrator.operation_recovery import recover_baseline_operation

    lease, owner, run, action, operation_id = prepared(store)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/sandboxes/v1/operations/{operation_id}"
        return httpx.Response(
            200,
            json={
                "uuid": operation_id,
                "kind": "instance",
                "status": status,
                "image_uuid": IMAGE,
                "result_image_uuid": None,
                "metadata": {"stdout": "private-provider-output"},
            },
        )

    transport = httpx.MockTransport(respond)
    result = recover_baseline_operation(
        store,
        lease,
        owner,
        action.execution_id,
        config(),
        transport=transport,
    )
    terminal = status in {"SUCCESS", "FAILED", "CANCELLED"}
    assert result.status == ("COMPUTE_TERMINAL" if terminal else "PENDING")
    assert result.provider_status == status
    assert result.evidence_origin == "TEST_TRANSPORT"
    assert not result.can_authorize_execution
    assert "private-provider-output" not in result.model_dump_json()
    assert store.get(run.run_id, owner).version == 1
    assert store.get(run.run_id, owner).baseline_result_ref is None
    if terminal:
        resumed = recover_baseline_operation(
            Store(store.dsn),
            lease,
            owner,
            action.execution_id,
            config(),
            transport=transport,
        )
        assert resumed.status == "ALREADY_TERMINAL"
        assert resumed.provider_status is None  # Historical journal is not a fresh GET.
        assert len(requests) == 1
    else:
        assert len(OperationJournal(store).history(action.execution_id, owner)) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "owner",
        "lease",
        "wrong_uuid",
        "wrong_image",
        "redirect",
        "timeout",
        "auth",
        "oversize",
    ],
)
def test_recovery_faults_never_submit_or_mark_terminal(store: Store, fault) -> None:
    from services.orchestrator.operation_recovery import recover_baseline_operation

    lease, owner, _, action, operation_id = prepared(store, known=fault != "unknown")
    calls = []

    def respond(request):
        calls.append(request.method)
        assert request.method == "GET"
        if fault == "timeout":
            raise httpx.ReadTimeout("private-provider-output")
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.invalid"})
        if fault == "auth":
            return httpx.Response(403, text="private-provider-output")
        if fault == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        return httpx.Response(
            200,
            json={
                "uuid": str(uuid4()) if fault == "wrong_uuid" else operation_id,
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": str(uuid4()) if fault == "wrong_image" else IMAGE,
                "result_image_uuid": None,
            },
        )

    actual_owner = uuid4() if fault == "owner" else owner
    actual_lease = (
        lease.model_copy(update={"token": uuid4()}) if fault == "lease" else lease
    )
    with pytest.raises((RunnerError, StoreError)) as error:
        recover_baseline_operation(
            store,
            actual_lease,
            actual_owner,
            action.execution_id,
            config(),
            transport=httpx.MockTransport(respond),
        )
    assert "private-provider-output" not in str(error.value)
    assert len(OperationJournal(store).history(action.execution_id, owner)) == (
        1 if fault == "unknown" else 2
    )
    assert len(calls) == (0 if fault in {"unknown", "owner", "lease"} else 1)


@pytest.mark.parametrize("fault", ["lease_lost", "version_changed", "db_rollback"])
def test_recovery_fences_after_network_and_rolls_back_terminal_append(store, fault):
    from services.orchestrator.operation_recovery import recover_baseline_operation

    lease, owner, _, action, operation_id = prepared(store)

    def respond(request):
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "lease_lost":
                conn.execute(
                    "UPDATE job_leases SET status='done' WHERE run_id=%s",
                    (lease.run_id,),
                )
            elif fault == "version_changed":
                conn.execute(
                    "UPDATE runs SET checkpoint="
                    "jsonb_set(checkpoint,'{version}','2') WHERE id=%s",
                    (lease.run_id,),
                )
            else:
                conn.execute(
                    sql.SQL(
                        "REVOKE INSERT ON baseline_operation_records FROM {}"
                    ).format(sql.Identifier(os.environ["REPLAYFORGE_TEST_ROLE"]))
                )
        return httpx.Response(
            200,
            json={
                "uuid": operation_id,
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": IMAGE,
                "result_image_uuid": None,
            },
        )

    try:
        with pytest.raises(StoreError):
            recover_baseline_operation(
                store,
                lease,
                owner,
                action.execution_id,
                config(),
                transport=httpx.MockTransport(respond),
            )
        assert len(OperationJournal(store).history(action.execution_id, owner)) == 2
    finally:
        if fault == "db_rollback":
            with psycopg.connect(
                os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]
            ) as conn:
                conn.execute(
                    sql.SQL("GRANT INSERT ON baseline_operation_records TO {}").format(
                        sql.Identifier(os.environ["REPLAYFORGE_TEST_ROLE"])
                    )
                )


def test_recovery_denies_test_history_on_real_transport_before_network(store):
    from services.orchestrator.operation_recovery import recover_baseline_operation

    lease, owner, _, action, _ = prepared(store)
    with pytest.raises(StoreError, match="OPERATION_RECOVERY_DENIED"):
        recover_baseline_operation(store, lease, owner, action.execution_id, config())
