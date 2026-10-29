"""Baseline producer preparation with real fences and synthetic identities."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.persistence.operations import OperationJournal
from services.persistence.store import StoreError
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import ProducedReceipt
from services.runner.receipts import (
    ExecutionReceipt,
    SignedReceipt,
    canonical,
    plan_ref,
)
from services.runner.workspace import WorkspaceMeasurement
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture

store = test_postgres.store


def setup(store, *, origin="TEST_TRANSPORT"):
    from services.persistence.baseline_producer import BaselineProducerJournal

    journal, lease, owner, _, action, *_ = baseline_journal_fixture(store)
    plan = action.plan.model_copy(
        update={
            "command_id": "PYTEST_FILES_MEASURED_V1",
            "argv": (action.plan.argv[0], "-B", *action.plan.argv[1:]),
        }
    )
    action = action.model_copy(update={"plan": plan})
    journal.record_action(lease, owner, action, expected_version=0)
    version = journal.reserve_dispatch(
        lease, owner, action.execution_id, expected_version=0
    )
    binding = PreparedImageBinding(
        plan_ref=plan_ref(plan),
        execution_id=action.execution_id,
        tree_ref=action.source_tree_ref,
        image_uuid=config().image_uuid,
        image_digest=plan.request.image_digest,
        runner_id=uuid4(),
        configuration_ref="sha256:" + "c" * 64,
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO baseline_submissions "
            "(execution_id,plan_ref,capability_ref,runner_id,configuration_ref,"
            "origin,expires_at) VALUES (%s,%s,%s,%s,%s,%s,"
            "clock_timestamp()+interval '1 hour')",
            (
                action.execution_id,
                binding.plan_ref,
                "sha256:" + "a" * 64,
                binding.runner_id,
                binding.configuration_ref,
                origin,
            ),
        )
    operations = OperationJournal(store)
    operations.record(
        lease,
        owner,
        action.execution_id,
        provider="NEBIUS_CONTREE" if origin == "REMOTE_SERVICE" else "TEST_TRANSPORT",
        phase="SUBMISSION_UNKNOWN",
        expected_version=version,
    )
    key = Ed25519PrivateKey.generate()
    producer = BaselineProducerJournal(
        store,
        lease,
        owner,
        binding,
        receipt_key_id="test",
        receipt_public_key=key.public_key(),
        origin=origin,
        expected_version=version,
    )
    producer._test_key = key
    measurement = WorkspaceMeasurement(
        image_uuid=binding.image_uuid,
        tree_ref=binding.tree_ref,
        files=1,
        bytes_measured=4,
        evidence_origin=origin,
    )
    return producer, operations, lease, owner, action, version, measurement


def test_preparation_is_sticky_and_restores_only_known_operation(store):
    producer, ops, lease, owner, action, version, measurement = setup(store)
    producer.guard_submission()
    producer.retain_preparation(measurement)
    with pytest.raises(StoreError, match="PRODUCER_ALREADY_PREPARED"):
        producer.guard_submission()
    operation = uuid4()
    with pytest.raises(StoreError, match="PRODUCER_KNOWN_OPERATION_REQUIRED"):
        producer.restore_preparation(operation)
    ops.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        expected_version=version,
        operation_id=operation,
    )
    restored = producer.restore_preparation(operation)
    assert restored.source == measurement
    with pytest.raises(StoreError, match="PRODUCER_KNOWN_OPERATION_REQUIRED"):
        producer.restore_preparation(uuid4())


def test_wrong_measurement_never_becomes_preparation(store):
    producer, _, _, _, _, _, measurement = setup(store)
    with pytest.raises(StoreError, match="PRODUCER_PREPARATION_DENIED"):
        producer.retain_preparation(
            measurement.model_copy(update={"image_uuid": uuid4()})
        )
    producer.guard_submission()


def test_signed_baseline_receipt_is_retained_and_verified_on_recovery(store):
    producer, ops, lease, owner, action, version, measurement = setup(store)
    producer.retain_preparation(measurement)
    operation = uuid4()
    ops.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        expected_version=version,
        operation_id=operation,
    )
    report = b"<testsuite/>"
    final = measurement.model_copy(update={"image_uuid": uuid4()})
    payload = ExecutionReceipt(
        execution_id=action.execution_id,
        run_id=lease.run_id,
        plan_ref=plan_ref(action.plan),
        source_tree_ref=measurement.tree_ref,
        final_tree_ref=final.tree_ref,
        junit_ref="sha256:" + sha256(report).hexdigest(),
        origin="TEST_TRANSPORT",
        observed_at=datetime.now(UTC),
        exit_code=1,
        terminal=True,
        timed_out=False,
    )
    unsigned = SignedReceipt(key_id="test", payload=payload, signature="0" * 128)
    signed = unsigned.model_copy(
        update={
            "signature": producer._test_key.sign(
                canonical(unsigned, unsigned=True)
            ).hex()
        }
    )
    result = ProducedReceipt(
        operation_id=operation,
        source=measurement,
        final=final,
        signed=signed,
        junit=report,
    )
    with pytest.raises(StoreError, match="PRODUCER_RECEIPT_SIGNATURE"):
        producer.retain_receipt(
            result.model_copy(
                update={"signed": signed.model_copy(update={"signature": "0" * 128})}
            )
        )
    with pytest.raises(StoreError, match="PRODUCER_RECEIPT_DENIED"):
        producer.retain_receipt(result.model_copy(update={"operation_id": uuid4()}))
    assert producer.retain_receipt(result) == result


@pytest.mark.parametrize("invalid", [False, True])
def test_produced_delivery_recovers_before_and_after_acceptance(
    store, invalid, monkeypatch
):
    """Synthetic remote-shaped receipts exercise the actual durable handoff."""
    from packages.contracts.run import Stage
    from services.orchestrator import produced_baseline_delivery as delivery
    from services.runner.receipts import ReceiptVerifier
    from tests.unit.test_baseline_inspection import XML

    producer, ops, lease, owner, action, version, measurement = setup(
        store, origin="REMOTE_SERVICE"
    )
    producer.retain_preparation(measurement)
    operation = uuid4()
    ops.record(
        lease,
        owner,
        action.execution_id,
        provider="NEBIUS_CONTREE",
        phase="OPERATION_KNOWN",
        expected_version=version,
        operation_id=operation,
    )
    xml = XML.replace(b"failure", b"error") if invalid else XML
    payload = ExecutionReceipt(
        execution_id=action.execution_id,
        run_id=lease.run_id,
        plan_ref=plan_ref(action.plan),
        source_tree_ref=measurement.tree_ref,
        final_tree_ref=measurement.tree_ref,
        junit_ref="sha256:" + sha256(xml).hexdigest(),
        origin="REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        exit_code=2 if invalid else 1,
        terminal=True,
        timed_out=False,
    )
    unsigned = SignedReceipt(key_id="test", payload=payload, signature="0" * 128)
    signed = unsigned.model_copy(
        update={
            "signature": producer._test_key.sign(
                canonical(unsigned, unsigned=True)
            ).hex()
        }
    )
    producer.retain_receipt(
        ProducedReceipt(
            operation_id=operation,
            source=measurement,
            final=measurement.model_copy(update={"image_uuid": uuid4()}),
            signed=signed,
            junit=xml,
        )
    )
    verifier = ReceiptVerifier(
        {"test": producer._test_key.public_key()}, origin="REMOTE_SERVICE"
    )
    original = delivery.receive_baseline

    def crash_after_acceptance(*args, **kwargs):
        original(*args, **kwargs)
        raise StoreError("SIMULATED_CRASH")

    with monkeypatch.context() as patch:
        patch.setattr(delivery, "receive_baseline", crash_after_acceptance)
        with pytest.raises(StoreError, match="SIMULATED_CRASH"):
            delivery.deliver_produced_baseline(
                store, lease, owner, producer, verifier, queue_patch=True
            )
    recovered = delivery.deliver_produced_baseline(
        store, lease, owner, producer, verifier, queue_patch=True
    )
    assert recovered.current_stage == (
        Stage.INCONCLUSIVE if invalid else Stage.PATCHING
    )
    assert (
        delivery.deliver_produced_baseline(
            store, lease, owner, producer, verifier, queue_patch=True
        )
        == recovered
    )


def test_confined_backend_restores_cached_baseline_without_provider_io(store):
    import httpx

    from services.runner.admission import CapabilityVerifier
    from services.runner.confined import DurableConfinedBackend
    from services.runner.confinement import confinement_configuration_ref
    from tests.unit.test_runner_admission import fixture as capabilities

    producer, ops, lease, owner, action, version, measurement = setup(store)
    _, profile, _, sign, verifier = capabilities("REMOTE_SERVICE")
    reference = confinement_configuration_ref(
        action.plan, producer.binding.image_uuid, action.execution_id
    )
    producer.binding = producer.binding.model_copy(
        update={
            "runner_id": profile.runner_id,
            "configuration_ref": reference,
        }
    )
    profile = profile.model_copy(update={"configuration_ref": reference})
    verifier = CapabilityVerifier(
        verifier._keys,
        runner_id=profile.runner_id,
        backend="NEBIUS_CONTREE",
        origin="REMOTE_SERVICE",
        configuration_ref=reference,
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE baseline_submissions SET runner_id=%s,configuration_ref=%s "
            "WHERE execution_id=%s",
            (profile.runner_id, reference, action.execution_id),
        )
    producer.retain_preparation(measurement)
    operation = uuid4()
    ops.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        expected_version=version,
        operation_id=operation,
    )
    report = b"<testsuite/>"
    final = measurement.model_copy(update={"image_uuid": uuid4()})
    payload = ExecutionReceipt(
        execution_id=action.execution_id,
        run_id=lease.run_id,
        plan_ref=plan_ref(action.plan),
        source_tree_ref=measurement.tree_ref,
        final_tree_ref=final.tree_ref,
        junit_ref="sha256:" + sha256(report).hexdigest(),
        origin="TEST_TRANSPORT",
        observed_at=datetime.now(UTC),
        exit_code=1,
        terminal=True,
        timed_out=False,
    )
    unsigned = SignedReceipt(key_id="test", payload=payload, signature="0" * 128)
    signed = unsigned.model_copy(
        update={
            "signature": producer._test_key.sign(
                canonical(unsigned, unsigned=True)
            ).hex()
        }
    )
    result = ProducedReceipt(
        operation_id=operation,
        source=measurement,
        final=final,
        signed=signed,
        junit=report,
    )
    producer.retain_receipt(result)

    def forbidden(_):
        pytest.fail("cached recovery must not contact provider")

    with DurableConfinedBackend(
        config(),
        producer.binding,
        sign(profile),
        verifier,
        receipt_key=producer._test_key,
        receipt_key_id="test",
        journal=producer,
        allow_privileged_setup=True,
        transport=httpx.MockTransport(forbidden),
    ) as backend:
        assert backend.collect_receipt(operation) == result
    assert producer.restore(operation)[2] == result
    assert producer.retain_receipt(result) == result


def test_concurrent_preparation_retains_only_one_record(store):
    producer, _, _, _, _, _, measurement = setup(store)

    def retain(_):
        try:
            producer.retain_preparation(measurement)
            return "retained"
        except StoreError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(retain, range(2))) == [
            "PRODUCER_ALREADY_PREPARED",
            "retained",
        ]


@pytest.mark.parametrize("fault", ["lease", "expired", "tamper", "scope", "intent"])
def test_recovery_checks_current_fences_and_integrity(store, fault):
    producer, ops, lease, owner, action, version, measurement = setup(store)
    producer.retain_preparation(measurement)
    operation = uuid4()
    ops.record(
        lease,
        owner,
        action.execution_id,
        provider="TEST_TRANSPORT",
        phase="OPERATION_KNOWN",
        expected_version=version,
        operation_id=operation,
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "lease":
            conn.execute(
                "UPDATE job_leases SET leased_until=clock_timestamp()"
                "-interval '1 second' "
                "WHERE run_id=%s",
                (lease.run_id,),
            )
        elif fault == "expired":
            conn.execute(
                "UPDATE baseline_preparations SET expires_at=clock_timestamp()"
                "-interval '1 second' "
                "WHERE execution_id=%s",
                (action.execution_id,),
            )
        elif fault == "tamper":
            conn.execute(
                "UPDATE baseline_preparations SET payload=%s WHERE execution_id=%s",
                (b"tampered", action.execution_id),
            )
        elif fault == "scope":
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false') "
                "WHERE id=%s",
                (action.plan.request.repository_id,),
            )
        else:
            conn.execute(
                "UPDATE baseline_dispatch_intents SET action_digest=%s "
                "WHERE execution_id=%s",
                ("f" * 64, action.execution_id),
            )
    with pytest.raises(StoreError):
        producer.restore_preparation(operation)
