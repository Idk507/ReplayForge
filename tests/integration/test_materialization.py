"""Actual source/reproduction custody and DB fences; synthetic remote HTTP only."""

import base64
import json
import os
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from packages.contracts.repair import ReproductionProposal
from packages.contracts.run import RunState, Stage
from services.orchestrator.materialization import materialize_once
from services.orchestrator.reproduction import ReproductionHandoffError
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.materialization import MaterializationUpload, prepare_upload
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.unit.test_source_archive import source

store = test_postgres.store


def setup(
    store: Store,
) -> tuple[UUID, RunState, Lease, dict[str, bytes], MaterializationUpload]:
    owner, state, lease, bundle, proof = test_postgres.source_manifest_fixture(store)
    files = {"src/a.py": b"x=1\n"}
    proof = proof.model_copy(update={"files": (source("src/a.py", files["src/a.py"]),)})
    authority = Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"])
    with authority._connection() as db:
        db.execute(
            "UPDATE repository_grants SET grant_json=jsonb_set(grant_json,"
            "'{approved_paths}','[\"src/\",\"tests/\"]') WHERE id=%s",
            (state.repository_id,),
        )
    state = store.store_context(
        lease, bundle, expected_version=3, source_manifest=proof, queue_triage=True
    )
    lease = store.claim(uuid4(), triage_only=True, run_id=state.run_id)
    assert lease is not None
    state = ReproductionJournal(store).retain(
        lease,
        owner,
        ReproductionProposal(
            target_path="tests/test_a.py",
            behavior="x is two",
            failure_signature="1 != 2",
            test_source="def test_a():\n    assert 1 == 2\n",
        ),
        expected_version=state.version,
    )
    upload = prepare_upload(
        config().image_uuid,
        proof,
        files,
        ReproductionJournal(store).get(state.run_id, owner),
    )
    return owner, state, lease, files, upload


def provider(upload, *, pending=False, drop=False):
    calls = []
    op, image = uuid4(), uuid4()
    observed = {}
    ready = [not pending]

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            if drop:
                raise httpx.ReadTimeout("private", request=request)
            observed.update(json.loads(request.content))
            return httpx.Response(201, json={"uuid": str(op)})
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(op),
                    "kind": "instance",
                    "status": "SUCCESS" if ready[0] else "EXECUTING",
                    "image_uuid": str(config().image_uuid),
                    "result_image_uuid": str(image) if ready[0] else None,
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": 0,
                                "signal": None,
                                "timed_out": False,
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, content=base64.b64decode(observed["stdin"]["value"]))

    return calls, ready, httpx.MockTransport(handle)


def test_materialization_restart_collects_known_operation_and_preserves_observation(
    store,
):
    owner, run, lease, files, upload = setup(store)
    calls, ready, transport = provider(upload, pending=True)
    assert (
        materialize_once(
            store,
            lease,
            owner,
            config(),
            files,
            lambda: None,
            expected_version=run.version,
            transport=transport,
        )
        is None
    )
    retained = store.get(run.run_id, owner)
    assert retained.version == run.version + 1
    assert (
        retained.remaining_budget.sandbox_commands
        == run.remaining_budget.sandbox_commands - 1
    )
    ready[0] = True
    result = materialize_once(
        Store(store.dsn),
        lease,
        owner,
        config(),
        files,
        lambda: None,
        expected_version=retained.version,
        transport=transport,
    )
    assert (
        result is not None and result.measurement.tree_ref == upload.plan.workspace_ref
    )
    count = len(calls)
    cached = materialize_once(
        Store(store.dsn),
        lease,
        owner,
        config(),
        files,
        lambda: None,
        expected_version=retained.version,
        transport=transport,
    )
    assert cached == result and len(calls) == count
    assert sum(r.method == "POST" for r in calls) == 1
    assert store.events(run.run_id, owner)[-1].event_type == "MATERIALIZATION_MEASURED"
    assert store.get(run.run_id, owner).current_stage == Stage.REPRODUCING
    assert not result.can_authorize_execution


def test_unknown_submission_is_sticky_after_restart_and_debits_only_once(store):
    owner, run, lease, files, upload = setup(store)
    calls, _, transport = provider(upload, drop=True)
    from packages.contracts.sandbox import RunnerError

    with pytest.raises(RunnerError, match="SUBMISSION_UNKNOWN"):
        materialize_once(
            store,
            lease,
            owner,
            config(),
            files,
            lambda: None,
            expected_version=run.version,
            transport=transport,
        )
    with pytest.raises(StoreError, match="SUBMISSION_UNKNOWN"):
        materialize_once(
            Store(store.dsn),
            lease,
            owner,
            config(),
            files,
            lambda: None,
            expected_version=run.version,
            transport=transport,
        )
    assert len(calls) == 1
    assert (
        store.get(run.run_id, owner).remaining_budget.sandbox_commands
        == run.remaining_budget.sandbox_commands - 1
    )


@pytest.mark.parametrize("fault", ["cancel", "consent", "lease", "version"])
def test_materialization_current_fences_prevent_further_provider_io(store, fault):
    owner, run, lease, files, upload = setup(store)
    calls, ready, transport = provider(upload, pending=True)
    materialize_once(
        store,
        lease,
        owner,
        config(),
        files,
        lambda: None,
        expected_version=run.version,
        transport=transport,
    )
    current = store.get(run.run_id, owner)
    if fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=current.version)
    if fault == "consent":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as db:
            db.execute(
                "UPDATE repository_grants SET grant_json=jsonb_set(grant_json,"
                "'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
    if fault == "lease":
        with psycopg.connect(store.dsn) as db:
            db.execute(
                "UPDATE job_leases SET leased_until=clock_timestamp()"
                "-interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
    count = len(calls)
    with pytest.raises((StoreError, ReproductionHandoffError)):
        materialize_once(
            store,
            lease,
            owner,
            config(),
            files,
            lambda: None,
            expected_version=current.version + 10
            if fault == "version"
            else current.version,
            transport=transport,
        )
    assert len(calls) == count


@pytest.mark.parametrize(
    "table",
    [
        "materialization_intents",
        "materialization_operations",
        "materialization_results",
    ],
)
def test_runtime_cannot_remove_materialization_custody(store, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(store.dsn) as db:
            db.execute(f"DELETE FROM {table}")
