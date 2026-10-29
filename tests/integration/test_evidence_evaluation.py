"""Checkpoint-bound evaluation using tiny synthetic authority, never live proof."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from services.persistence.evidence import EvidenceStore
from services.persistence.store import StoreError
from services.runner.receipts import canonical
from services.verifier.authenticated import GateDenied
from tests.integration import test_postgres
from tests.integration.test_evidence_store import prepared
from tests.unit.test_authenticated_gates import prepared as gate_fixture

store = test_postgres.store


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "owner",
        "lease",
        "version",
        "cancel",
        "patch",
        "test",
        "image",
        "configuration",
        "signature",
        "expiry",
        "tamper",
        "consent",
    ],
)
def test_checkpoint_bound_evidence_evaluation(store, fault):
    lease, owner, run, _ = prepared(store)
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
    evidence = EvidenceStore(store)
    run = evidence.retain(lease, owner, "a" * 64, artifacts, expected_version=0)
    context = context.model_copy(
        update={
            "run_id": run.run_id,
            "repository_id": run.repository_id,
            "source_commit": run.source_commit,
            "run_version": run.version,
            "manifest_ref": run.evidence_manifest_ref,
        }
    )
    payload = signed.payload.model_copy(
        update={
            "context": context,
            "gates": tuple(
                g.model_copy(update={"source_commit": run.source_commit})
                for g in signed.payload.gates
            ),
        }
    )
    signed = signed.model_copy(update={"payload": payload})
    signed = signed.model_copy(
        update={
            "signature": key.sign(canonical(signed, unsigned=True)).hex(),
        }
    )
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault in {"patch", "test", "cancel"}:
            changes = {
                "patch": {"patch_attempt_refs": ("sha256:" + "0" * 64,)},
                "test": {"reproduction_test_ref": "sha256:" + "0" * 64},
                "cancel": {"cancellation_requested": True},
            }[fault]
            conn.execute(
                "UPDATE runs SET checkpoint=%s WHERE id=%s",
                (
                    Jsonb(run.model_copy(update=changes).model_dump(mode="json")),
                    run.run_id,
                ),
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
                (b"tampered", run.run_id),
            )
        elif fault == "consent":
            conn.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false') WHERE id=%s",
                (run.repository_id,),
            )
    if fault == "signature":
        signed = signed.model_copy(update={"signature": "0" * 128})

    def evaluate():
        return evidence.evaluate(
            lease.model_copy(update={"token": uuid4()}) if fault == "lease" else lease,
            uuid4() if fault == "owner" else owner,
            signed,
            verifier,
            image_digest="sha256:" + "0" * 64
            if fault == "image"
            else context.image_digest,
            configuration_ref="sha256:" + "0" * 64
            if fault == "configuration"
            else context.configuration_ref,
            phase_bindings=context.phase_bindings,
            expected_version=0 if fault == "version" else 1,
        )

    before = store.get(run.run_id, owner)
    events = store.events(run.run_id, owner)
    if fault:
        code = {
            "owner": "LEASE_LOST",
            "lease": "LEASE_LOST",
            "version": "STALE_VERSION",
            "cancel": "EVIDENCE_STAGE_DENIED",
            "patch": "GATE_CONTEXT_DENIED",
            "test": "GATE_CONTEXT_DENIED",
            "image": "GATE_CONTEXT_DENIED",
            "configuration": "GATE_CONTEXT_DENIED",
            "signature": "GATE_SIGNATURE_OR_SCHEMA_INVALID",
            "expiry": "NOT_FOUND",
            "tamper": "EVIDENCE_INTEGRITY",
            "consent": "EVIDENCE_SCOPE_DENIED",
        }[fault]
        with pytest.raises((StoreError, GateDenied), match=f"^{code}$"):
            evaluate()
    else:
        result = evaluate()
        assert result.status == "VERIFIED"
        assert result.context == context
        assert not result.can_authorize_publication
    assert store.get(run.run_id, owner) == before
    assert store.events(run.run_id, owner) == events
