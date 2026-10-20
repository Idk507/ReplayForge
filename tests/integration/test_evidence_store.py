"""Small private artifact bundles in disposable PostgreSQL; no runner calls."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from packages.contracts.run import Stage
from services.api.app import LocalAuth, create_app
from services.persistence.store import Store, StoreError
from services.verifier.manifest import ArtifactInput
from tests.integration import test_postgres
from tests.integration.test_postgres import baseline_journal_fixture

store = test_postgres.store


def prepared(store):
    _, lease, owner, run, _, _, _, _ = baseline_journal_fixture(store)
    run = run.model_copy(update={"current_stage": Stage.VERIFYING})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
    return (
        lease,
        owner,
        run,
        (
            ArtifactInput(name="test.py", content=b"synthetic; never executed"),
            ArtifactInput(name="binary.bin", content=b"\x00\xff"),
        ),
    )


def test_evidence_bundle_restart_and_concurrent_idempotence(store):
    from services.persistence.evidence import EvidenceStore

    lease, owner, run, artifacts = prepared(store)

    def retain(_):
        return EvidenceStore(store).retain(
            lease, owner, "a" * 64, artifacts, expected_version=0
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(retain, range(2)))
    assert results[0] == results[1]
    assert results[0].version == 1
    manifest, restored = EvidenceStore(Store(store.dsn)).read(
        run.run_id, owner, results[0].evidence_manifest_ref
    )
    assert manifest.manifest_id == results[0].evidence_manifest_ref
    assert {a.name: a.content for a in restored} == {
        a.name: a.content for a in artifacts
    }
    assert store.events(run.run_id, owner)[-1].event_type == "EVIDENCE_RETAINED"


@pytest.mark.parametrize(
    "fault", ["owner", "expiry", "tamper", "stale", "cancel", "stage", "rollback"]
)
def test_evidence_bundle_negative_access_and_transaction_cases(
    store, fault, monkeypatch
):
    from services.persistence.evidence import EvidenceStore

    lease, owner, run, artifacts = prepared(store)
    evidence = EvidenceStore(store)
    if fault in {"owner", "expiry", "tamper"}:
        retained = evidence.retain(
            lease, owner, "a" * 64, artifacts, expected_version=0
        )

        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "expiry":
                conn.execute(
                    "UPDATE evidence_bundles SET expires_at=clock_timestamp()-"
                    "interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
            elif fault == "tamper":
                conn.execute(
                    "UPDATE evidence_bundles SET payload=%s WHERE run_id=%s",
                    (b"tampered", run.run_id),
                )
        with pytest.raises(StoreError):
            evidence.read(
                run.run_id,
                uuid4() if fault == "owner" else owner,
                retained.evidence_manifest_ref,
            )
        if fault == "expiry":
            with pytest.raises(StoreError, match="EVIDENCE_EXPIRED"):
                evidence.retain(lease, owner, "a" * 64, artifacts, expected_version=1)
        return
    if fault == "cancel":
        store.cancel(run.run_id, owner, expected_version=0)
    elif fault == "stage":
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            conn.execute(
                "UPDATE runs SET checkpoint=%s WHERE id=%s",
                (
                    Jsonb(
                        run.model_copy(
                            update={"current_stage": Stage.PATCHING}
                        ).model_dump(mode="json")
                    ),
                    run.run_id,
                ),
            )
    elif fault == "rollback":

        def fail(*args):
            raise StoreError("SYNTHETIC_AUDIT_FAILURE")

        monkeypatch.setattr(Store, "_event", staticmethod(fail))
    with pytest.raises(StoreError):
        evidence.retain(
            lease,
            owner,
            "a" * 64,
            artifacts,
            expected_version=1 if fault == "stale" else 0,
        )
    with psycopg.connect(store.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM evidence_bundles WHERE run_id=%s", (run.run_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "fault", [None, "auth", "owner", "expiry", "tamper", "missing"]
)
def test_evidence_bundle_api_metadata_and_safe_download(store, fault):
    from services.persistence.evidence import EvidenceStore

    lease, owner, run, artifacts = prepared(store)
    evidence = EvidenceStore(store)
    retained = evidence.retain(lease, owner, "a" * 64, artifacts, expected_version=0)
    manifest, _ = evidence.read(run.run_id, owner, retained.evidence_manifest_ref)
    if fault in {"expiry", "tamper"}:
        with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
            if fault == "expiry":
                conn.execute(
                    "UPDATE evidence_bundles SET expires_at=clock_timestamp()-"
                    "interval '1 second' WHERE run_id=%s",
                    (run.run_id,),
                )
            else:
                conn.execute(
                    "UPDATE evidence_bundles SET payload=%s WHERE run_id=%s",
                    (b"tampered", run.run_id),
                )
    token = "synthetic-evidence-token-" * 3
    app = create_app(
        store,
        LocalAuth(
            token=SecretStr(token), owner_id=uuid4() if fault == "owner" else owner
        ),
    )
    with TestClient(app, client=("127.0.0.1", 1)) as http:
        headers = {} if fault == "auth" else {"Authorization": "Bearer " + token}
        base = f"/api/v1/runs/{run.run_id}/evidence/{manifest.manifest_id}"
        response = http.get(base, headers=headers)
        expected = {"auth": 401, "owner": 404, "expiry": 404, "tamper": 409}.get(
            fault, 200
        )
        assert response.status_code == expected
        if expected != 200:
            denied = http.get(
                base + "/artifacts/" + manifest.entries[0].artifact_id, headers=headers
            )
            assert denied.status_code == expected
            return
        assert response.json() == manifest.model_dump(mode="json")
        assert "content_b64" not in response.text
        assert response.headers["cache-control"] == "no-store"
        reference = (
            "sha256:" + "0" * 64
            if fault == "missing"
            else manifest.entries[0].artifact_id
        )
        downloaded = http.get(base + "/artifacts/" + reference, headers=headers)
        if fault == "missing":
            assert downloaded.status_code == 404
        else:
            assert downloaded.status_code == 200 and downloaded.content == b"\x00\xff"
            assert downloaded.headers["content-type"] == "application/octet-stream"
            assert downloaded.headers["x-content-type-options"] == "nosniff"
            assert downloaded.headers["cache-control"] == "no-store"
            assert (
                downloaded.headers["content-disposition"]
                == 'attachment; filename="binary.bin"'
            )


def test_evidence_bundle_runtime_cannot_mutate_or_delete(store):
    from services.persistence.evidence import EvidenceStore

    lease, owner, run, artifacts = prepared(store)
    EvidenceStore(store).retain(lease, owner, "a" * 64, artifacts, expected_version=0)
    for statement in (
        "UPDATE evidence_bundles SET payload=payload WHERE run_id=%s",
        "DELETE FROM evidence_bundles WHERE run_id=%s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(store.dsn) as conn:
                conn.execute(statement, (run.run_id,))
