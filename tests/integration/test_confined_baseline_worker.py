"""Actual controller and DB with tiny archives and synthetic remote responses."""

import json
import os
import tarfile
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb

from packages.contracts.repair import ReproductionProposal
from packages.contracts.run import Stage
from services.orchestrator.reproduction import bind_reproduction
from services.persistence.baseline_producer import BaselineProducerJournal
from services.persistence.store import Store, StoreError
from services.runner.admission import CapabilityVerifier
from services.runner.confinement import confinement_configuration_ref
from services.runner.prepared import PreparedImageBinding
from services.runner.receipts import plan_ref
from services.runner.workspace import archive_identity
from tests.contract.test_sandbox_smoke import config
from tests.contract.test_workspace_measurement import archive
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture
from tests.unit.test_runner_admission import fixture as capabilities

store = test_postgres.store


def setup(store, *, pending=False, lost=False, reproduction_fault=None):
    from services.runner.confined_baseline_provider import ConfinedBaselineProvider

    _, lease, owner, run, action, _, xml, _ = baseline_journal_fixture(store)
    run = run.model_copy(update={"context_refs": ("sha256:" + "f" * 64,)})
    proposal = ReproductionProposal(
        target_path=(
            "tests/test_other.py"
            if reproduction_fault == "path"
            else "tests/test_regression.py"
        ),
        behavior="duplicate transaction",
        failure_signature=(
            "OTHER_FAILURE_MARKER"
            if reproduction_fault == "signature"
            else "RF_DUPLICATE_TRANSACTION"
        ),
        test_source=(
            "def test_other():\n    assert False, 'RF_DUPLICATE_TRANSACTION'\n"
            if reproduction_fault == "name"
            else "def test_duplicate():\n    assert False, 'OTHER_FAILURE_MARKER'\n"
            if reproduction_fault == "marker"
            else "def test_duplicate():\n    assert False, 'RF_DUPLICATE_TRANSACTION'\n"
        ),
    )
    artifact = bind_reproduction(
        proposal, run, store.repository(run.repository_id, owner), owner
    )
    run = run.model_copy(update={"reproduction_test_ref": artifact.artifact_ref()})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
        if reproduction_fault != "missing":
            body = artifact.canonical_bytes()
            if reproduction_fault == "tamper":
                body += b" "
            conn.execute(
                "INSERT INTO reproduction_artifacts(run_id,digest,payload) "
                "VALUES (%s,%s,%s)",
                (run.run_id, artifact.artifact_ref()[7:], body),
            )
            if reproduction_fault == "expired":
                conn.execute(
                    "UPDATE reproduction_artifacts SET expires_at=clock_timestamp()-"
                    "interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
            if reproduction_fault == "consent":
                conn.execute(
                    "UPDATE repository_grants SET grant_json="
                    "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                    (run.repository_id,),
                )
    body = archive([("src/a.py", b"x=1\n", tarfile.REGTYPE)])
    tree = archive_identity(body)[0]
    request = action.plan.request.model_copy(
        update={"reproduction_test_ref": artifact.artifact_ref()}
    )
    plan = action.plan.model_copy(
        update={
            "command_id": "PYTEST_FILES_MEASURED_V1",
            "argv": (action.plan.argv[0], "-B", *action.plan.argv[1:]),
            "request": request,
        }
    )
    action = action.model_copy(update={"plan": plan, "source_tree_ref": tree})
    _, profile, _, sign, verifier = capabilities("REMOTE_SERVICE")
    reference = confinement_configuration_ref(
        plan, config().image_uuid, action.execution_id
    )
    profile = profile.model_copy(update={"configuration_ref": reference})
    verifier = CapabilityVerifier(
        verifier._keys,
        runner_id=profile.runner_id,
        backend="NEBIUS_CONTREE",
        origin="REMOTE_SERVICE",
        configuration_ref=reference,
    )
    binding = PreparedImageBinding(
        plan_ref=plan_ref(plan),
        execution_id=action.execution_id,
        tree_ref=tree,
        image_uuid=config().image_uuid,
        image_digest=plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=reference,
    )
    operation, image, calls = uuid4(), uuid4(), []
    key = Ed25519PrivateKey.generate()

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["networking"] == {"enabled": False}
            assert payload["shell"] is False and payload["preserve_env"] is False
            if lost:
                raise httpx.ReadTimeout("synthetic response lost")
            return httpx.Response(201, json={"uuid": str(operation)})
        if request.url.path.endswith("/archive"):
            return httpx.Response(200, content=body)
        if request.url.path.endswith("/download"):
            if request.url.params.get("path") == "/tmp/replayforge-execution.json":
                return httpx.Response(
                    200,
                    json={
                        "run_id": str(run.run_id),
                        "execution_id": str(action.execution_id),
                        "plan_ref": binding.plan_ref,
                        "argv": list(plan.argv),
                        "env": dict(plan.env),
                        "exit_code": 1,
                        "timed_out": False,
                        "output_limited": False,
                        "confinement": {
                            "uid": 1000,
                            "gid": 1000,
                            "process": {
                                "CapEff": "0000000000000000",
                                "NoNewPrivs": "1",
                                "Seccomp": "2",
                            },
                            "root_mount_read_only": True,
                            "tmp_bytes": 16777216,
                            "workspace_bytes": 16777216,
                            "cgroup": {
                                "cpu.max": "100000 100000",
                                "memory.max": "134217728",
                                "memory.swap.max": "0",
                                "pids.max": "16",
                            },
                        },
                    },
                )
            return httpx.Response(200, content=xml)
        return httpx.Response(
            200,
            json={
                "uuid": str(operation),
                "kind": "instance",
                "status": "PENDING" if pending else "SUCCESS",
                "image_uuid": str(binding.image_uuid),
                "result_image_uuid": str(image),
                "metadata": {
                    "result": {
                        "state": {"exit_code": 1, "timed_out": False, "signal": None}
                    }
                },
            },
        )

    def provider():
        return ConfinedBaselineProvider(
            config(),
            action,
            binding,
            sign(profile),
            verifier,
            receipt_key=key,
            receipt_key_id="test",
            allow_privileged_setup=True,
            transport=httpx.MockTransport(handler),
        )

    return lease, owner, run, operation, calls, provider


@pytest.mark.parametrize(
    "fault",
    ["missing", "tamper", "expired", "consent", "path", "name", "marker", "signature"],
)
def test_confined_baseline_rejects_unbound_reproduction_before_any_io(store, fault):
    from services.orchestrator.confined_baseline import advance_confined_baseline

    lease, owner, run, _, calls, provider = setup(store, reproduction_fault=fault)
    with pytest.raises(StoreError, match="BASELINE_REPRODUCTION"):
        advance_confined_baseline(store, lease, owner, provider())
    assert calls == []
    assert store.get(run.run_id, owner).version == run.version


def test_confined_baseline_submits_collects_and_restores_without_reexecution(store):
    from services.orchestrator.confined_baseline import advance_confined_baseline

    lease, owner, run, operation, calls, provider = setup(store)
    first = advance_confined_baseline(store, lease, owner, provider())
    assert first.status == "TEST_EVIDENCE_RETAINED"
    assert first.operation_id == operation
    assert store.get(run.run_id, owner).current_stage == Stage.REPRODUCING
    count = len(calls)
    again = advance_confined_baseline(Store(store.dsn), lease, owner, provider())
    assert again == first and len(calls) == count
    assert sum(request.method == "POST" for request in calls) == 1
    assert store.get(run.run_id, owner).remaining_budget.sandbox_commands == 19


def test_revoked_authorization_before_submission_is_sticky(store):
    from services.orchestrator.confined_baseline import advance_confined_baseline

    lease, owner, _, _, calls, factory = setup(store)
    versions = []

    def authorize(_lease, version):
        versions.append(version)
        if version == 1:
            raise StoreError("SYNTHETIC_ACCESS_REVOKED")

    with pytest.raises(StoreError, match="SYNTHETIC_ACCESS_REVOKED"):
        advance_confined_baseline(store, lease, owner, factory(), authorize=authorize)
    assert versions == [0, 1] and calls == []
    recovered = advance_confined_baseline(
        Store(store.dsn), lease, owner, factory(), authorize=lambda *_: None
    )
    assert recovered.status == "SUBMISSION_UNKNOWN" and calls == []


def test_driver_polls_pending_under_one_database_claim(store):
    import threading

    from services.orchestrator.confined_baseline_driver import run_confined_baseline

    _, _, run, operation, calls, provider = setup(store, pending=True)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE job_leases SET status='queued',token=NULL,attempt_count=0 "
            "WHERE run_id=%s",
            (run.run_id,),
        )
    result = run_confined_baseline(
        store,
        uuid4(),
        provider(),
        threading.Event(),
        max_polls=2,
        poll_seconds=0.01,
        heartbeat_seconds=0.01,
    )
    assert result.status == "PENDING" and result.polls == 2
    assert result.result is not None and result.result.operation_id == operation
    assert sum(request.method == "POST" for request in calls) == 1
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        assert (
            conn.execute(
                "SELECT attempt_count FROM job_leases WHERE run_id=%s", (run.run_id,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("lost", [False, True])
def test_pending_or_unknown_baseline_never_repeats_post(store, lost):
    from services.orchestrator.confined_baseline import advance_confined_baseline

    lease, owner, _, operation, calls, provider = setup(store, pending=True, lost=lost)
    first = advance_confined_baseline(store, lease, owner, provider())
    assert first.status == ("SUBMISSION_UNKNOWN" if lost else "PENDING")
    assert first.operation_id == (None if lost else operation)
    again = advance_confined_baseline(store, lease, owner, provider())
    assert again.status == first.status
    assert sum(request.method == "POST" for request in calls) == 1


def test_produced_baseline_delivery_rejects_test_evidence(store):
    from services.orchestrator.confined_baseline import advance_confined_baseline
    from services.orchestrator.produced_baseline_delivery import (
        deliver_produced_baseline,
    )

    lease, owner, run, _, _, factory = setup(store)
    provider = factory()
    advance_confined_baseline(store, lease, owner, provider)
    journal = BaselineProducerJournal(
        store,
        lease,
        owner,
        provider.binding,
        receipt_key_id=provider.receipt_key_id,
        receipt_public_key=provider.receipt_public_key,
        origin=provider.origin,
        expected_version=1,
    )
    with pytest.raises(StoreError, match="BASELINE_TEST_EVIDENCE_DENIED"):
        deliver_produced_baseline(
            store, lease, owner, journal, provider.receipt_verifier()
        )
    assert store.get(run.run_id, owner).current_stage == Stage.REPRODUCING
