"""Collection dispatch journal and operator reconciliation on real PostgreSQL.

Synthetic source transport only; no GitHub, provider or repository execution.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from packages.contracts.run import Stage
from services.orchestrator.source_broker import (
    BrokerClient,
    BrokerError,
    dispatch_once,
)
from services.persistence.collection_journal import CollectionJournal, DispatchState
from services.persistence.store import Lease, Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_collection_broker import prepared

store = test_postgres.store
OPERATOR = "ops.local"
REASON = "operator reviewed durable journal"


def allow(owner, repository_id):
    return None


def deny(owner, repository_id):
    raise PermissionError("consent revoked")


def requeue_job(store, run_id):
    """Return a prepared run's job to the never-claimed state (test setup only)."""
    with store._connection() as db:
        db.execute(
            "UPDATE job_leases SET status='queued',worker_id=NULL,token=NULL,"
            "leased_until=NULL,attempt_count=0 WHERE run_id=%s",
            (run_id,),
        )


def expire(store, run_id):
    with store._connection() as db:
        db.execute(
            "UPDATE job_leases SET leased_until=clock_timestamp()-interval '1 second' "
            "WHERE run_id=%s",
            (run_id,),
        )


def broker_client(c, *, fail=None):
    internal = TestClient(
        c.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    )
    posts = []

    def handle(req):
        if req.method == "POST":
            posts.append(req.url.path)
            if fail is not None:
                raise fail
        response = internal.request(
            req.method, str(req.url), headers=dict(req.headers), content=req.content
        )
        return httpx.Response(response.status_code, content=response.content)

    return (
        BrokerClient(
            "http://127.0.0.1:8000",
            SecretStr("s" * 43),
            transport=httpx.MockTransport(handle),
        ),
        posts,
    )


def claimed(store):
    c, j, session, broker, payload, fetched = prepared(store)
    lease = Lease.model_validate(payload["lease"])
    return c, session, lease, payload, fetched


def events(store, run_id, session):
    return [e.event_type for e in store.events(run_id, session.user_id)]


def audit_rows(store, run_id):
    with store._connection() as db:
        return db.execute(
            "SELECT action,observed_state,operator FROM collection_reconciliations "
            "WHERE run_id=%s ORDER BY created_at",
            (run_id,),
        ).fetchall()


def test_crash_before_dispatch_is_proven_unsent_and_requeue_is_audited(store):
    _c, session, lease, payload, fetched = claimed(store)
    journal = CollectionJournal(store)
    expire(store, lease.run_id)
    assert journal.observe(lease.run_id).state == DispatchState.CLAIMED_NO_INTENT
    result = journal.reconcile(
        lease.run_id,
        session.user_id,
        "REQUEUE",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    assert result.job_status == "queued" and not fetched
    again = store.claim(uuid4(), first_attempt_only=True)
    assert again is not None and again.run_id == lease.run_id
    assert store.collection_first_claim(again)
    rows = audit_rows(store, lease.run_id)
    assert [(r["action"], r["observed_state"]) for r in rows] == [
        ("REQUEUE", "CLAIMED_NO_INTENT")
    ]


def test_crash_after_intent_before_send_is_proven_unsent(store):
    _c, session, lease, payload, _fetched = claimed(store)
    journal = CollectionJournal(store)
    operation_id = journal.record_intent(lease, payload["expected_version"])
    expire(store, lease.run_id)
    obs = journal.observe(lease.run_id)
    assert obs.state == DispatchState.INTENT_NOT_SENT
    assert obs.operation_id == operation_id
    journal.reconcile(
        lease.run_id,
        session.user_id,
        "REQUEUE",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    with store._connection() as db:  # intent evidence is never erased
        assert (
            db.execute(
                "SELECT count(*) AS n FROM collection_dispatches WHERE operation_id=%s",
                (operation_id,),
            ).fetchone()["n"]
            == 1
        )


def test_second_intent_for_one_claim_is_refused(store):
    _c, _session, lease, payload, _fetched = claimed(store)
    journal = CollectionJournal(store)
    journal.record_intent(lease, payload["expected_version"])
    with pytest.raises(StoreError, match="DISPATCH_ALREADY_RECORDED"):
        journal.record_intent(lease, payload["expected_version"])


def sent_uncertain(store):
    c, session, lease, payload, fetched = claimed(store)
    journal = CollectionJournal(store)
    operation_id = journal.record_intent(lease, payload["expected_version"])
    journal.mark_sent(lease, operation_id)
    expire(store, lease.run_id)
    return session, lease, journal, operation_id, fetched


def test_provider_accepted_but_unpersisted_blocks_blind_requeue(store):
    session, lease, journal, operation_id, fetched = sent_uncertain(store)
    assert journal.observe(lease.run_id).state == DispatchState.SENT_UNCERTAIN
    with pytest.raises(StoreError, match="RECONCILIATION_BLOCKED:UNCERTAIN"):
        journal.reconcile(
            lease.run_id,
            session.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
        )
    with store._connection() as db:
        job = db.execute(
            "SELECT status,attempt_count FROM job_leases WHERE run_id=%s",
            (lease.run_id,),
        ).fetchone()
    assert (job["status"], job["attempt_count"]) == ("leased", 1)
    assert [r["action"] for r in audit_rows(store, lease.run_id)] == ["BLOCKED"]
    assert not fetched


def test_uncertain_requeue_needs_read_only_replay_acceptance_and_unchanged_run(store):
    session, lease, journal, _op, _fetched = sent_uncertain(store)
    with store._connection() as db:  # the run moved since the intent was recorded
        db.execute(
            "UPDATE runs SET checkpoint=jsonb_set(checkpoint,'{version}',"
            "to_jsonb((checkpoint->>'version')::int+1)),version=version+1 "
            "WHERE id=%s",
            (lease.run_id,),
        )
    with pytest.raises(StoreError, match="REPLAY_NOT_PROVEN_SAFE"):
        journal.reconcile(
            lease.run_id,
            session.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
            accept_read_only_replay=True,
        )


def test_uncertain_requeue_with_explicit_read_only_acceptance_fences_old_lease(store):
    session, lease, journal, _op, _fetched = sent_uncertain(store)
    journal.reconcile(
        lease.run_id,
        session.user_id,
        "REQUEUE",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
        accept_read_only_replay=True,
    )
    with pytest.raises(StoreError, match="LEASE_LOST"):  # old claim is fenced out
        store.claimed_run(lease)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        journal.mark_sent(lease, uuid4())


def test_fail_moves_to_inconclusive_and_keeps_history(store):
    session, lease, journal, operation_id, _fetched = sent_uncertain(store)
    result = journal.reconcile(
        lease.run_id,
        session.user_id,
        "FAIL",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    assert result.stage == Stage.INCONCLUSIVE and result.job_status == "done"
    state = store.get(lease.run_id, session.user_id)
    assert state.current_stage == Stage.INCONCLUSIVE
    assert state.failure_reason == "SOURCE_UNAVAILABLE"
    names = events(store, lease.run_id, session)
    assert "LEASE_CLAIMED" in names and names[-1] == "STAGE_CHANGED"
    with store._connection() as db:
        kinds = [
            r["kind"]
            for r in db.execute(
                "SELECT kind FROM collection_dispatch_events WHERE operation_id=%s",
                (operation_id,),
            ).fetchall()
        ]
    assert kinds == ["SENT"]
    assert audit_rows(store, lease.run_id)[0]["action"] == "FAIL"


def test_active_lease_cannot_be_reconciled(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    with pytest.raises(StoreError, match="LEASE_ACTIVE"):
        CollectionJournal(store).reconcile(
            lease.run_id,
            session.user_id,
            "FAIL",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
        )


def test_concurrent_operators_produce_exactly_one_decision(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    expire(store, lease.run_id)

    def attempt(_):
        try:
            CollectionJournal(Store(store.dsn)).reconcile(
                lease.run_id,
                session.user_id,
                "REQUEUE",
                operator=OPERATOR,
                reason=REASON,
                authorize=allow,
            )
            return "OK"
        except StoreError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, range(2)))
    assert outcomes[0] == "OK" and outcomes[1].startswith("RECONCILIATION_BLOCKED")
    actions = sorted(r["action"] for r in audit_rows(store, lease.run_id))
    assert actions == ["BLOCKED", "REQUEUE"]


def test_revoked_consent_blocks_requeue_and_leaves_job_untouched(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    expire(store, lease.run_id)
    with pytest.raises(StoreError, match="RECONCILIATION_BLOCKED"):
        CollectionJournal(store).reconcile(
            lease.run_id,
            session.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=deny,
        )
    with store._connection() as db:
        status = db.execute(
            "SELECT status FROM job_leases WHERE run_id=%s", (lease.run_id,)
        ).fetchone()["status"]
    assert status == "leased"
    assert "CONSENT_DENIED" in _reason_of_last_block(store, lease.run_id)


def _reason_of_last_block(store, run_id) -> str:
    with store._connection() as db:
        return db.execute(
            "SELECT reason FROM collection_reconciliations WHERE run_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()["reason"]


@pytest.mark.parametrize(
    ("operator", "reason", "owner_is_stranger", "message"),
    [
        ("x y", REASON, False, "OPERATOR_INVALID"),
        ("ops.local", "short", False, "REASON_INVALID"),
        ("ops.local", REASON, True, "NOT_FOUND"),
    ],
)
def test_invalid_operator_input_or_foreign_owner_is_denied(
    store, operator, reason, owner_is_stranger, message
):
    _c, session, lease, _payload, _fetched = claimed(store)
    expire(store, lease.run_id)
    owner = uuid4() if owner_is_stranger else session.user_id
    with pytest.raises(StoreError, match=message):
        CollectionJournal(store).reconcile(
            lease.run_id,
            owner,
            "FAIL",
            operator=operator,
            reason=reason,
            authorize=allow,
        )
    assert audit_rows(store, lease.run_id) == []


def test_successful_dispatch_is_journaled_and_cannot_be_reconciled(store):
    c, session, lease, payload, fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    client, posts = broker_client(c)
    with client:
        result = dispatch_once(store, uuid4(), client, ("README.md",))
    assert result.stage == Stage.TRIAGING and len(posts) == 1 and len(fetched) == 1
    journal = CollectionJournal(store)
    assert journal.observe(run_id).state == DispatchState.COMPLETED
    with store._connection() as db:
        kinds = {
            r["kind"]
            for r in db.execute(
                "SELECT e.kind FROM collection_dispatch_events e JOIN "
                "collection_dispatches d USING (operation_id) WHERE d.run_id=%s",
                (run_id,),
            ).fetchall()
        }
    assert kinds == {"SENT", "COMPLETED"}
    expire(store, run_id)
    with pytest.raises(StoreError, match="RECONCILIATION_BLOCKED:COMPLETED"):
        journal.reconcile(
            run_id,
            session.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
        )


def test_unconfirmed_dispatch_stays_uncertain_and_polling_never_resends(store):
    c, session, lease, payload, fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    client, posts = broker_client(c, fail=httpx.ConnectError("lost response"))
    with client:
        with pytest.raises(BrokerError, match="COLLECTION_UNCONFIRMED"):
            dispatch_once(store, uuid4(), client, ("README.md",))
        expire(store, run_id)
        for _ in range(3):  # duplicate worker polling, including after restart
            assert (
                dispatch_once(store, uuid4(), client, ("README.md",)).status == "IDLE"
            )
    assert len(posts) == 1 and not fetched
    restarted = CollectionJournal(Store(store.dsn))  # fresh process view
    assert restarted.observe(run_id).state == DispatchState.SENT_UNCERTAIN
    assert [o.run_id for o in restarted.candidates()] == [run_id]


def test_broker_refusal_is_recorded_as_rejected_and_requeue_is_allowed(store):
    c, session, lease, payload, fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    client, posts = broker_client(c)
    with store._connection() as db:  # make the broker refuse: stale expected version
        db.execute(
            "UPDATE runs SET checkpoint=jsonb_set("
            "checkpoint,'{cancellation_requested}',"
            "'true'::jsonb) WHERE id=%s",
            (run_id,),
        )
    with client:
        with pytest.raises(BrokerError, match="COLLECTION_DENIED"):
            dispatch_once(store, uuid4(), client, ("README.md",))
    assert not fetched
    expire(store, run_id)
    journal = CollectionJournal(store)
    assert journal.observe(run_id).state == DispatchState.REJECTED
    with store._connection() as db:
        db.execute(
            "UPDATE runs SET checkpoint=jsonb_set("
            "checkpoint,'{cancellation_requested}',"
            "'false'::jsonb) WHERE id=%s",
            (run_id,),
        )
    result = journal.reconcile(
        run_id,
        session.user_id,
        "REQUEUE",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    assert result.observed_state == DispatchState.REJECTED


def test_cancelled_run_is_not_reconcilable(store):
    _c, session, lease, payload, _fetched = claimed(store)
    store.cancel(
        lease.run_id, session.user_id, expected_version=payload["expected_version"]
    )
    expire(store, lease.run_id)
    with pytest.raises(StoreError, match="RECONCILIATION_BLOCKED"):
        CollectionJournal(store).reconcile(
            lease.run_id,
            session.user_id,
            "REQUEUE",
            operator=OPERATOR,
            reason=REASON,
            authorize=allow,
        )


def test_run_ids_in_listing_belong_to_pre_collection_runs_only(store):
    _c, _session, lease, _payload, _fetched = claimed(store)
    expire(store, lease.run_id)
    listed = CollectionJournal(store).candidates()
    assert lease.run_id in {o.run_id for o in listed}
    assert all(isinstance(o.run_id, UUID) for o in listed)


def test_local_collection_lifecycle_survives_refresh_and_restart(store):
    """Create -> worker claim -> collect -> manifest -> timeline, re-read cold."""
    c, session, lease, payload, fetched = claimed(store)
    run_id = lease.run_id
    requeue_job(store, run_id)
    client, posts = broker_client(c)
    with client:
        result = dispatch_once(store, uuid4(), client, ("README.md",))
    assert result.stage == Stage.TRIAGING and len(posts) == 1
    cold = Store(store.dsn)  # a fresh process: nothing cached from the worker
    state = cold.get(run_id, session.user_id)
    assert state.current_stage == Stage.TRIAGING and state.source_manifest_ref
    manifest = c.get(
        f"/api/v1/runs/{run_id}/source-manifests/{state.source_manifest_ref}"
    )
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["source_commit"] == state.source_commit
    assert [f["path"] for f in body["files"]] == ["README.md"]
    assert body["evidence_origin"] == "TEST_TRANSPORT"  # synthetic, never live
    names = events(cold, run_id, session)
    assert names[0] == "CREATED" and "LEASE_CLAIMED" in names
    assert names[-1] == "STAGE_CHANGED"
    assert c.get(f"/api/v1/runs/{run_id}/events").status_code == 200


def test_failed_collection_is_visible_and_consent_loss_never_fetches(store):
    c, _j, session, broker, payload, fetched = prepared(store)
    run_id = UUID(payload["lease"]["run_id"])
    requeue_job(store, run_id)
    state = store.get(run_id, session.user_id)
    broker.access.disconnect(session.user_id, state.repository_id)
    client, posts = broker_client(c)
    with client:
        with pytest.raises(BrokerError, match="COLLECTION_DENIED"):
            dispatch_once(store, uuid4(), client, ("README.md",))
    assert not fetched
    after = store.get(run_id, session.user_id)
    assert after.current_stage != Stage.TRIAGING and after.source_manifest_ref is None
    assert "LEASE_CLAIMED" in events(store, run_id, session)
    assert CollectionJournal(store).observe(run_id).state == DispatchState.REJECTED


def backdate(store, run_id, seconds):
    with store._connection() as db:
        db.execute(
            "UPDATE runs SET created_at=clock_timestamp()-make_interval(secs=>%s) "
            "WHERE id=%s",
            (seconds, run_id),
        )


def test_diagnostics_distinguish_waiting_from_an_unclaimed_stale_queue(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    journal = CollectionJournal(store)
    young = journal.diagnose(lease.run_id, session.user_id)
    assert (young.code, young.job_status, young.attempt) == (
        "WAITING_FOR_WORKER",
        "queued",
        0,
    )
    assert not young.operator_action_required
    backdate(store, lease.run_id, 600)
    stale = journal.diagnose(lease.run_id, session.user_id)
    assert stale.code == "WORKER_NOT_CLAIMING" and stale.age_seconds >= 600


def test_diagnostics_follow_the_claim_and_never_hide_uncertainty(store):
    _c, session, lease, payload, _fetched = claimed(store)
    journal = CollectionJournal(store)
    live = journal.diagnose(lease.run_id, session.user_id)
    assert live.code == "COLLECTING" and (live.lease_seconds_left or 0) > 0
    op = journal.record_intent(lease, payload["expected_version"])
    journal.mark_sent(lease, op)
    expire(store, lease.run_id)
    stuck = journal.diagnose(lease.run_id, session.user_id)
    assert stuck.code == "NEEDS_RECONCILIATION" and stuck.operator_action_required
    assert stuck.dispatch_state == DispatchState.SENT_UNCERTAIN and stuck.attempt == 1
    journal.reconcile(
        lease.run_id,
        session.user_id,
        "FAIL",
        operator=OPERATOR,
        reason=REASON,
        authorize=allow,
    )
    assert journal.diagnose(lease.run_id, session.user_id).code == "TERMINAL"


def test_diagnostics_report_progress_only_after_the_run_advanced(store):
    c, session, lease, _payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    client, _posts = broker_client(c)
    with client:
        dispatch_once(store, uuid4(), client, ("README.md",))
    assert (
        CollectionJournal(store).diagnose(lease.run_id, session.user_id).code
        == "ADVANCED"
    )


def test_diagnostics_api_is_owner_scoped_and_read_only(store):
    c, session, lease, _payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    r = c.get(f"/api/v1/runs/{lease.run_id}/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "WAITING_FOR_WORKER" and body["run_id"] == str(lease.run_id)
    assert set(body) == {
        "run_id",
        "stage",
        "code",
        "job_status",
        "attempt",
        "age_seconds",
        "lease_seconds_left",
        "dispatch_state",
        "operator_action_required",
    }
    assert c.post(f"/api/v1/runs/{lease.run_id}/diagnostics").status_code in {404, 405}
    with pytest.raises(StoreError, match="NOT_FOUND"):
        CollectionJournal(store).diagnose(lease.run_id, uuid4())
