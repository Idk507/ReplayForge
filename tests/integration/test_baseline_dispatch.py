"""Baseline submission port uses fake transport and disposable PostgreSQL."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from services.orchestrator.baseline_dispatch import dispatch_baseline
from services.persistence.store import Store, StoreError
from services.runner.admission import AdmissionDenied
from tests.integration import test_postgres
from tests.integration.test_postgres import (
    baseline_journal_fixture as _baseline_fixture,
)
from tests.integration.test_replay_dispatch import FakeBackend
from tests.unit.test_runner_admission import fixture

store = test_postgres.store


def baseline_journal_fixture(store):
    values = list(_baseline_fixture(store))
    action = values[4]
    values[4] = action.model_copy(
        update={
            "plan": action.plan.model_copy(
                update={"command_id": "PYTEST_FILES_SAVED_JUNIT_V1"}
            )
        }
    )
    return tuple(values)


def test_real_rest_serializer_preserves_report_and_denies_guest_network(store):
    import json

    import httpx

    from services.runner.prepared import PreparedImageBinding, PreparedNebiusBackend
    from services.runner.receipts import plan_ref
    from tests.contract.test_sandbox_smoke import config

    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    binding = PreparedImageBinding(
        plan_ref=plan_ref(action.plan),
        execution_id=action.execution_id,
        tree_ref=action.source_tree_ref,
        image_uuid=config().image_uuid,
        image_digest=action.plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    calls = []
    operation = uuid4()

    def handle(req):
        body = json.loads(req.content)
        assert body["networking"] == {"enabled": False}
        assert body["disposable"] is False and body["shell"] is False
        assert body["preserve_env"] is False
        calls.append(req)
        return httpx.Response(201, json={"uuid": str(operation)})

    with PreparedNebiusBackend(
        config(), binding, sign(), verifier, transport=httpx.MockTransport(handle)
    ) as backend:
        result = dispatch_baseline(
            store,
            lease,
            owner,
            action.execution_id,
            sign(),
            verifier,
            backend,
            expected_version=0,
        )
    assert result.status == "SUBMITTED" and result.operation_id == operation
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [False, True])
def test_baseline_submission_is_one_shot_across_restart(store, timeout):
    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    backend = FakeBackend(profile)
    if timeout:
        backend.callback = lambda: (_ for _ in ()).throw(TimeoutError())
    first = dispatch_baseline(
        store,
        lease,
        owner,
        action.execution_id,
        sign(),
        verifier,
        backend,
        expected_version=0,
    )
    again = dispatch_baseline(
        Store(store.dsn),
        lease,
        owner,
        action.execution_id,
        sign(),
        verifier,
        backend,
        expected_version=1,
    )
    assert first.status == ("SUBMISSION_UNKNOWN" if timeout else "SUBMITTED")
    assert again.status == ("SUBMISSION_UNKNOWN" if timeout else "ALREADY_SUBMITTED")
    assert len(backend.calls) == 1
    with store._connection() as conn:
        saved = conn.execute(
            "SELECT capability_ref,capability_payload FROM baseline_submissions "
            "WHERE execution_id=%s",
            (action.execution_id,),
        ).fetchone()
    assert saved is not None
    assert hashlib.sha256(bytes(saved["capability_payload"])).hexdigest() == (
        saved["capability_ref"].removeprefix("sha256:")
    )


@pytest.mark.parametrize("gap", ["reservation", "legacy", "raced"])
def test_baseline_crash_and_legacy_operation_boundaries(store, gap, monkeypatch):
    from services.persistence.operations import OperationJournal

    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    backend = FakeBackend(profile)
    version = 0
    if gap in {"reservation", "legacy"}:
        version = journal.reserve_dispatch(
            lease, owner, action.execution_id, expected_version=0
        )
    if gap == "legacy":
        OperationJournal(store).record(
            lease,
            owner,
            action.execution_id,
            provider="TEST_TRANSPORT",
            phase="SUBMISSION_UNKNOWN",
            expected_version=1,
        )
        with pytest.raises(StoreError, match="EXTERNAL_OPERATION"):
            dispatch_baseline(
                store,
                lease,
                owner,
                action.execution_id,
                sign(),
                verifier,
                backend,
                expected_version=version,
            )
        assert not backend.calls
        return
    if gap == "raced":
        original = OperationJournal.record

        def record(self, *args, **kwargs):
            if kwargs.get("require_new"):
                original(self, *args, **{**kwargs, "require_new": False})
            return original(self, *args, **kwargs)

        monkeypatch.setattr(OperationJournal, "record", record)
    result = dispatch_baseline(
        store,
        lease,
        owner,
        action.execution_id,
        sign(),
        verifier,
        backend,
        expected_version=version,
    )
    assert result.status == ("SUBMISSION_UNKNOWN" if gap == "raced" else "SUBMITTED")
    assert len(backend.calls) == (0 if gap == "raced" else 1)
    assert store.get(run.run_id, owner).remaining_budget.sandbox_commands == 19
    assert result.can_authorize_verification is False


def test_concurrent_baseline_dispatch_calls_provider_once(store):
    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    backend = FakeBackend(profile)

    def send():
        return dispatch_baseline(
            store,
            lease,
            owner,
            action.execution_id,
            sign(),
            verifier,
            backend,
            expected_version=0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert len(backend.calls) == 1
    assert {r.status for r in results} <= {
        "SUBMITTED",
        "ALREADY_SUBMITTED",
        "SUBMISSION_UNKNOWN",
    }


@pytest.mark.parametrize(
    "fault", ["live", "runner", "configuration", "assessment", "cancel", "version"]
)
def test_denied_baseline_never_reaches_provider(store, fault):
    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture(
        "TEST_TRANSPORT" if fault == "assessment" else "REMOTE_SERVICE"
    )
    backend = FakeBackend(profile)
    if fault == "live":
        backend.origin = "REMOTE_SERVICE"
    if fault == "runner":
        backend.runner_id = uuid4()
    if fault == "configuration":
        backend.configuration_ref = "sha256:" + "f" * 64
    if fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=0)
    with pytest.raises((StoreError, AdmissionDenied)):
        dispatch_baseline(
            store,
            lease,
            owner,
            action.execution_id,
            sign(),
            verifier,
            backend,
            expected_version=4 if fault == "version" else 0,
        )
    assert not backend.calls


def test_claim_binding_tampering_and_runtime_mutation_are_denied(store):
    from alembic import command
    from alembic.config import Config

    journal, lease, owner, run, action, *_ = baseline_journal_fixture(store)
    journal.record_action(lease, owner, action, expected_version=0)
    _, profile, _, sign, verifier = fixture("REMOTE_SERVICE")
    backend = FakeBackend(profile)
    dispatch_baseline(
        store,
        lease,
        owner,
        action.execution_id,
        sign(),
        verifier,
        backend,
        expected_version=0,
    )
    with psycopg.connect(store.dsn) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "DELETE FROM baseline_submissions WHERE execution_id=%s",
                (action.execution_id,),
            )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as c:
        c.execute(
            "UPDATE baseline_submissions SET configuration_ref=%s "
            "WHERE execution_id=%s",
            ("sha256:" + "f" * 64, action.execution_id),
        )
    with pytest.raises(StoreError, match="CONFLICT"):
        dispatch_baseline(
            store,
            lease,
            owner,
            action.execution_id,
            sign(),
            verifier,
            backend,
            expected_version=1,
        )
    assert len(backend.calls) == 1
    with pytest.raises(RuntimeError, match="ARCHIVE_BASELINE"):
        command.downgrade(Config("alembic.ini"), "0027")
