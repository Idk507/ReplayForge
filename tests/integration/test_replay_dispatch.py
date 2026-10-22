"""Durable dispatch contracts with a fake port and ephemeral capability signer."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.store import Store, StoreError
from services.runner.admission import AdmissionDenied
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


class FakeBackend:
    origin = "TEST_TRANSPORT"

    def __init__(self, profile, callback=None):
        self.runner_id = profile.runner_id
        self.configuration_ref = profile.configuration_ref
        self.calls = []
        self.callback = callback
        self.operation = uuid4()

    def submit(self, plan, execution_id, expected_tree_ref):
        self.calls.append((plan, execution_id, expected_tree_ref))
        if self.callback:
            self.callback()
        return self.operation


def inputs(store, callback=None):
    lease, owner, run, actions, *_ = prepared(store)
    _, profile, _, sign, verifier = capability_fixture("REMOTE_SERVICE")
    return (
        lease,
        owner,
        run,
        actions,
        profile,
        sign,
        verifier,
        FakeBackend(profile, callback),
    )


@pytest.mark.parametrize("phase", ["CANDIDATE", "CLEAN_REPLAY"])
def test_submit_once_and_restart_discovers_known_operation(store, phase):
    lease, owner, run, actions, _, sign, verifier, backend = inputs(store)
    first = dispatch_replay(
        store,
        lease,
        owner,
        phase,
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    again = dispatch_replay(
        Store(store.dsn),
        lease,
        owner,
        phase,
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    assert first.status == "SUBMITTED"
    assert again.status == "ALREADY_SUBMITTED"
    assert first.operation_id == again.operation_id == backend.operation
    assert first.evidence_origin == "TEST_TRANSPORT"
    assert first.can_authorize_verification is False
    assert len(backend.calls) == 1
    assert backend.calls[0][2] == actions.patched_tree_ref
    assert store.get(run.run_id, owner) == run


def test_concurrent_claims_never_duplicate_external_call(store):
    lease, owner, run, _, _, sign, verifier, backend = inputs(store)

    def submit(_):
        return dispatch_replay(
            Store(store.dsn),
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            backend,
            expected_version=run.version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sum(r.status == "SUBMITTED" for r in results) == 1
    assert len(backend.calls) == 1


@pytest.mark.parametrize("fault", [TimeoutError, ConnectionError])
def test_uncertain_submission_is_sticky_across_restart(store, fault):
    def fail():
        raise fault("synthetic; no real network")

    lease, owner, run, _, _, sign, verifier, backend = inputs(store, fail)
    for _ in range(2):
        result = dispatch_replay(
            Store(store.dsn),
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            backend,
            expected_version=run.version,
        )
        assert result.status == "SUBMISSION_UNKNOWN"
        assert result.retryable is False
    assert len(backend.calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "lease",
        "version",
        "cancel",
        "consent",
        "signature",
        "capability",
        "runner",
        "configuration",
        "live",
        "rollback",
    ],
)
def test_denials_never_call_port_or_create_submission(store, fault, monkeypatch):
    lease, owner, run, _, profile, sign, verifier, backend = inputs(store)
    signed = sign()
    if fault == "owner":
        owner = uuid4()
    elif fault == "lease":
        lease = lease.model_copy(update={"token": uuid4()})
    elif fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=run.version)
    elif fault == "consent":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
    elif fault == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})
    elif fault == "capability":
        signed = sign(profile.model_copy(update={"controls": ()}))
    elif fault == "runner":
        backend.runner_id = uuid4()
    elif fault == "configuration":
        backend.configuration_ref = "sha256:" + "9" * 64
    elif fault == "live":
        backend.origin = "REMOTE_SERVICE"
    elif fault == "rollback":

        def fail(*args):
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")

        monkeypatch.setattr(Store, "_event", staticmethod(fail))
    with pytest.raises((StoreError, AdmissionDenied)):
        dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            signed,
            verifier,
            backend,
            expected_version=run.version + (fault == "version"),
        )
    assert backend.calls == []
    with psycopg.connect(store.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM replay_submissions WHERE run_id=%s", (run.run_id,)
            ).fetchone()[0]
            == 0
        )


def test_stale_worker_after_io_retains_unknown_intent_and_never_resubmits(store):
    lease, owner, run, _, _, sign, verifier, backend = inputs(store)

    def cancel():
        store.cancel(run.run_id, owner, expected_version=run.version)

    backend.callback = cancel
    with pytest.raises(StoreError):
        dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            backend,
            expected_version=run.version,
        )
    with psycopg.connect(store.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM replay_submissions WHERE run_id=%s", (run.run_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM replay_submission_results WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()[0]
            == 0
        )
    assert len(backend.calls) == 1


def test_result_audit_failure_rolls_back_binding_but_keeps_sticky_claim(
    store, monkeypatch
):
    lease, owner, run, _, _, sign, verifier, backend = inputs(store)
    original = Store._event

    def fail_result(conn, state, kind):
        if kind == "REPLAY_OPERATION_BOUND":
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")
        original(conn, state, kind)

    monkeypatch.setattr(Store, "_event", staticmethod(fail_result))
    with pytest.raises(StoreError, match="SYNTHETIC_AUDIT_FAILURE"):
        dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            backend,
            expected_version=run.version,
        )
    result = dispatch_replay(
        Store(store.dsn),
        lease,
        owner,
        "CANDIDATE",
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    assert result.status == "SUBMISSION_UNKNOWN"
    assert len(backend.calls) == 1


def test_same_provider_operation_cannot_bind_both_phases(store):
    lease, owner, run, _, _, sign, verifier, backend = inputs(store)
    dispatch_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    with pytest.raises(StoreError, match="DB_FAILURE_23505"):
        dispatch_replay(
            store,
            lease,
            owner,
            "CLEAN_REPLAY",
            sign(),
            verifier,
            backend,
            expected_version=run.version,
        )
    result = dispatch_replay(
        store,
        lease,
        owner,
        "CLEAN_REPLAY",
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    assert result.status == "SUBMISSION_UNKNOWN"
    assert len(backend.calls) == 2


@pytest.mark.parametrize("table", ["replay_submissions", "replay_submission_results"])
def test_runtime_cannot_modify_submission_history(store, table):
    lease, owner, run, _, _, sign, verifier, backend = inputs(store)
    dispatch_replay(
        store,
        lease,
        owner,
        "CANDIDATE",
        sign(),
        verifier,
        backend,
        expected_version=run.version,
    )
    with psycopg.connect(store.dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"DELETE FROM {table}")
