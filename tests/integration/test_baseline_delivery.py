"""Durable signed-report delivery with ephemeral synthetic signing authority."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.run import Stage
from services.persistence.baseline import BaselineJournal
from services.persistence.store import Store, StoreError
from services.runner.receipts import ReceiptDenied, ReceiptVerifier, canonical
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture

store = test_postgres.store


def prepared(store, *, invalid=False, test_origin=False):
    journal, lease, owner, run, action, signed, xml, _ = baseline_journal_fixture(store)
    key = Ed25519PrivateKey.generate()
    if invalid:
        xml = xml.replace(b"failure", b"error")
    origin = "TEST_TRANSPORT" if test_origin else "REMOTE_SERVICE"
    payload = signed.payload.model_copy(
        update={
            "origin": origin,
            "junit_ref": "sha256:" + hashlib.sha256(xml).hexdigest(),
            "exit_code": 2 if invalid else 1,
        }
    )
    signed = signed.model_copy(update={"payload": payload})
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    verifier = ReceiptVerifier({"test": key.public_key()}, origin=origin)
    journal.record_action(lease, owner, action, expected_version=0)
    journal.reserve_dispatch(lease, owner, action.execution_id, expected_version=0)
    return journal, lease, owner, run, action, signed, xml, verifier


@pytest.mark.parametrize("invalid", [False, True])
def test_signed_delivery_survives_restart_before_acceptance(store, invalid):
    from services.orchestrator.baseline_delivery import accept_retained_baseline

    journal, lease, owner, run, action, signed, xml, verifier = prepared(
        store, invalid=invalid
    )
    journal.retain(
        lease, owner, action.execution_id, signed, xml, verifier, expected_version=1
    )
    restarted = Store(store.dsn)
    assert BaselineJournal(restarted).report(action.execution_id, owner) == xml
    result = accept_retained_baseline(
        restarted, lease, owner, action.execution_id, verifier
    )
    assert result.current_stage == (Stage.INCONCLUSIVE if invalid else Stage.PATCHING)
    assert result.version == 2
    assert store.get(run.run_id, owner) == result
    assert (
        accept_retained_baseline(restarted, lease, owner, action.execution_id, verifier)
        == result
    )


@pytest.mark.parametrize(
    "fault", ["owner", "tamper", "legacy", "expiry", "test_origin", "signature"]
)
def test_report_delivery_denies_invalid_or_missing_evidence(store, fault):
    from services.orchestrator.baseline_delivery import accept_retained_baseline

    journal, lease, owner, run, action, signed, xml, verifier = prepared(
        store, test_origin=fault == "test_origin"
    )
    journal.retain(
        lease, owner, action.execution_id, signed, xml, verifier, expected_version=1
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "tamper":
            conn.execute(
                "UPDATE baseline_receipts SET report_payload=%s WHERE execution_id=%s",
                (b"tampered", action.execution_id),
            )
        elif fault == "legacy":
            conn.execute(
                "UPDATE baseline_receipts SET report_payload=NULL "
                "WHERE execution_id=%s",
                (action.execution_id,),
            )
        elif fault == "expiry":
            conn.execute(
                "UPDATE baseline_receipts SET expires_at=clock_timestamp()-"
                "interval '1 second' WHERE execution_id=%s",
                (action.execution_id,),
            )
    if fault == "signature":
        verifier = ReceiptVerifier(
            {"test": Ed25519PrivateKey.generate().public_key()}, origin="REMOTE_SERVICE"
        )
    with pytest.raises((StoreError, ReceiptDenied)):
        accept_retained_baseline(
            store,
            lease,
            uuid4() if fault == "owner" else owner,
            action.execution_id,
            verifier,
        )
    assert store.get(run.run_id, owner).current_stage == Stage.REPRODUCING


def test_signed_delivery_one_step_and_concurrent_reconciliation(store):
    from services.orchestrator.baseline_delivery import receive_baseline

    _, lease, owner, run, action, signed, xml, verifier = prepared(store)

    def deliver(_):
        return receive_baseline(
            store, lease, owner, action.execution_id, signed, xml, verifier
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, range(2)))
    assert results[0] == results[1]
    assert store.get(run.run_id, owner).version == 2
    assert BaselineJournal(store).report(action.execution_id, owner) == xml

    # A completed checkpoint must not cause conflicting redelivery to be ignored.
    with pytest.raises(StoreError, match="BASELINE_CONFLICT"):
        receive_baseline(
            store, lease, owner, action.execution_id, signed, b"changed", verifier
        )


def test_delivery_acceptance_audit_failure_keeps_recoverable_signed_report(
    store, monkeypatch
):
    from services.orchestrator.baseline_delivery import (
        accept_retained_baseline,
        receive_baseline,
    )

    _, lease, owner, run, action, signed, xml, verifier = prepared(store)
    original = Store._event

    def fail(conn, state, event_type):
        if state.current_stage == Stage.PATCHING:
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")
        return original(conn, state, event_type)

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_event", staticmethod(fail))
        with pytest.raises(StoreError, match="SYNTHETIC_AUDIT_FAILURE"):
            receive_baseline(
                store, lease, owner, action.execution_id, signed, xml, verifier
            )
    assert store.get(run.run_id, owner).current_stage == Stage.REPRODUCING
    assert BaselineJournal(Store(store.dsn)).report(action.execution_id, owner) == xml
    assert (
        accept_retained_baseline(
            Store(store.dsn), lease, owner, action.execution_id, verifier
        ).current_stage
        == Stage.PATCHING
    )


def test_acceptance_reconciles_completion_after_initial_checkpoint_read(
    store, monkeypatch
):
    from services.orchestrator.baseline_delivery import accept_retained_baseline

    journal, lease, owner, run, action, signed, xml, verifier = prepared(store)
    journal.retain(
        lease, owner, action.execution_id, signed, xml, verifier, expected_version=1
    )
    original_get = Store.get
    completed = []

    def competing_completion(self, run_id, requested_owner):
        before = original_get(self, run_id, requested_owner)
        if not completed and before.current_stage == Stage.REPRODUCING:
            completed.append(
                journal.accept(
                    lease, owner, action.execution_id, xml, verifier, expected_version=1
                )
            )
        return before  # Deliberately hand the receiver its now-stale snapshot.

    monkeypatch.setattr(Store, "get", competing_completion)
    result = accept_retained_baseline(
        store, lease, owner, action.execution_id, verifier
    )
    assert result == completed[0]
    assert original_get(store, run.run_id, owner).version == 2
