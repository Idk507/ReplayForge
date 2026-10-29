"""Real PostgreSQL delivery; tiny reports signed with synthetic authority only."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb

from packages.contracts.run import Stage
from services.orchestrator.baseline_delivery import receive_baseline
from services.orchestrator.replay_delivery import receive_replays
from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.evidence import EvidenceStore
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store, StoreError
from services.runner.receipts import (
    ReceiptDenied,
    ReceiptVerifier,
    SignedReplayReceipt,
    canonical,
    plan_ref,
)
from services.verifier.replay import ReplayActions, ReplayReport
from tests.integration import test_postgres
from tests.integration.test_baseline_delivery import prepared as baseline_inputs
from tests.unit.test_replay_receipts import XML
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


def prepared(store, *, failing=False, reserve=True, include_suite=False):
    _, lease, owner, _, baseline, signed, xml, verifier = baseline_inputs(store)
    run = receive_baseline(
        store, lease, owner, baseline.execution_id, signed, xml, verifier
    )
    run = run.model_copy(
        update={
            "current_stage": Stage.VERIFYING,
            "patch_attempt_refs": ("sha256:" + "7" * 64,),
        }
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
    key = Ed25519PrivateKey.generate()
    plans, reports, operation_ids = {}, {}, {}
    phases = (
        ("CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY")
        if include_suite
        else ("CANDIDATE", "CLEAN_REPLAY")
    )
    for phase in phases:
        operation_ids[phase] = uuid4()
        plan = baseline.plan.model_copy(
            update={
                "request": baseline.plan.request.model_copy(
                    update={"phase": phase, "patch_ref": run.patch_attempt_refs[-1]}
                )
            }
        )
        payload = signed.payload.model_copy(
            update={
                "execution_id": uuid4(),
                "plan_ref": plan_ref(plan),
                "source_tree_ref": "sha256:" + "8" * 64,
                "final_tree_ref": "sha256:" + "8" * 64,
                "junit_ref": "sha256:" + hashlib.sha256(XML).hexdigest(),
                "exit_code": 1 if failing else 0,
            }
        )
        envelope = SignedReplayReceipt(
            key_id="test", payload=payload, signature="0" * 128
        )
        envelope = envelope.model_copy(
            update={"signature": key.sign(canonical(envelope, unsigned=True)).hex()}
        )
        plans[phase] = plan
        reports[phase] = ReplayReport(
            operation_id=operation_ids[phase], signed=envelope, junit=XML
        )
    actions = ReplayActions(
        candidate_plan=plans["CANDIDATE"],
        suite_plan=plans.get("EXISTING_SUITE"),
        clean_plan=plans["CLEAN_REPLAY"],
        baseline_execution_id=baseline.execution_id,
        candidate_execution_id=reports["CANDIDATE"].signed.payload.execution_id,
        suite_execution_id=(
            reports["EXISTING_SUITE"].signed.payload.execution_id
            if include_suite
            else None
        ),
        clean_execution_id=reports["CLEAN_REPLAY"].signed.payload.execution_id,
        patched_tree_ref="sha256:" + "8" * 64,
        test_name="test_duplicate",
    )
    from services.persistence.replay import ReplayJournal

    if reserve:
        run = ReplayJournal(store).reserve(
            lease, owner, actions, expected_version=run.version
        )
        _, profile, _, attest, capability_verifier = capability_fixture(
            "REMOTE_SERVICE"
        )

        class Backend:
            origin = "REMOTE_SERVICE"
            runner_id = profile.runner_id
            configuration_ref = profile.configuration_ref

            def submit(self, plan, execution_id, expected_tree_ref):
                return operation_ids[plan.request.phase]

        backend = Backend()
        for phase in phases:
            dispatch_replay(
                store,
                lease,
                owner,
                phase,
                attest(),
                capability_verifier,
                backend,
                expected_version=run.version,
                allow_live=True,
            )
    result = (
        lease,
        owner,
        run,
        actions,
        reports["CANDIDATE"],
        reports["CLEAN_REPLAY"],
        ReceiptVerifier({"test": key.public_key()}, origin="REMOTE_SERVICE"),
    )
    return (*result, reports["EXISTING_SUITE"]) if include_suite else result


@pytest.mark.parametrize("failing", [False, True])
def test_delivery_restart_and_concurrent_duplicates_preserve_exact_bytes(
    store, failing
):
    lease, owner, run, actions, candidate, clean, verifier = prepared(
        store, failing=failing
    )

    def deliver(_):
        return receive_replays(
            Store(store.dsn),
            lease,
            owner,
            actions,
            candidate,
            clean,
            verifier,
            workspace_sha256="a" * 64,
            expected_version=run.version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, range(2)))
    assert results[0] == results[1] == deliver(None)
    assert results[0].current_stage == Stage.VERIFYING
    assert results[0].version == run.version + 1
    _, files = EvidenceStore(Store(store.dsn)).read(
        run.run_id, owner, results[0].evidence_manifest_ref
    )
    assert next(f.content for f in files if f.name == "candidate-junit.xml") == XML
    assert next(
        f.content for f in files if f.name == "clean-receipt.json"
    ) == canonical(clean.signed)
    assert [e.event_type for e in store.events(run.run_id, owner)].count(
        "EVIDENCE_RETAINED"
    ) == 1


def test_existing_suite_operation_and_receipt_are_retained_as_a_distinct_phase(store):
    lease, owner, run, actions, candidate, clean, verifier, suite = prepared(
        store, include_suite=True
    )
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
        suite=suite,
    )
    _, retained = EvidenceStore(store).read(
        run.run_id, owner, result.evidence_manifest_ref
    )
    assert any(item.name == "suite-receipt.json" for item in retained)
    assert any(item.name == "suite-junit.xml" for item in retained)
    assert suite.operation_id == ReplayJournal(store).operation_id(
        run.run_id, owner, "EXISTING_SUITE"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "lease",
        "version",
        "patch",
        "test",
        "baseline",
        "signature",
        "operation",
        "cancel",
        "consent",
        "rollback",
        "conflict",
    ],
)
def test_delivery_denials_leave_no_partial_bundle(store, fault, monkeypatch):
    lease, owner, run, actions, candidate, clean, verifier = prepared(store)
    version = run.version
    if fault == "owner":
        owner = uuid4()
    elif fault == "lease":
        lease = lease.model_copy(update={"token": uuid4()})
    elif fault == "version":
        version += 1
    elif fault == "signature":
        candidate = candidate.model_copy(
            update={
                "signed": candidate.signed.model_copy(update={"signature": "0" * 128})
            }
        )
    elif fault == "operation":
        candidate = candidate.model_copy(update={"operation_id": uuid4()})
    elif fault == "baseline":
        actions = actions.model_copy(update={"baseline_execution_id": uuid4()})
    elif fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=version)
    elif fault in {"patch", "test", "consent"}:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "consent":
                conn.execute(
                    "UPDATE repository_grants SET grant_json="
                    "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                    (run.repository_id,),
                )
            else:
                update = (
                    {"patch_attempt_refs": ("sha256:" + "9" * 64,)}
                    if fault == "patch"
                    else {"reproduction_test_ref": "sha256:" + "9" * 64}
                )
                conn.execute(
                    "UPDATE runs SET checkpoint=%s WHERE id=%s",
                    (
                        Jsonb(run.model_copy(update=update).model_dump(mode="json")),
                        run.run_id,
                    ),
                )
    elif fault == "rollback":

        def fail(*args):
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")

        monkeypatch.setattr(Store, "_event", staticmethod(fail))
    elif fault == "conflict":
        receive_replays(
            store,
            lease,
            owner,
            actions,
            candidate,
            clean,
            verifier,
            workspace_sha256="a" * 64,
            expected_version=version,
        )
    with pytest.raises((StoreError, ReceiptDenied)):
        receive_replays(
            store,
            lease,
            owner,
            actions,
            candidate,
            clean,
            verifier,
            workspace_sha256="b" * 64 if fault == "conflict" else "a" * 64,
            expected_version=version,
        )
    with psycopg.connect(store.dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM evidence_bundles WHERE run_id=%s", (run.run_id,)
        ).fetchone()[0] == (1 if fault == "conflict" else 0)
