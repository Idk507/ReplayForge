"""Synthetic remote-shaped producer facts; real DB handoff, no live acceptance."""

import tarfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.orchestrator.produced_delivery import deliver_produced_replays
from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.evidence import EvidenceStore
from services.persistence.producer import ProducerJournal
from services.persistence.replay import ReplayJournal
from services.persistence.store import StoreError
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import ProducedReceipt
from services.runner.receipts import (
    ReceiptVerifier,
    SignedReplayReceipt,
    canonical,
    plan_ref,
)
from services.runner.workspace import WorkspaceMeasurement, archive_identity
from services.verifier.replay import ReplayActions
from tests.contract.test_sandbox_smoke import config
from tests.contract.test_workspace_measurement import archive
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


def inputs(store, *, include_suite=False):
    prepared_result = prepared(store, reserve=False, include_suite=include_suite)
    lease, owner, run, actions, candidate, clean, verifier = prepared_result[:7]
    suite_report = prepared_result[7] if include_suite else None
    content = archive([("app.py", b"x=1\n", tarfile.REGTYPE)])
    tree, count, size = archive_identity(content)

    def measured(plan):
        return plan.model_copy(
            update={
                "command_id": "PYTEST_FILES_MEASURED_V1",
                "argv": (plan.argv[0], "-B", *plan.argv[1:]),
            }
        )

    actions = ReplayActions.model_validate(
        {
            **actions.model_dump(),
            "candidate_plan": measured(actions.candidate_plan),
            "suite_plan": (
                None if actions.suite_plan is None else measured(actions.suite_plan)
            ),
            "clean_plan": measured(actions.clean_plan),
            "patched_tree_ref": tree,
        }
    )
    run = ReplayJournal(store).reserve(
        lease, owner, actions, expected_version=run.version
    )
    key = Ed25519PrivateKey.generate()
    _, profile, _, sign, admission = capability_fixture("REMOTE_SERVICE")
    journals = []
    phase_inputs = [
        (
            "CANDIDATE",
            actions.candidate_plan,
            actions.candidate_execution_id,
            candidate,
        ),
    ]
    if suite_report is not None and actions.suite_plan is not None:
        phase_inputs.append(
            (
                "EXISTING_SUITE",
                actions.suite_plan,
                actions.suite_execution_id,
                suite_report,
            )
        )
    phase_inputs.append(
        ("CLEAN_REPLAY", actions.clean_plan, actions.clean_execution_id, clean)
    )
    for phase, plan, execution, report in phase_inputs:
        assert execution is not None
        binding = PreparedImageBinding(
            plan_ref=plan_ref(plan),
            execution_id=execution,
            tree_ref=tree,
            image_uuid=config().image_uuid,
            image_digest=plan.request.image_digest,
            runner_id=profile.runner_id,
            configuration_ref=profile.configuration_ref,
        )
        journal = ProducerJournal(
            store,
            lease,
            owner,
            phase,
            binding,
            receipt_key_id="test",
            receipt_public_key=key.public_key(),
            origin="REMOTE_SERVICE",
            expected_version=run.version,
        )
        source = WorkspaceMeasurement(
            image_uuid=binding.image_uuid,
            tree_ref=tree,
            files=count,
            bytes_measured=size,
            evidence_origin="REMOTE_SERVICE",
        )

        # Fake internal port, NOT a provider transport. Its invented process
        # observations exercise only the production-shaped handoff contract.
        class FakePort:
            origin = "REMOTE_SERVICE"
            runner_id = profile.runner_id
            configuration_ref = profile.configuration_ref

            def __init__(self, prepared_journal, measurement):
                self.journal = prepared_journal
                self.source = measurement

            def submit(self, plan, execution_id, expected_tree_ref):
                self.journal.retain_preparation(self.source)
                return uuid4()

        dispatched = dispatch_replay(
            store,
            lease,
            owner,
            phase,
            sign(),
            admission,
            FakePort(journal, source),
            expected_version=run.version,
            allow_live=True,
        )
        payload = report.signed.payload.model_copy(
            update={
                "plan_ref": plan_ref(plan),
                "source_tree_ref": tree,
                "final_tree_ref": tree,
                "observed_at": datetime.now(UTC),
            }
        )
        unsigned = SignedReplayReceipt(
            key_id="test", payload=payload, signature="0" * 128
        )
        signed = unsigned.model_copy(
            update={"signature": key.sign(canonical(unsigned, unsigned=True)).hex()}
        )
        journal.retain_receipt(
            ProducedReceipt(
                operation_id=dispatched.operation_id,
                source=source,
                final=source.model_copy(update={"image_uuid": uuid4()}),
                signed=signed,
                junit=report.junit,
            )
        )
        journals.append(journal)
    return (
        lease,
        owner,
        run,
        journals,
        ReceiptVerifier({"test": key.public_key()}, origin="REMOTE_SERVICE"),
    )


def test_completed_producer_handoff_includes_independent_suite_operation(store):
    lease, owner, run, journals, verifier = inputs(store, include_suite=True)
    first = deliver_produced_replays(
        store,
        lease,
        owner,
        journals[0],
        journals[2],
        verifier,
        workspace_sha256="a" * 64,
        expected_version=run.version,
        suite=journals[1],
    )
    _, files = EvidenceStore(store).read(run.run_id, owner, first.evidence_manifest_ref)
    names = {item.name for item in files}
    assert {"suite-receipt.json", "suite-junit.xml"} <= names


def test_completed_producer_pair_handoff_retains_exact_reports_and_reconciles(store):
    lease, owner, run, journals, verifier = inputs(store)
    first = deliver_produced_replays(
        store,
        lease,
        owner,
        *journals,
        verifier,
        workspace_sha256="a" * 64,
        expected_version=run.version,
    )
    again = deliver_produced_replays(
        store,
        lease,
        owner,
        *journals,
        verifier,
        workspace_sha256="a" * 64,
        expected_version=run.version,
    )
    assert first == again
    assert first.version == run.version + 1
    assert first.current_stage.value == "VERIFYING"
    manifest, files = EvidenceStore(store).read(
        run.run_id, owner, first.evidence_manifest_ref
    )
    assert len(files) == 5
    assert (
        next(f.content for f in files if f.name == "candidate-junit.xml")
        == journals[0].at_version(first.version).completed().junit
    )
    assert not first.verification_ref


@pytest.mark.parametrize("fault", ["owner", "phase", "version"])
def test_handoff_refuses_mixed_contexts(store, fault):
    lease, owner, run, journals, verifier = inputs(store)
    if fault == "phase":
        journals = list(reversed(journals))
    with pytest.raises(StoreError):
        deliver_produced_replays(
            store,
            lease,
            uuid4() if fault == "owner" else owner,
            *journals,
            verifier,
            workspace_sha256="a" * 64,
            expected_version=run.version + (fault == "version"),
        )
