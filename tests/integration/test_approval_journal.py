"""Durable approvals over synthetic accepted verifier evidence, never live consent."""

from uuid import uuid4

import pytest

from packages.contracts.run import Stage
from services.approval.journal import ApprovalJournal
from services.approval.policy import ApprovalBinding, ApprovalDenied, ReviewPermission
from services.persistence.store import Store, StoreError
from services.persistence.verification import VerificationJournal
from tests.integration import test_postgres
from tests.integration.test_verification_journal import prepared

store = test_postgres.store


def setup(store, outcome="VERIFIED"):
    lease, owner, run, signed, verifier, context = prepared(store, outcome)
    run = VerificationJournal(store).accept(
        lease,
        owner,
        signed,
        verifier,
        image_digest=context.image_digest,
        configuration_ref=context.configuration_ref,
        expected_version=run.version,
    )
    expected = ApprovalBinding(
        run_id=run.run_id,
        repository_id=run.repository_id,
        owner_id=owner,
        source_commit=run.source_commit,
        patch_ref=run.patch_attempt_refs[-1],
        verification_ref=run.verification_ref,
        target_branch="main",
        policy_version=run.policy_version,
    )
    permission = ReviewPermission(
        reviewer_id=uuid4(), repository_id=run.repository_id, owner_id=owner
    )
    return (
        owner,
        run,
        expected,
        permission,
        ApprovalJournal(store, target_branch="main"),
    )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_review_is_durable_exact_and_restart_idempotent(store, decision):
    owner, run, expected, permission, journal = setup(store)
    nonce = uuid4()
    first = journal.record(
        expected,
        permission,
        decision=decision,
        nonce=nonce,
        expected_version=run.version,
    )
    second = ApprovalJournal(Store(store.dsn), target_branch="main").record(
        expected,
        permission,
        decision=decision,
        nonce=nonce,
        expected_version=run.version,
    )
    assert first == second
    assert first.binding == expected and first.reviewer_id == permission.reviewer_id
    assert 0 < (first.expires_at - first.issued_at).total_seconds() <= 900
    state = store.get(run.run_id, owner)
    assert state.version == run.version + 1
    assert state.current_stage == (
        Stage.AWAITING_APPROVAL if decision == "approve" else Stage.CLOSED
    )
    assert not first.can_authorize_publication


def test_publication_claim_consumes_approval_once_and_recovers_same_external_id(store):
    owner, run, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="approve",
        nonce=uuid4(),
        expected_version=run.version,
    )
    first = journal.claim(
        expected, permission, nonce=decision.nonce, expected_version=run.version + 1
    )
    second = ApprovalJournal(Store(store.dsn), target_branch="main").claim(
        expected, permission, nonce=decision.nonce, expected_version=run.version + 1
    )
    assert first == second
    assert first.binding == expected
    assert first.approval_nonce == decision.nonce
    assert first.external_id is not None
    assert store.get(run.run_id, owner).current_stage == Stage.PR_CREATING
    assert store.get(run.run_id, owner).version == run.version + 2


@pytest.mark.parametrize(
    "fault", ["owner", "reviewer", "patch", "source", "target", "version", "unverified"]
)
def test_no_review_on_wrong_identity_binding_or_verdict(store, fault):
    owner, run, expected, permission, journal = setup(
        store, "NOT_VERIFIED" if fault == "unverified" else "VERIFIED"
    )
    if fault == "owner":
        permission = permission.model_copy(update={"owner_id": uuid4()})
    if fault == "reviewer":
        permission = permission.model_copy(update={"repository_id": uuid4()})
    if fault == "patch":
        expected = expected.model_copy(update={"patch_ref": "sha256:" + "f" * 64})
    if fault == "source":
        expected = expected.model_copy(update={"source_commit": "f" * 40})
    if fault == "target":
        expected = expected.model_copy(update={"target_branch": "other"})
    with pytest.raises((StoreError, ApprovalDenied)):
        journal.record(
            expected,
            permission,
            decision="approve",
            nonce=uuid4(),
            expected_version=run.version + (fault == "version"),
        )
    assert store.get(run.run_id, owner) == run


def test_rejection_and_cancellation_cannot_be_consumed(store):
    owner, run, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="reject",
        nonce=uuid4(),
        expected_version=run.version,
    )
    with pytest.raises((StoreError, ApprovalDenied)):
        journal.claim(
            expected, permission, nonce=decision.nonce, expected_version=run.version + 1
        )


@pytest.mark.parametrize(
    "fault", ["cancel", "expired", "scope", "evidence", "nonce", "patch"]
)
def test_publication_rechecks_current_review_and_evidence(store, fault):
    import os

    import psycopg

    owner, run, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="approve",
        nonce=uuid4(),
        expected_version=run.version,
    )
    if fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=run.version + 1)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "expired":
            conn.execute(
                "UPDATE approval_decisions SET "
                "expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
        if fault == "scope":
            conn.execute(
                "UPDATE repository_grants SET "
                "grant_json=jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
        if fault == "evidence":
            conn.execute(
                "UPDATE evidence_bundles SET payload_digest=%s WHERE run_id=%s",
                ("0" * 64, run.run_id),
            )
    changed = (
        expected.model_copy(update={"patch_ref": "sha256:" + "e" * 64})
        if fault == "patch"
        else expected
    )
    with pytest.raises((StoreError, ApprovalDenied)):
        journal.claim(
            changed,
            permission,
            nonce=uuid4() if fault == "nonce" else decision.nonce,
            expected_version=run.version + 1,
        )


def test_concurrent_publication_claims_share_one_id_and_audit_event(store):
    from concurrent.futures import ThreadPoolExecutor

    owner, run, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="approve",
        nonce=uuid4(),
        expected_version=run.version,
    )

    def claim(_):
        return ApprovalJournal(Store(store.dsn), target_branch="main").claim(
            expected, permission, nonce=decision.nonce, expected_version=run.version + 1
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(claim, range(2)))
    assert values[0] == values[1]
    assert [e.event_type for e in store.events(run.run_id, owner)].count(
        "PUBLICATION_CLAIMED"
    ) == 1


def test_approval_audit_failure_rolls_back_decision_and_checkpoint(store, monkeypatch):
    owner, run, expected, permission, journal = setup(store)
    original = Store._event

    def fail(conn, state, kind):
        if kind == "APPROVAL_RECORDED":
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")
        original(conn, state, kind)

    monkeypatch.setattr(Store, "_event", staticmethod(fail))
    with pytest.raises(StoreError):
        journal.record(
            expected,
            permission,
            decision="approve",
            nonce=uuid4(),
            expected_version=run.version,
        )
    assert store.get(run.run_id, owner) == run
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM approval_decisions WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("table", ["approval_decisions", "publication_intents"])
def test_approval_and_consumption_records_are_immutable_for_runtime(store, table):
    import psycopg
    from psycopg import sql

    owner, run, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="approve",
        nonce=uuid4(),
        expected_version=run.version,
    )
    journal.claim(
        expected, permission, nonce=decision.nonce, expected_version=run.version + 1
    )
    for statement in ("UPDATE {} SET digest=digest", "DELETE FROM {}"):
        with psycopg.connect(store.dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql.SQL(statement).format(sql.Identifier(table)))


@pytest.mark.parametrize("review", ["approve", "reject", "claim"])
def test_accepted_repair_memory_survives_review_lifecycle(store, review):
    from packages.contracts.run import RunState
    from services.persistence.memory import RunMemory

    owner, previous, expected, permission, journal = setup(store)
    decision = journal.record(
        expected,
        permission,
        decision="reject" if review == "reject" else "approve",
        nonce=uuid4(),
        expected_version=previous.version,
    )
    if review == "claim":
        journal.claim(
            expected,
            permission,
            nonce=decision.nonce,
            expected_version=previous.version + 1,
        )
    current = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=previous.repository_id,
        source_commit=previous.source_commit,
        policy_version=previous.policy_version,
        trace_id=uuid4(),
    )
    store.create(current, owner, uuid4(), "a" * 64)
    recalled = RunMemory(store).recall(current.run_id, owner)
    assert len(recalled.lessons) == 1
    assert recalled.lessons[0].verification_ref == previous.verification_ref
    assert recalled.lessons[0].outcome == "VERIFIED"
    assert not recalled.can_authorize_execution


def test_memory_does_not_trust_a_review_stage_without_durable_review(store):
    import os

    import psycopg
    from psycopg.types.json import Jsonb

    from packages.contracts.run import RunState
    from services.persistence.memory import RunMemory

    owner, previous, _, _, _ = setup(store)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (
                Jsonb(
                    previous.model_copy(
                        update={"current_stage": Stage.PR_CREATING}
                    ).model_dump(mode="json")
                ),
                previous.run_id,
            ),
        )
    current = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=previous.repository_id,
        source_commit=previous.source_commit,
        policy_version=previous.policy_version,
        trace_id=uuid4(),
    )
    store.create(current, owner, uuid4(), "a" * 64)
    with pytest.raises(StoreError, match="MEMORY_INTEGRITY"):
        RunMemory(store).recall(current.run_id, owner)
