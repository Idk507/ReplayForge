"""Production gate loader denials using disposable DB and synthetic identities."""

import json
import os
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.orchestrator.production_verification import (
    PHASES,
    PhaseTrust,
    ProductionGateProducer,
)
from services.persistence.store import StoreError
from services.verifier.authenticated import GateVerifier
from services.verifier.signing import ProductionGateSigner
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.integration.test_produced_delivery import inputs
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


def test_deployment_trust_starts_worker_with_disposable_database(store):
    """The deployment trust builder composes the actual worker startup types."""
    from psycopg import sql

    from services.orchestrator.deployment_trust import build_verification_trust
    from services.orchestrator.verification_worker import ProductionVerificationWorker
    from tests.unit.test_deployment_trust import _fixture

    config_data, _, backend, now = _fixture()
    trust = build_verification_trust(
        json.dumps(config_data, separators=(",", ":")).encode(),
        backend,
        now=now,
    )
    role = os.environ["REPLAYFORGE_TEST_ROLE"]
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            sql.SQL("GRANT SELECT ON alembic_version TO {}").format(
                sql.Identifier(role)
            )
        )
        # A production worker needs only the columns its durable claim and
        # finalization paths update. Keep the disposable acceptance role at
        # that same least-privilege boundary instead of granting table UPDATE.
        conn.execute(
            sql.SQL("REVOKE UPDATE ON TABLE runs, job_leases FROM {}").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL("GRANT UPDATE (checkpoint, version) ON TABLE runs TO {}").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL(
                "GRANT UPDATE (status, worker_id, token, leased_until, attempt_count) "
                "ON TABLE job_leases TO {}"
            ).format(sql.Identifier(role))
        )
    from psycopg.conninfo import conninfo_to_dict

    database = conninfo_to_dict(os.environ["REPLAYFORGE_TEST_DATABASE_URL"])["dbname"]
    try:
        worker = ProductionVerificationWorker.from_env(
            trust,
            {
                "REPLAYFORGE_DATABASE_URL": os.environ["REPLAYFORGE_TEST_DATABASE_URL"],
                "REPLAYFORGE_DATABASE_NAME": database,
                "REPLAYFORGE_VERIFICATION_WORKER_ROLE": role,
                "REPLAYFORGE_SCHEMA_REVISION": "0036",
            },
            test_only=True,
        )
        with psycopg.connect(os.environ["REPLAYFORGE_TEST_DATABASE_URL"]) as conn:
            assert not conn.execute(
                "SELECT has_table_privilege(current_user,'runs','UPDATE')"
            ).fetchone()[0]
            assert not conn.execute(
                "SELECT has_table_privilege(current_user,'job_leases','UPDATE')"
            ).fetchone()[0]
    finally:
        # Restore the harness defaults before the other cases in this module.
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                sql.SQL("GRANT UPDATE ON TABLE runs, job_leases TO {}").format(
                    sql.Identifier(role)
                )
            )
    assert worker.producer.signer.key_id == "gate-202610"
    assert set(worker.producer.phase_trust) == set(PHASES)


def _producer(
    store,
    receipt_verifier,
    *,
    origin="REMOTE_SERVICE",
    capability_verifier=None,
    runner_id=None,
    configuration_ref=None,
    image_uuids=None,
    omit_phase=None,
    signing_profile="v4",
):
    _, profile, _, _, admitted = capability_fixture(origin)
    capability_verifier = capability_verifier or admitted
    runner_id = runner_id or profile.runner_id
    configuration_ref = configuration_ref or profile.configuration_ref
    image = config().image_uuid
    digest = "sha256:" + "d" * 64
    phases = {
        phase: PhaseTrust(
            image_uuid=(image_uuids or {}).get(phase, image),
            image_digest=digest,
            configuration_ref=configuration_ref,
            runner_id=runner_id,
            receipt_verifier=receipt_verifier,
            capability_verifier=capability_verifier,
        )
        for phase in PHASES
        if phase != omit_phase
    }
    if signing_profile == "v5":
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        from services.verifier.azure_es256 import (
            AzureES256GateSignerV5,
            AzureES256GateVerifierV5,
            AzureES256KeyVaultBackend,
            AzureES256TrustKey,
        )

        private = ec.generate_private_key(ec.SECP256R1())
        key_id = "synthetic-gate-v5"
        key_version = f"https://fixture.vault.azure.net/keys/gate/{uuid4().hex}"

        class TestAzureSigner:
            def sign(self, algorithm, digest):
                assert algorithm == "ES256"
                der = private.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
                r, s = utils.decode_dss_signature(der)
                return type(
                    "Result",
                    (),
                    {
                        "key_id": key_version,
                        "algorithm": "ES256",
                        "signature": r.to_bytes(32, "big") + s.to_bytes(32, "big"),
                    },
                )()

        backend = AzureES256KeyVaultBackend(
            key_id=key_id,
            key_version=key_version,
            public_key=private.public_key(),
            client=TestAzureSigner(),
        )
        signer = AzureES256GateSignerV5(backend)
        verifier = AzureES256GateVerifierV5(
            {
                key_id: AzureES256TrustKey(
                    key_version=key_version, public_key=private.public_key()
                )
            }
        )
    else:
        private = Ed25519PrivateKey.generate()
        signer = ProductionGateSigner("synthetic-gate-test", private.sign)
        verifier = GateVerifier({"synthetic-gate-test": private.public_key()})
    return ProductionGateProducer(store, phases, signer, verifier)


def test_loader_rejects_unowned_run_before_reading_phase_rows(store):
    lease, owner, _, journals, receipt_verifier = inputs(store, include_suite=True)
    producer = _producer(store, receipt_verifier)
    with pytest.raises(StoreError, match="NOT_FOUND"):
        producer.artifacts(lease, uuid4())
    assert journals


def test_loader_requires_durable_baseline_preparation_and_assessor(store):
    lease, owner, _, _, receipt_verifier = inputs(store, include_suite=True)
    producer = _producer(store, receipt_verifier)
    with pytest.raises(StoreError, match="GATE_BASELINE_EVIDENCE_MISSING"):
        producer.artifacts(lease, owner)


def test_production_loader_rejects_test_transport_trust(store):
    _, _, _, _, receipt_verifier = inputs(store, include_suite=True)
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        _producer(store, receipt_verifier, origin="TEST_TRANSPORT")


def test_production_loader_rejects_incomplete_phase_trust(store):
    _, _, _, _, receipt_verifier = inputs(store, include_suite=True)
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        _producer(store, receipt_verifier, omit_phase="CLEAN_REPLAY")


def test_production_loader_rejects_missing_or_mismatched_signer_trust(store):
    _, _, _, _, receipt_verifier = inputs(store, include_suite=True)
    trusted = _producer(store, receipt_verifier)
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        ProductionGateProducer(store, trusted.phase_trust, None, trusted.gate_verifier)
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        ProductionGateProducer(
            store,
            trusted.phase_trust,
            ProductionGateSigner("untrusted-signer-id", lambda _body: b"x" * 64),
            trusted.gate_verifier,
        )


def test_production_gate_rejects_v4_v5_profile_confusion(store):
    """Versioned signer/verifier pairs cannot be crossed at composition time."""
    _, _, _, _, receipt_verifier = inputs(store, include_suite=True)
    v4 = _producer(store, receipt_verifier)
    v5 = _producer(store, receipt_verifier, signing_profile="v5")
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        ProductionGateProducer(store, v4.phase_trust, v4.signer, v5.gate_verifier)
    with pytest.raises(StoreError, match="GATE_TRUST_CONFIGURATION_INVALID"):
        ProductionGateProducer(store, v5.phase_trust, v5.signer, v4.gate_verifier)


def test_loader_rejects_stale_worker_fence(store):
    lease, owner, _, _, receipt_verifier = inputs(store, include_suite=True)
    producer = _producer(store, receipt_verifier)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        producer.artifacts(lease.model_copy(update={"token": uuid4()}), owner)


def test_loader_rejects_revoked_repository_consent(store):
    lease, owner, run, _, receipt_verifier = inputs(store, include_suite=True)
    producer = _producer(store, receipt_verifier)
    with psycopg.connect(
        os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"], row_factory=dict_row
    ) as conn:
        row = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        assert row is not None
        grant = {**row["grant_json"], "consent": False}
        conn.execute(
            "UPDATE repository_grants SET grant_json=%s WHERE id=%s AND owner_id=%s",
            (Jsonb(grant), run.repository_id, owner),
        )
    with pytest.raises(StoreError, match="GATE_CONSENT_DENIED"):
        producer.artifacts(lease, owner)


@pytest.mark.parametrize(
    "adversarial_case",
    (
        "positive",
        "positive_v5",
        "missing_phase",
        "swapped_provider_operation",
        "wrong_source_commit",
        "altered_source_manifest",
        "patch_lineage_tamper",
        "missing_suite_receipt",
        "modified_suite_receipt",
        "tampered_evidence_bundle",
        "wrong_runtime_binding",
        "wrong_image_uuid",
        "wrong_runtime_configuration",
        "wrong_workspace_digest",
        "revoked_consent",
        "expired_assessor",
        "untrusted_collector",
        "modified_regression_test",
        "lease_conflict",
        "budget_violation",
    ),
)
def test_production_verification_finalizes_complete_four_phase_run(
    store, record_property, adversarial_case
):
    """Positive acceptance; fixture records are synthetic and test-only."""
    # Start at the existing source collection factory and persist through the
    # production source, reproduction and baseline journals. No fixture code is
    # executed; the signed JUnit is controlled synthetic data.
    import hashlib
    from datetime import UTC, datetime, timedelta

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from psycopg.types.json import Jsonb

    from packages.contracts.context import ContextBundle
    from packages.contracts.execution import TestCommandRequest
    from packages.contracts.repair import PatchProposal, ReproductionProposal
    from packages.contracts.run import Stage
    from packages.contracts.source import SourceFile, SourceManifest
    from services.orchestrator.baseline_delivery import receive_baseline
    from services.orchestrator.baseline_dispatch import dispatch_baseline
    from services.orchestrator.produced_delivery import deliver_produced_replays
    from services.orchestrator.replay_dispatch import dispatch_replay
    from services.persistence.baseline import BaselineAction, BaselineJournal
    from services.persistence.baseline_producer import BaselineProducerJournal
    from services.persistence.materialization import MaterializationJournal
    from services.persistence.operations import OperationJournal
    from services.persistence.patch import PatchJournal
    from services.persistence.patched_materialization import (
        PatchedMaterializationIntent,
        PatchedMaterializationJournal,
    )
    from services.persistence.producer import ProducerJournal
    from services.persistence.replay import ReplayJournal
    from services.persistence.reproduction import ReproductionJournal
    from services.runner.baseline import FailureExpectation
    from services.runner.candidate_workspace import prepare_candidate
    from services.runner.commands import plan_pytest
    from services.runner.materialization import MaterializationResult, prepare_upload
    from services.runner.prepared import PreparedImageBinding
    from services.runner.producer import ProducedReceipt
    from services.runner.receipts import (
        ExecutionReceipt,
        ReceiptVerifier,
        SignedReceipt,
        SignedReplayReceipt,
        canonical,
        plan_ref,
    )
    from services.runner.workspace import WorkspaceMeasurement
    from services.verifier.replay import ReplayActions
    from tests.contract.test_sandbox_smoke import config
    from tests.integration.test_postgres import collection_fixture
    from tests.unit.test_baseline_inspection import XML
    from tests.unit.test_runner_admission import fixture as capability_fixture

    owner, state, lease, _ = collection_fixture(store)
    grant_row = psycopg.connect(
        os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"], row_factory=dict_row
    )
    with grant_row as conn:
        row = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s",
            (state.repository_id,),
        ).fetchone()
        assert row is not None
        grant_data = {**row["grant_json"], "approved_paths": ["src/", "tests/"]}
        conn.execute(
            "UPDATE repository_grants SET grant_json=%s WHERE id=%s",
            (Jsonb(grant_data), state.repository_id),
        )
    from packages.contracts.repository import RepositoryGrant

    grant = RepositoryGrant.model_validate(grant_data)
    src, test = (
        b"def payment():\n    return True\n",
        b"def test_payment():\n    assert True\n",
    )
    manifest = SourceManifest(
        repository_id=state.repository_id,
        source_commit=state.source_commit,
        root_tree="b" * 40,
        evidence_origin="REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
        files=(
            SourceFile(
                path="src/payments.py",
                blob_id=hashlib.sha1(
                    f"blob {len(src)}\0".encode() + src, usedforsecurity=False
                ).hexdigest(),
                sha256=hashlib.sha256(src).hexdigest(),
                size_bytes=len(src),
            ),
            SourceFile(
                path="tests/test_payments.py",
                blob_id=hashlib.sha1(
                    f"blob {len(test)}\0".encode() + test, usedforsecurity=False
                ).hexdigest(),
                sha256=hashlib.sha256(test).hexdigest(),
                size_bytes=len(test),
            ),
        ),
    )
    bundle = ContextBundle(
        source_commit=state.source_commit,
        commit_identity="GITHUB_HTTPS_METADATA",
        evidence_origin="REMOTE_SERVICE",
        sanitized_errors="synthetic checkout incident",
    )
    store.advance(lease, Stage.VALIDATING, expected_version=0)
    store.advance(lease, Stage.QUEUED, expected_version=1)
    store.advance(lease, Stage.COLLECTING, expected_version=2)
    run = store.store_context(
        lease, bundle, expected_version=3, source_manifest=manifest, queue_triage=True
    )
    lease = store.claim(uuid4(), triage_only=True, run_id=run.run_id)
    assert lease is not None
    reproduction = ReproductionProposal(
        target_path="tests/test_duplicate_repro.py",
        behavior="Duplicate payment is rejected",
        failure_signature="RF_DUPLICATE_TRANSACTION",
        test_source="def test_duplicate():\n    assert 'RF_DUPLICATE_TRANSACTION'\n",
    )
    run = ReproductionJournal(store).retain(
        lease,
        owner,
        reproduction,
        expected_version=4,
        finish_job=True,
        queue_baseline=True,
    )
    lease = store.claim(uuid4(), stages=(Stage.REPRODUCING,), run_id=run.run_id)
    assert lease is not None
    run = store.get(run.run_id, owner)
    repro_artifact = ReproductionJournal(store).get(run.run_id, owner)
    upload = prepare_upload(
        config().image_uuid,
        manifest,
        {"src/payments.py": src, "tests/test_payments.py": test},
        repro_artifact,
    )
    mat = MaterializationJournal(store, origin="REMOTE_SERVICE")
    assert mat.claim(lease, owner, upload.plan, expected_version=run.version)
    source_operation = uuid4()
    mat.operation(lease, owner, upload.plan, source_operation)
    source_measurement = WorkspaceMeasurement(
        image_uuid=config().image_uuid,
        tree_ref=upload.plan.workspace_ref,
        files=3,
        bytes_measured=len(src) + len(test) + len(reproduction.test_source),
        evidence_origin="REMOTE_SERVICE",
    )
    source_result = MaterializationResult(
        plan=upload.plan,
        operation_id=source_operation,
        result_image_uuid=config().image_uuid,
        measurement=source_measurement,
        observed_at=datetime.now(UTC),
    )
    mat.result(lease, owner, upload.plan, source_result)
    run = store.get(run.run_id, owner)
    _, profile, _, cap_sign, cap_verifier = capability_fixture("REMOTE_SERVICE")
    request = TestCommandRequest(
        run_id=run.run_id,
        repository_id=run.repository_id,
        source_commit=run.source_commit,
        source_manifest_ref=run.source_manifest_ref,
        reproduction_test_ref=run.reproduction_test_ref,
        image_digest="sha256:" + "d" * 64,
        phase="BASELINE",
        reproduction_path=reproduction.target_path,
        test_paths=(reproduction.target_path,),
    )
    plan = plan_pytest(request, run, grant, owner)
    plan = plan.model_copy(
        update={
            "command_id": "PYTEST_FILES_MEASURED_V1",
            "argv": (plan.argv[0], "-B", *plan.argv[1:]),
        }
    )
    execution = uuid4()
    action = BaselineAction(
        execution_id=execution,
        plan=plan,
        source_tree_ref=upload.plan.workspace_ref,
        expected=FailureExpectation(
            test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
        ),
    )
    BaselineJournal(store).record_action(
        lease, owner, action, expected_version=run.version
    )
    base_private = Ed25519PrivateKey.generate()
    binding = PreparedImageBinding(
        plan_ref=plan_ref(plan),
        execution_id=execution,
        tree_ref=upload.plan.workspace_ref,
        image_uuid=config().image_uuid,
        image_digest=plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    base_journal = BaselineProducerJournal(
        store,
        lease,
        owner,
        binding,
        receipt_key_id="synthetic-collector",
        receipt_public_key=base_private.public_key(),
        origin="REMOTE_SERVICE",
        expected_version=run.version + 1,
    )

    class BaseBackend:
        origin = "REMOTE_SERVICE"
        runner_id = profile.runner_id
        configuration_ref = profile.configuration_ref

        def submit(self, _plan, _execution, _tree):
            base_journal.retain_preparation(source_measurement)
            return base_operation

    base_operation = uuid4()
    dispatch_baseline(
        store,
        lease,
        owner,
        execution,
        cap_sign(),
        cap_verifier,
        BaseBackend(),
        expected_version=run.version,
        allow_live=True,
    )
    reserved = store.get(run.run_id, owner)
    OperationJournal(store).record(
        lease,
        owner,
        execution,
        provider="NEBIUS_CONTREE",
        phase="COMPUTE_TERMINAL",
        expected_version=reserved.version,
        operation_id=base_operation,
    )
    fail_xml = XML.replace(b"tests.test_regression", b"tests.test_duplicate_repro")
    fail_payload = ExecutionReceipt(
        execution_id=execution,
        run_id=run.run_id,
        plan_ref=plan_ref(plan),
        source_tree_ref=source_measurement.tree_ref,
        final_tree_ref=source_measurement.tree_ref,
        junit_ref="sha256:" + hashlib.sha256(fail_xml).hexdigest(),
        origin="REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        exit_code=1,
        terminal=True,
        timed_out=False,
    )
    fail_unsigned = SignedReceipt(
        key_id="synthetic-collector", payload=fail_payload, signature="0" * 128
    )
    fail_signed = fail_unsigned.model_copy(
        update={
            "signature": base_private.sign(
                canonical(fail_unsigned, unsigned=True)
            ).hex()
        }
    )
    base_journal.retain_receipt(
        ProducedReceipt(
            operation_id=base_operation,
            source=source_measurement,
            final=source_measurement.model_copy(update={"image_uuid": uuid4()}),
            signed=fail_signed,
            junit=fail_xml,
        )
    )
    receipt_verifier = ReceiptVerifier(
        {"synthetic-collector": base_private.public_key()}, origin="REMOTE_SERVICE"
    )
    run = receive_baseline(
        store, lease, owner, execution, fail_signed, fail_xml, receipt_verifier
    )
    assert run.current_stage == Stage.PATCHING
    patch_diff = (
        "diff --git a/src/payments.py b/src/payments.py\n"
        "--- a/src/payments.py\n+++ b/src/payments.py\n"
        "@@ -1,2 +1,2 @@\n def payment():\n-    return True\n+    return False\n"
    )
    proposal = PatchProposal(
        unified_diff=patch_diff,
        files_changed=("src/payments.py",),
        justification="synthetic duplicate-payment regression repair",
        expected_test_delta="regression passes",
        risk_notes="synthetic fixture only",
    )
    _, patch_version = PatchJournal(store).reserve(
        lease, owner, expected_version=run.version
    )
    run = PatchJournal(store).retain(
        lease,
        owner,
        proposal,
        expected_version=patch_version,
        queue_verification=True,
    )
    lease = store.claim(uuid4(), stages=(Stage.VERIFYING,), run_id=run.run_id)
    assert lease is not None
    run = store.get(run.run_id, owner)
    retained_reproduction = ReproductionJournal(store).get(run.run_id, owner)
    from services.persistence.patch import PatchJournal as StoredPatch

    retained_patch = StoredPatch(store).get(run.run_id, owner)
    patched_workspace = prepare_candidate(
        config().image_uuid,
        manifest,
        {"src/payments.py": src, "tests/test_payments.py": test},
        retained_reproduction,
        patch=retained_patch,
        run=run,
        grant=grant,
        owner=owner,
    )
    patched_results = {}
    for phase in ("CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"):
        intent = PatchedMaterializationIntent(
            phase=phase,
            plan=patched_workspace.upload.plan,
            patch_ref=run.patch_attempt_refs[-1],
        )
        journal = PatchedMaterializationJournal(store, phase, origin="REMOTE_SERVICE")
        latest = store.get(run.run_id, owner)
        assert journal.claim(lease, owner, intent, expected_version=latest.version)
        operation = uuid4()
        journal.operation(lease, owner, intent, operation)
        image = uuid4()
        measured = WorkspaceMeasurement(
            image_uuid=image,
            tree_ref=patched_workspace.upload.plan.workspace_ref,
            files=3,
            bytes_measured=99,
            evidence_origin="REMOTE_SERVICE",
        )
        materialized = MaterializationResult(
            plan=patched_workspace.upload.plan,
            operation_id=operation,
            result_image_uuid=image,
            measurement=measured,
            observed_at=datetime.now(UTC),
        )
        journal.result(lease, owner, intent, materialized)
        patched_results[phase] = (materialized, operation)

    run = store.get(run.run_id, owner)
    digest = "sha256:" + "d" * 64

    def measured_plan(phase, test_paths):
        request = TestCommandRequest(
            run_id=run.run_id,
            repository_id=run.repository_id,
            source_commit=run.source_commit,
            source_manifest_ref=run.source_manifest_ref,
            reproduction_test_ref=run.reproduction_test_ref,
            patch_ref=run.patch_attempt_refs[-1],
            image_digest=digest,
            phase=phase,
            reproduction_path=reproduction.target_path,
            test_paths=test_paths,
        )
        plan_value = plan_pytest(request, run, grant, owner)
        return plan_value.model_copy(
            update={
                "command_id": "PYTEST_FILES_MEASURED_V1",
                "argv": (plan_value.argv[0], "-B", *plan_value.argv[1:]),
            }
        )

    candidate_plan = measured_plan("CANDIDATE", (reproduction.target_path,))
    suite_plan = measured_plan(
        "EXISTING_SUITE",
        tuple(sorted(("tests/test_payments.py", reproduction.target_path))),
    )
    clean_plan = measured_plan("CLEAN_REPLAY", (reproduction.target_path,))
    actions = ReplayActions(
        candidate_plan=candidate_plan,
        suite_plan=suite_plan,
        clean_plan=clean_plan,
        baseline_execution_id=execution,
        candidate_execution_id=uuid4(),
        suite_execution_id=uuid4(),
        clean_execution_id=uuid4(),
        patched_tree_ref=patched_workspace.upload.plan.workspace_ref,
        test_name="test_duplicate",
    )
    run = ReplayJournal(store).reserve(
        lease, owner, actions, expected_version=run.version
    )
    # One synthetic collector identity is explicitly shared by the baseline
    # and replay receipts, matching the producer's injected verifier.
    collector = base_private
    replay_journals = {}
    junit_by_phase = {
        "CANDIDATE": (
            b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase classname="tests.test_duplicate_repro" name="test_duplicate"/>'
            b"</testsuite></testsuites>"
        ),
        "EXISTING_SUITE": (
            b'<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
            b'<testcase classname="tests.test_duplicate_repro" name="test_duplicate"/>'
            b'<testcase classname="tests.test_payments" name="test_payment"/>'
            b"</testsuite></testsuites>"
        ),
        "CLEAN_REPLAY": (
            b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase classname="tests.test_duplicate_repro" name="test_duplicate"/>'
            b"</testsuite></testsuites>"
        ),
    }
    plan_by_phase = {
        "CANDIDATE": candidate_plan,
        "EXISTING_SUITE": suite_plan,
        "CLEAN_REPLAY": clean_plan,
    }
    execution_by_phase = {
        "CANDIDATE": actions.candidate_execution_id,
        "EXISTING_SUITE": actions.suite_execution_id,
        "CLEAN_REPLAY": actions.clean_execution_id,
    }
    replay_operation_ids = {}
    for phase in ("CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"):
        plan_value = plan_by_phase[phase]
        execution_value = execution_by_phase[phase]
        image_measurement = patched_results[phase][0].measurement
        binding_value = PreparedImageBinding(
            plan_ref=plan_ref(plan_value),
            execution_id=execution_value,
            tree_ref=actions.patched_tree_ref,
            image_uuid=image_measurement.image_uuid,
            image_digest=digest,
            runner_id=profile.runner_id,
            configuration_ref=profile.configuration_ref,
        )
        journal = ProducerJournal(
            store,
            lease,
            owner,
            phase,
            binding_value,
            receipt_key_id="synthetic-collector",
            receipt_public_key=collector.public_key(),
            origin="REMOTE_SERVICE",
            expected_version=run.version,
        )

        operation_id = uuid4()
        replay_operation_ids[phase] = operation_id

        class ReplayBackend:
            origin = "REMOTE_SERVICE"
            runner_id = profile.runner_id
            configuration_ref = profile.configuration_ref

            def __init__(self, retained_journal, measurement, known_operation):
                self.journal = retained_journal
                self.measurement = measurement
                self.known_operation = known_operation

            def submit(self, _plan, _execution, _tree):
                self.journal.retain_preparation(self.measurement)
                return self.known_operation

        dispatch_replay(
            store,
            lease,
            owner,
            phase,
            cap_sign(),
            cap_verifier,
            ReplayBackend(journal, image_measurement, operation_id),
            expected_version=run.version,
            allow_live=True,
        )
        junit = junit_by_phase[phase]
        final_measurement = image_measurement.model_copy(update={"image_uuid": uuid4()})
        payload = ExecutionReceipt(
            execution_id=execution_value,
            run_id=run.run_id,
            plan_ref=plan_ref(plan_value),
            source_tree_ref=image_measurement.tree_ref,
            final_tree_ref=final_measurement.tree_ref,
            junit_ref="sha256:" + hashlib.sha256(junit).hexdigest(),
            origin="REMOTE_SERVICE",
            observed_at=datetime.now(UTC),
            exit_code=0,
            terminal=True,
            timed_out=False,
        )
        unsigned = SignedReplayReceipt(
            key_id="synthetic-collector", payload=payload, signature="0" * 128
        )
        signed = unsigned.model_copy(
            update={
                "signature": collector.sign(canonical(unsigned, unsigned=True)).hex()
            }
        )
        journal.retain_receipt(
            ProducedReceipt(
                operation_id=operation_id,
                source=image_measurement,
                final=final_measurement,
                signed=signed,
                junit=junit,
            )
        )
        replay_journals[phase] = journal

    producer = _producer(
        store,
        ReceiptVerifier(
            {"synthetic-collector": collector.public_key()}, origin="REMOTE_SERVICE"
        ),
        capability_verifier=cap_verifier,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
        image_uuids={
            "BASELINE": config().image_uuid,
            **{
                phase: result[0].result_image_uuid
                for phase, result in patched_results.items()
            },
        },
        signing_profile="v5" if adversarial_case == "positive_v5" else "v4",
    )
    retained = deliver_produced_replays(
        store,
        lease,
        owner,
        replay_journals["CANDIDATE"],
        replay_journals["CLEAN_REPLAY"],
        ReceiptVerifier(
            {"synthetic-collector": collector.public_key()}, origin="REMOTE_SERVICE"
        ),
        workspace_sha256=actions.patched_tree_ref[7:],
        expected_version=run.version,
        suite=replay_journals["EXISTING_SUITE"],
        production=producer,
        defer_finalization=True,
    )
    assert retained.current_stage == Stage.VERIFYING
    from psycopg.conninfo import conninfo_to_dict
    from pydantic import SecretStr

    from services.orchestrator.verification_worker import (
        ProductionVerificationWorker,
        VerificationTrust,
        VerificationWorkerConfig,
        VerificationWorkerError,
    )

    trust = VerificationTrust(
        phase_trust=producer.phase_trust,
        signer=producer.signer,
        gate_verifier=producer.gate_verifier,
    )
    test_dsn = os.environ["REPLAYFORGE_TEST_DATABASE_URL"]
    test_info = conninfo_to_dict(test_dsn)
    worker_config = VerificationWorkerConfig(
        dsn=SecretStr(test_dsn),
        expected_database=test_info["dbname"],
        expected_revision="0036",
        required_role=os.environ["REPLAYFORGE_TEST_ROLE"],
    )
    # Schema-head read is part of worker startup's database contract.
    from psycopg import sql

    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            sql.SQL("GRANT SELECT ON alembic_version TO {}").format(
                sql.Identifier(os.environ["REPLAYFORGE_TEST_ROLE"])
            )
        )
    with pytest.raises(VerificationWorkerError, match="VERIFICATION_TEST_TRUST_DENIED"):
        ProductionVerificationWorker(store, trust, worker_config)
    # Simulate process death after the evidence bundle commit but before signing.
    # The worker acquires a fresh fence; it never resubmits or polls provider work.
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if adversarial_case == "missing_phase":
            conn.execute(
                "DELETE FROM replay_produced_receipts "
                "WHERE run_id=%s AND phase='CLEAN_REPLAY'",
                (run.run_id,),
            )
        elif adversarial_case == "swapped_provider_operation":
            candidate_op = replay_operation_ids["CANDIDATE"]
            clean_op = replay_operation_ids["CLEAN_REPLAY"]
            temporary = uuid4()
            conn.execute(
                "UPDATE replay_submission_results SET operation_id=%s "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (temporary, run.run_id),
            )
            conn.execute(
                "UPDATE replay_submission_results SET operation_id=%s "
                "WHERE run_id=%s AND phase='CLEAN_REPLAY'",
                (candidate_op, run.run_id),
            )
            conn.execute(
                "UPDATE replay_submission_results SET operation_id=%s "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (clean_op, run.run_id),
            )
        elif adversarial_case == "wrong_source_commit":
            conn.execute(
                "UPDATE runs SET checkpoint=checkpoint || jsonb_build_object("
                "'source_commit',%s::text) "
                "WHERE id=%s",
                ("c" * 40, run.run_id),
            )
            changed = conn.execute(
                "SELECT checkpoint->>'source_commit' FROM runs WHERE id=%s",
                (run.run_id,),
            ).fetchone()
            assert changed is not None and changed[0] == "c" * 40
        elif adversarial_case == "altered_source_manifest":
            conn.execute(
                "UPDATE source_manifests SET payload=payload || decode('20','hex') "
                "WHERE run_id=%s",
                (run.run_id,),
            )
        elif adversarial_case == "patch_lineage_tamper":
            conn.execute(
                "UPDATE patch_candidates SET payload=payload || decode('20','hex') "
                "WHERE run_id=%s",
                (run.run_id,),
            )
        elif adversarial_case == "missing_suite_receipt":
            conn.execute(
                "DELETE FROM replay_produced_receipts "
                "WHERE run_id=%s AND phase='EXISTING_SUITE'",
                (run.run_id,),
            )
        elif adversarial_case == "tampered_evidence_bundle":
            conn.execute(
                "UPDATE evidence_bundles SET payload=payload || decode('20','hex') "
                "WHERE run_id=%s",
                (run.run_id,),
            )
        elif adversarial_case == "wrong_runtime_binding":
            conn.execute(
                "UPDATE replay_preparations SET payload=payload || decode('20','hex') "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (run.run_id,),
            )
        elif adversarial_case == "revoked_consent":
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false'::jsonb) "
                "WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            )
        elif adversarial_case == "expired_assessor":
            from services.runner.receipts import canonical

            expired = cap_sign(
                profile.model_copy(
                    update={
                        "issued_at": datetime.now(UTC) - timedelta(days=2),
                        "expires_at": datetime.now(UTC) - timedelta(days=1),
                    }
                )
            )
            expired_bytes = canonical(expired)
            expired_ref = "sha256:" + hashlib.sha256(expired_bytes).hexdigest()
            conn.execute(
                "UPDATE replay_submissions SET capability_payload=%s,capability_ref=%s "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (expired_bytes, expired_ref, run.run_id),
            )
        elif adversarial_case in {"untrusted_collector", "modified_suite_receipt"}:
            from services.runner.receipts import ReceiptDenied, canonical

            phase = (
                "EXISTING_SUITE"
                if adversarial_case == "modified_suite_receipt"
                else "CANDIDATE"
            )
            row = conn.execute(
                "SELECT payload FROM replay_produced_receipts "
                "WHERE run_id=%s AND phase=%s",
                (run.run_id, phase),
            ).fetchone()
            assert row is not None
            original = ProducedReceipt.model_validate_json(row[0])
            forged = original.model_copy(
                update={
                    "signed": original.signed.model_copy(
                        update={"signature": "0" * 128}
                    )
                }
            )
            forged_bytes = canonical(forged)
            conn.execute(
                "UPDATE replay_produced_receipts SET payload=%s,digest=%s "
                "WHERE run_id=%s AND phase=%s",
                (
                    forged_bytes,
                    hashlib.sha256(forged_bytes).hexdigest(),
                    run.run_id,
                    phase,
                ),
            )
        elif adversarial_case in {
            "wrong_image_uuid",
            "wrong_runtime_configuration",
            "wrong_workspace_digest",
        }:
            from services.persistence.producer import Preparation

            row = conn.execute(
                "SELECT payload FROM replay_preparations "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (run.run_id,),
            ).fetchone()
            assert row is not None
            preparation = Preparation.model_validate_json(row[0])
            binding_update = {
                "wrong_image_uuid": {"image_uuid": uuid4()},
                "wrong_runtime_configuration": {
                    "configuration_ref": "sha256:" + "0" * 64
                },
                "wrong_workspace_digest": {"tree_ref": "sha256:" + "0" * 64},
            }[adversarial_case]
            changed_preparation = preparation.model_copy(
                update={
                    "binding": preparation.binding.model_copy(update=binding_update)
                }
            )
            changed_bytes = canonical(changed_preparation)
            conn.execute(
                "UPDATE replay_preparations SET payload=%s,digest=%s "
                "WHERE run_id=%s AND phase='CANDIDATE'",
                (changed_bytes, hashlib.sha256(changed_bytes).hexdigest(), run.run_id),
            )
        elif adversarial_case == "modified_regression_test":
            conn.execute(
                "UPDATE reproduction_artifacts "
                "SET payload=payload || decode('20','hex') "
                "WHERE run_id=%s",
                (run.run_id,),
            )
        elif adversarial_case == "budget_violation":
            conn.execute(
                "UPDATE runs SET checkpoint=jsonb_set(checkpoint,"
                "'{remaining_budget,sandbox_commands}','-1'::jsonb) WHERE id=%s",
                (run.run_id,),
            )
        if adversarial_case != "lease_conflict":
            conn.execute(
                "UPDATE job_leases "
                "SET leased_until=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s AND status='leased'",
                (run.run_id,),
            )
    restarted_worker = ProductionVerificationWorker(
        store, trust, worker_config, test_only=True
    )
    concurrent_worker = ProductionVerificationWorker(
        store, trust, worker_config, test_only=True
    )
    if not adversarial_case.startswith("positive"):
        if adversarial_case == "lease_conflict":
            assert restarted_worker.tick(uuid4(), run_id=run.run_id).status == "IDLE"
        else:
            from pydantic import ValidationError

            from services.orchestrator.reproduction import ReproductionHandoffError
            from services.runner.receipts import ReceiptDenied

            try:
                result = restarted_worker.tick(uuid4(), run_id=run.run_id)
            except (
                StoreError,
                ValidationError,
                ReceiptDenied,
                ReproductionHandoffError,
            ):
                pass
            else:
                assert result.status == "IDLE"
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            state = conn.execute(
                "SELECT checkpoint->>'current_stage',"
                "(SELECT count(*) FROM verification_results WHERE run_id=%s) "
                "FROM runs WHERE id=%s",
                (run.run_id, run.run_id),
            ).fetchone()
        assert state is not None
        assert state[0] == Stage.VERIFYING.value
        count = state[1]
        assert count == 0
        record_property("rejected_invariant", adversarial_case)
        record_property("false_verified_envelope_persisted", "false")
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda worker: worker.tick(uuid4(), run_id=run.run_id),
                (restarted_worker, concurrent_worker),
            )
        )
    assert [result.status for result in results].count("FINALIZED") == 1
    assert [result.status for result in results].count("IDLE") == 1
    accepted = store.get(run.run_id, owner)
    assert accepted.current_stage == Stage.VERIFIED
    from services.persistence.verification import VerificationJournal
    from services.verifier.authenticated import GatePhaseBinding
    from services.verifier.gates import REQUIRED_GATES

    assert all(replay_operation_ids.values())
    bindings = tuple(
        GatePhaseBinding(
            phase=phase,
            image_uuid=(
                config().image_uuid
                if phase == "BASELINE"
                else patched_results[phase][0].result_image_uuid
            ),
            image_digest=digest,
            configuration_ref=profile.configuration_ref,
            workspace_sha256=(
                upload.plan.workspace_ref[7:]
                if phase == "BASELINE"
                else actions.patched_tree_ref[7:]
            ),
        )
        for phase in ("BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY")
    )
    verified_record = VerificationJournal(store).get_verified(
        run.run_id,
        owner,
        producer.gate_verifier,
        image_digest=digest,
        configuration_ref=profile.configuration_ref,
        phase_bindings=bindings,
    )
    assert accepted.verification_ref is not None
    assert verified_record.verdict.status == "VERIFIED"
    assert not verified_record.verdict.missing and not verified_record.verdict.failed
    assert (
        tuple(gate.gate for gate in verified_record.signed.payload.gates)
        == REQUIRED_GATES
    )
    assert all(gate.status == "PASS" for gate in verified_record.signed.payload.gates)
    gate_evidence = {
        gate.phase: gate
        for gate in verified_record.signed.payload.gates
        if gate.phase in PHASES
    }
    assert set(gate_evidence) == set(PHASES)
    assert all(gate.operation_id is not None for gate in gate_evidence.values())
    assert gate_evidence["BASELINE"].operation_id == base_operation
    for phase, operation_id in replay_operation_ids.items():
        assert gate_evidence[phase].operation_id == operation_id
    persisted_ref = accepted.verification_ref
    duplicate = producer.finalize(lease, owner, expected_version=accepted.version)
    assert duplicate.current_stage == Stage.VERIFIED
    assert duplicate.version == accepted.version
    assert duplicate.verification_ref == persisted_ref
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        persisted_count = conn.execute(
            "SELECT count(*) FROM verification_results WHERE run_id=%s",
            (run.run_id,),
        ).fetchone()[0]
    assert persisted_count == 1
    if adversarial_case == "positive_v5":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            envelope = conn.execute(
                "SELECT envelope_version,envelope_bytes FROM verification_results "
                "WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
        assert envelope is not None and envelope[0] == 5
        assert envelope[1] == canonical(verified_record.signed)
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                "UPDATE verification_results SET envelope_bytes=envelope_bytes || "
                "decode('00','hex') WHERE run_id=%s",
                (run.run_id,),
            )
        with pytest.raises(StoreError, match="VERIFICATION_ENVELOPE_INTEGRITY"):
            VerificationJournal(store).get(run.run_id, owner)
        record_property("v5_envelope_version", "5")
        record_property("v5_canonical_envelope_bytes_persisted", "true")
        record_property("tampered_v5_envelope_rejected", "true")
    assert restarted_worker.tick(uuid4(), run_id=run.run_id).status == "IDLE"
    from threading import Event

    shutdown = Event()
    shutdown.set()
    shutdown_result = restarted_worker.serve(uuid4(), shutdown, max_ticks=1)
    assert shutdown_result.ticks == 0 and shutdown_result.stop_requested
    record_property("synthetic_run_id", str(run.run_id))
    record_property("synthetic_envelope_id", persisted_ref)
    record_property("signature_revalidated_independently", "true")
    record_property("verified_transition", "VERIFIED")
    record_property("idempotent_repeat", "same_ref_same_version_single_row")
    record_property("worker_restart_recovered", "true")
    record_property("concurrent_finalizers", "one_finalized_one_idle")
    record_property("cooperative_shutdown", "true")
    record_property(
        "phase_ids_and_operations",
        ";".join(
            [
                f"BASELINE:{execution}:{base_operation}",
                *(
                    f"{phase}:{execution_by_phase[phase]}:{operation_id}"
                    for phase, operation_id in replay_operation_ids.items()
                ),
            ]
        ),
    )
    record_property(
        "seven_gates",
        ",".join(
            f"{gate.gate}={gate.status}"
            for gate in verified_record.signed.payload.gates
        ),
    )
