"""Durable verdict contracts with small synthetic signed evidence, not live proof."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from packages.contracts.run import Stage
from services.persistence.evidence import EvidenceStore
from services.persistence.store import Store, StoreError
from services.runner.receipts import canonical
from services.verifier.authenticated import GateDenied, GateVerifier
from tests.integration import test_postgres
from tests.integration.test_evidence_store import prepared as evidence_fixture
from tests.unit.test_authenticated_gates import prepared as gate_fixture

store = test_postgres.store


def prepared(store, outcome="VERIFIED"):
    lease, owner, run, _ = evidence_fixture(store)
    key, signed, context, _, artifacts, verifier = gate_fixture()
    run = run.model_copy(
        update={
            "patch_attempt_refs": (context.patch_ref,),
            "reproduction_test_ref": context.test_ref,
            "baseline_result_ref": "sha256:" + "b" * 64,
        }
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
    run = EvidenceStore(store).retain(
        lease, owner, "a" * 64, artifacts, expected_version=0
    )
    context = context.model_copy(
        update={
            "run_id": run.run_id,
            "repository_id": run.repository_id,
            "source_commit": run.source_commit,
            "run_version": run.version,
            "manifest_ref": run.evidence_manifest_ref,
        }
    )
    gates = tuple(
        g.model_copy(update={"source_commit": run.source_commit})
        for g in signed.payload.gates
    )
    if outcome == "NOT_VERIFIED":
        gates = (*gates[:-1], gates[-1].model_copy(update={"status": "FAIL"}))
    elif outcome == "INCONCLUSIVE":
        gates = gates[:-1]
    signed = signed.model_copy(
        update={
            "payload": signed.payload.model_copy(
                update={"context": context, "gates": gates}
            )
        }
    )
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    return lease, owner, run, signed, verifier, context


@pytest.mark.parametrize("outcome", ["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE"])
def test_verification_journal_atomic_restart_and_duplicate(store, outcome):
    from services.persistence.verification import VerificationJournal

    lease, owner, run, signed, verifier, context = prepared(store, outcome)

    def accept(_):
        return VerificationJournal(Store(store.dsn)).accept(
            lease,
            owner,
            signed,
            verifier,
            image_digest=context.image_digest,
            configuration_ref=context.configuration_ref,
            expected_version=run.version,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(accept, range(2)))
    assert first == second == store.get(run.run_id, owner)
    assert first.current_stage == Stage(outcome)
    assert first.version == run.version + 1
    retained = VerificationJournal(Store(store.dsn)).get(run.run_id, owner)
    assert retained.signed == signed
    assert retained.verdict.status == outcome
    assert not retained.verdict.can_authorize_publication
    assert store.events(run.run_id, owner)[-1].event_type == "STAGE_CHANGED"
    with store._connection() as conn:
        assert conn.execute(
            "SELECT status,token FROM job_leases WHERE run_id=%s", (run.run_id,)
        ).fetchone() == {"status": "done", "token": None}
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM verification_results WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 1
        )


def accept(journal, setup):
    lease, owner, run, signed, verifier, context = setup
    return journal.accept(
        lease,
        owner,
        signed,
        verifier,
        image_digest=context.image_digest,
        configuration_ref=context.configuration_ref,
        expected_version=run.version,
    )


@pytest.mark.parametrize(
    "fault,code",
    [
        ("owner", "NOT_FOUND"),
        ("lease", "LEASE_LOST"),
        ("version", "STALE_VERSION"),
        ("cancel", "EVIDENCE_STAGE_DENIED"),
        ("stage", "EVIDENCE_STAGE_DENIED"),
        ("consent", "EVIDENCE_SCOPE_DENIED"),
        ("patch", "GATE_CONTEXT_DENIED"),
        ("image", "GATE_CONTEXT_DENIED"),
        ("signature", "GATE_SIGNATURE_OR_SCHEMA_INVALID"),
        ("unknown_key", "GATE_KEY_UNKNOWN"),
        ("expiry", "NOT_FOUND"),
        ("tamper", "EVIDENCE_INTEGRITY"),
        ("expired_signature", "GATE_FRESHNESS_DENIED"),
        ("test_transport", "GATE_TEST_EVIDENCE_DENIED"),
    ],
)
def test_verification_journal_denies_without_partial_writes(store, fault, code):
    from services.persistence.verification import VerificationJournal

    setup = list(prepared(store))
    lease, owner, run, signed, _, context = setup
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault in {"cancel", "stage", "patch"}:
            changes = {
                "cancel": {"cancellation_requested": True},
                "stage": {"current_stage": Stage.PATCHING},
                "patch": {"patch_attempt_refs": ("sha256:" + "0" * 64,)},
            }[fault]
            conn.execute(
                "UPDATE runs SET checkpoint=%s WHERE id=%s",
                (
                    Jsonb(run.model_copy(update=changes).model_dump(mode="json")),
                    run.run_id,
                ),
            )
        elif fault == "consent":
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
        elif fault == "expiry":
            conn.execute(
                "UPDATE evidence_bundles SET expires_at=clock_timestamp() "
                "-interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
        elif fault == "tamper":
            conn.execute(
                "UPDATE evidence_bundles SET payload=%s WHERE run_id=%s",
                (b"bad", run.run_id),
            )
    if fault == "owner":
        setup[1] = uuid4()
    elif fault == "lease":
        setup[0] = lease.model_copy(update={"token": uuid4()})
    elif fault == "version":
        setup[2] = run.model_copy(update={"version": 0})
    elif fault == "image":
        setup[5] = context.model_copy(update={"image_digest": "sha256:" + "0" * 64})
    elif fault == "signature":
        setup[3] = signed.model_copy(update={"signature": "0" * 128})
    elif fault == "unknown_key":
        setup[4] = GateVerifier({"other": gate_fixture()[0].public_key()})
    elif fault in {"expired_signature", "test_transport"}:
        # Valid signatures also authenticate forbidden origins or stale time.
        key = gate_fixture()[0]
        payload = signed.payload.model_copy(
            update={"observed_at": signed.payload.observed_at - timedelta(seconds=301)}
            if fault == "expired_signature"
            else {"origin": "TEST_TRANSPORT"}
        )
        changed = signed.model_copy(update={"payload": payload})
        setup[3] = changed.model_copy(
            update={"signature": key.sign(canonical(changed, unsigned=True)).hex()}
        )
        setup[4] = GateVerifier({signed.key_id: key.public_key()})
    before = store.get(run.run_id, owner)
    events = store.events(run.run_id, owner)
    with pytest.raises((StoreError, GateDenied), match=f"^{code}$"):
        accept(VerificationJournal(store), setup)
    assert store.get(run.run_id, owner) == before
    assert store.events(run.run_id, owner) == events
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM verification_results WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("fault", ["audit", "lease_expiry"])
def test_verification_journal_commit_failure_rolls_back(store, monkeypatch, fault):
    from services.persistence.verification import VerificationJournal

    setup = prepared(store)
    _, owner, run, _, _, _ = setup
    if fault == "audit":

        def fail(*args):
            raise StoreError("AUDIT_UNAVAILABLE")

        monkeypatch.setattr(Store, "_event", staticmethod(fail))
    else:
        original_save = Store._save

        def save(conn, updated):
            original_save(conn, updated)
            # Force expiry during this transaction without a real sleep.
            conn.execute(
                "UPDATE job_leases SET leased_until=clock_timestamp()-"
                "interval '1 second' WHERE run_id=%s",
                (updated.run_id,),
            )

        monkeypatch.setattr(Store, "_save", staticmethod(save))
    before_events = store.events(run.run_id, owner)
    with pytest.raises(StoreError, match="AUDIT_UNAVAILABLE|LEASE_LOST"):
        accept(VerificationJournal(store), setup)
    assert store.get(run.run_id, owner) == run
    assert store.events(run.run_id, owner) == before_events
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM verification_results WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize(
    "fault", [None, "auth", "owner", "expiry", "tamper", "missing", "disabled"]
)
def test_verification_journal_private_summary_api(store, fault):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from services.api.app import LocalAuth, create_app
    from services.persistence.verification import VerificationJournal

    setup = prepared(store)
    _, owner, run, signed, _, _ = setup
    accepted = accept(VerificationJournal(store), setup)
    if fault in {"expiry", "tamper"}:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "expiry":
                conn.execute(
                    "UPDATE verification_results SET expires_at=clock_timestamp()-"
                    "interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
            else:
                conn.execute(
                    "UPDATE verification_results SET payload=%s WHERE run_id=%s",
                    (b"tampered", run.run_id),
                )
    token = "synthetic-verification-token-" * 3
    auth = (
        LocalAuth()
        if fault == "disabled"
        else LocalAuth(
            owner_id=uuid4() if fault == "owner" else owner, token=SecretStr(token)
        )
    )
    app = create_app(store, auth)
    requested_id = uuid4() if fault == "missing" else run.run_id
    with TestClient(app, client=("127.0.0.1", 1)) as http:
        response = http.get(
            f"/api/v1/runs/{requested_id}/verification",
            headers={} if fault == "auth" else {"Authorization": "Bearer " + token},
        )
    expected = {
        "auth": 401,
        "owner": 404,
        "expiry": 404,
        "tamper": 409,
        "missing": 404,
        "disabled": 503,
    }.get(fault, 200)
    assert response.status_code == expected
    if expected == 200:
        body = response.json()
        assert body["verification_ref"] == accepted.verification_ref
        assert body["verdict"]["status"] == "VERIFIED"
        assert len(body["gates"]) == 7
        assert body["verdict"]["can_authorize_publication"] is False
        assert response.headers["cache-control"] == "no-store"
        assert "signature" not in body
        assert signed.signature not in response.text


@pytest.mark.parametrize(
    "fault",
    ["conflict", "expired_record", "tampered_record", "read_owner", "permission"],
)
def test_verification_journal_record_protection(store, fault):
    from services.persistence.verification import VerificationJournal

    setup = list(prepared(store))
    _, owner, run, signed, _, context = setup
    journal = VerificationJournal(store)
    accepted = accept(journal, setup)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "expired_record":
            conn.execute(
                "UPDATE verification_results SET expires_at=clock_timestamp()-"
                "interval '1 second' WHERE run_id=%s",
                (run.run_id,),
            )
        elif fault == "tampered_record":
            conn.execute(
                "UPDATE verification_results SET payload=%s WHERE run_id=%s",
                (b"tampered", run.run_id),
            )
    if fault == "conflict":
        setup[5] = context.model_copy(
            update={"configuration_ref": "sha256:" + "0" * 64}
        )
        with pytest.raises(StoreError, match="^VERIFICATION_CONFLICT$"):
            accept(journal, setup)
    elif fault == "expired_record":
        with pytest.raises(StoreError, match="^VERIFICATION_EXPIRED$"):
            accept(journal, setup)
        with pytest.raises(StoreError, match="^NOT_FOUND$"):
            journal.get(run.run_id, owner)
    elif fault == "tampered_record":
        with pytest.raises(StoreError, match="^VERIFICATION_INTEGRITY$"):
            journal.get(run.run_id, owner)
        with pytest.raises(StoreError, match="^VERIFICATION_INTEGRITY$"):
            accept(journal, setup)
    elif fault == "read_owner":
        with pytest.raises(StoreError, match="^NOT_FOUND$"):
            journal.get(run.run_id, uuid4())
    else:
        for statement in (
            "UPDATE verification_results SET payload=payload WHERE run_id=%s",
            "DELETE FROM verification_results WHERE run_id=%s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(store.dsn) as conn:
                    conn.execute(statement, (run.run_id,))
    assert store.get(run.run_id, owner) == accepted
