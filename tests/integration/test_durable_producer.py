"""Small inert archives, real Postgres fences, synthetic provider HTTP only."""

import tarfile
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.producer import ProducerJournal
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store, StoreError
from services.runner.durable_producer import DurableMeasuredBackend
from services.runner.prepared import PreparedImageBinding
from services.runner.receipts import canonical, plan_ref
from services.runner.workspace import archive_identity
from services.verifier.replay import ReplayActions
from tests.contract.test_sandbox_smoke import config
from tests.contract.test_workspace_measurement import archive
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared
from tests.unit.test_replay_receipts import XML
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


def setup(store):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)
    content = archive([("app.py", b"x=1\n", tarfile.REGTYPE)])
    tree = archive_identity(content)[0]

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
            "clean_plan": measured(actions.clean_plan),
            "patched_tree_ref": tree,
        }
    )
    run = ReplayJournal(store).reserve(
        lease, owner, actions, expected_version=run.version
    )
    _, profile, _, sign, verifier = capability_fixture("REMOTE_SERVICE")
    binding = PreparedImageBinding(
        plan_ref=plan_ref(actions.candidate_plan),
        execution_id=actions.candidate_execution_id,
        tree_ref=tree,
        image_uuid=config().image_uuid,
        image_digest=actions.candidate_plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    operation, image, calls = uuid4(), uuid4(), []
    key = Ed25519PrivateKey.generate()

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": str(operation)})
        if request.url.path.endswith("/archive"):
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=XML)
        return httpx.Response(
            200,
            json={
                "uuid": str(operation),
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": str(binding.image_uuid),
                "result_image_uuid": str(image),
                "metadata": {
                    "result": {
                        "state": {"exit_code": 0, "timed_out": False, "signal": None}
                    }
                },
            },
        )

    def journal(**changes):
        return ProducerJournal(
            Store(store.dsn),
            changes.get("lease", lease),
            changes.get("owner", owner),
            "CANDIDATE",
            binding,
            receipt_key_id="test",
            receipt_public_key=key.public_key(),
            origin="TEST_TRANSPORT",
            expected_version=changes.get("version", run.version),
        )

    def backend(**changes):
        return DurableMeasuredBackend(
            config(),
            binding,
            sign(),
            verifier,
            receipt_key=key,
            receipt_key_id="test",
            journal=journal(**changes),
            transport=httpx.MockTransport(handler),
        )

    def submit(current):
        return dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            current,
            expected_version=run.version,
        )

    return lease, owner, run, operation, calls, backend, submit, journal


@pytest.mark.parametrize("collect_first", [False, True])
def test_producer_recovers_preparation_or_completed_receipt_without_resubmission(
    store, collect_first
):
    lease, owner, run, operation, calls, backend, submit, journal = setup(store)
    first = None
    with backend() as current:
        assert submit(current).status == "SUBMITTED"
        if collect_first:
            first = current.collect_receipt(operation)
    count = len(calls)
    with backend() as restarted:
        assert submit(restarted).status == "ALREADY_SUBMITTED"
        recovered = restarted.collect_receipt(operation)
        assert len(calls) == count + (0 if collect_first else 3)
        if first is not None:
            assert canonical(recovered) == canonical(first)
        final_count = len(calls)
        assert restarted.collect_receipt(operation) == recovered
        assert len(calls) == final_count
    assert sum(r.method == "POST" for r in calls) == 1
    assert journal().restore(operation)[2] == recovered
    assert store.get(run.run_id, owner) == run
    kinds = [e.event_type for e in store.events(run.run_id, owner)]
    assert kinds.count("REPLAY_PREPARATION_RETAINED") == 1
    assert kinds.count("REPLAY_RECEIPT_RETAINED") == 1


@pytest.mark.parametrize("fault", ["owner", "lease", "version", "cancel"])
def test_recovery_denials_precede_network(store, fault):
    lease, owner, run, operation, calls, backend, submit, _ = setup(store)
    with backend() as current:
        submit(current)
    changes = {}
    if fault == "owner":
        changes["owner"] = uuid4()
    if fault == "lease":
        changes["lease"] = lease.model_copy(update={"token": uuid4()})
    if fault == "version":
        changes["version"] = run.version + 1
    if fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=run.version)
    count = len(calls)
    with backend(**changes) as restarted:
        with pytest.raises(StoreError):
            restarted.collect_receipt(operation)
    assert len(calls) == count


def test_preparation_audit_failure_prevents_post_and_stays_unknown(store, monkeypatch):
    _, _, _, operation, calls, backend, submit, _ = setup(store)
    original = Store._event

    def fail(conn, run, kind):
        if kind == "REPLAY_PREPARATION_RETAINED":
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")
        original(conn, run, kind)

    monkeypatch.setattr(Store, "_event", staticmethod(fail))
    with backend() as current:
        with pytest.raises(StoreError):
            submit(current)
    with backend() as restarted:
        assert submit(restarted).status == "SUBMISSION_UNKNOWN"
        with pytest.raises(StoreError):
            restarted.collect_receipt(operation)
    assert [r.method for r in calls] == ["GET"]


@pytest.mark.parametrize(
    "fault", ["consent", "expired", "corrupt", "unknown_operation"]
)
def test_invalid_retained_preparation_cannot_resume(store, fault):
    import os

    import psycopg

    lease, owner, run, operation, calls, backend, submit, _ = setup(store)
    with backend() as current:
        submit(current)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "consent":
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
        elif fault == "expired":
            conn.execute(
                "UPDATE replay_preparations SET expires_at=clock_timestamp()"
                "-interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
        elif fault == "corrupt":
            conn.execute(
                "UPDATE replay_preparations SET digest=%s WHERE run_id=%s",
                ("0" * 64, run.run_id),
            )
    count = len(calls)
    with backend() as restarted:
        with pytest.raises(StoreError):
            restarted.collect_receipt(
                uuid4() if fault == "unknown_operation" else operation
            )
    assert len(calls) == count


def test_receipt_audit_rollback_retries_retention_without_more_http(store, monkeypatch):
    _, owner, run, operation, calls, backend, submit, _ = setup(store)
    original = Store._event

    def fail(conn, state, kind):
        if kind == "REPLAY_RECEIPT_RETAINED":
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")
        original(conn, state, kind)

    with backend() as current:
        submit(current)
        monkeypatch.setattr(Store, "_event", staticmethod(fail))
        with pytest.raises(StoreError):
            current.collect_receipt(operation)
        count = len(calls)
        monkeypatch.setattr(Store, "_event", staticmethod(original))
        current.collect_receipt(operation)
        assert len(calls) == count
    assert [e.event_type for e in store.events(run.run_id, owner)].count(
        "REPLAY_RECEIPT_RETAINED"
    ) == 1


def test_parallel_collectors_keep_one_receipt_and_original_time(store):
    from concurrent.futures import ThreadPoolExecutor

    _, owner, run, operation, calls, backend, submit, _ = setup(store)
    with backend() as current:
        submit(current)

    def collect(_):
        with backend() as restarted:
            return restarted.collect_receipt(operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(collect, range(2)))
    assert canonical(values[0]) == canonical(values[1])
    assert sum(r.method == "POST" for r in calls) == 1
    assert [e.event_type for e in store.events(run.run_id, owner)].count(
        "REPLAY_RECEIPT_RETAINED"
    ) == 1


@pytest.mark.parametrize("fault", ["signature", "content"])
def test_receipt_redelivery_cannot_change_signed_facts(store, fault):
    _, _, _, operation, calls, backend, submit, journal = setup(store)
    with backend() as current:
        submit(current)
        result = current.collect_receipt(operation)
    changed = (
        result.model_copy(update={"junit": b"other"})
        if fault == "content"
        else result.model_copy(
            update={"signed": result.signed.model_copy(update={"signature": "0" * 128})}
        )
    )
    count = len(calls)
    with pytest.raises(StoreError):
        journal().retain_receipt(changed)
    assert journal().restore(operation)[2] == result
    assert len(calls) == count


@pytest.mark.parametrize("table", ["replay_preparations", "replay_produced_receipts"])
def test_runtime_cannot_modify_producer_records(store, table):
    import psycopg
    from psycopg import sql

    _, _, _, operation, _, backend, submit, _ = setup(store)
    with backend() as current:
        submit(current)
        current.collect_receipt(operation)
    for statement in ("UPDATE {} SET digest=digest", "DELETE FROM {}"):
        with psycopg.connect(store.dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql.SQL(statement).format(sql.Identifier(table)))
