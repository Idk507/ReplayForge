"""Durable approval/nonce reducer, internal to an independently authenticated API.

ReviewPermission MUST be supplied by the trusted human identity/membership
adapter, never deserialized as browser/model proof. This module implements no
login, public endpoint, GitHub calls or deployment.
"""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal, TypeVar
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit
from services.approval.policy import (
    ApprovalBinding,
    ApprovalDenied,
    HumanDecision,
    ReviewPermission,
    validate_decision,
)
from services.persistence.evidence import _decode as decode_evidence
from services.persistence.store import Store, StoreError
from services.persistence.verification import _decode as decode_verification
from services.runner.receipts import canonical

T = TypeVar("T", bound=BaseModel)


class PublicationIntent(BaseModel):
    """Immutable idempotency identity; not a transferable publication permit."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    external_id: UUID
    approval_nonce: UUID
    binding: ApprovalBinding
    reviewer_id: UUID
    head_branch: str = Field(pattern=r"^replayforge/[a-f0-9]{32}-[a-f0-9]{12}$")
    can_authorize_publication: Literal[False] = False


def _decode(row: dict[str, Any], model: type[T]) -> T:
    raw = row["payload"]
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ApprovalDenied("APPROVAL_RECORD_INTEGRITY")
    body = bytes(raw)
    if not 1 <= len(body) <= 16384 or sha256(body).hexdigest() != row["digest"]:
        raise ApprovalDenied("APPROVAL_RECORD_INTEGRITY")
    try:
        value = model.model_validate_json(body)
        if canonical(value) != body:
            raise ApprovalDenied("APPROVAL_RECORD_INTEGRITY")
        return value
    except (ValidationError, ValueError):
        raise ApprovalDenied("APPROVAL_RECORD_INTEGRITY") from None


class ApprovalJournal:
    """One immutable human decision and publication claim per verified run.

    target_branch is trusted operator policy, not browser-selected policy.
    Current reviewer membership must be independently loaded before EVERY call.
    No public route is enabled until that adapter is integrated by the auth owner.
    """

    def __init__(self, store: Store, *, target_branch: str) -> None:
        self.store, self.target = store, target_branch

    def _current(
        self,
        conn: psycopg.Connection[dict[str, Any]],
        expected: ApprovalBinding,
        permission: ReviewPermission,
    ) -> tuple[RunState, datetime, datetime]:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (expected.run_id, expected.owner_id),
        ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        run = RunState.model_validate(row["checkpoint"])
        if (
            run.cancellation_requested
            or not run.patch_attempt_refs
            or run.verification_ref is None
            or expected
            != ApprovalBinding(
                run_id=run.run_id,
                repository_id=run.repository_id,
                owner_id=expected.owner_id,
                source_commit=run.source_commit,
                patch_ref=run.patch_attempt_refs[-1],
                verification_ref=run.verification_ref,
                target_branch=self.target,
                policy_version=run.policy_version,
            )
        ):
            raise ApprovalDenied("APPROVAL_BINDING_DENIED")
        if (
            permission.repository_id != run.repository_id
            or permission.owner_id != expected.owner_id
        ):
            raise ApprovalDenied("APPROVAL_REVIEWER_DENIED")
        scope = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, expected.owner_id),
        ).fetchone()
        if (
            scope is None
            or not admit(
                RepositoryGrant.model_validate(scope["grant_json"]),
                expected.owner_id,
                run.repository_id,
                run.source_commit,
            ).allowed
        ):
            raise ApprovalDenied("APPROVAL_SCOPE_DENIED")
        verification = conn.execute(
            "SELECT v.payload,v.digest,clock_timestamp() AS "
            "now,LEAST(v.expires_at,e.expires_at) AS expiry FROM "
            "verification_results v JOIN evidence_bundles e ON "
            "e.run_id=v.run_id WHERE v.run_id=%s AND e.manifest_digest=%s AND "
            "v.expires_at>clock_timestamp() AND e.expires_at>clock_timestamp()",
            (run.run_id, str(run.evidence_manifest_ref).removeprefix("sha256:")),
        ).fetchone()
        if (
            verification is None
            or decode_verification(verification, run).verdict.status != "VERIFIED"
        ):
            raise ApprovalDenied("APPROVAL_VERIFIED_EVIDENCE_REQUIRED")
        evidence = conn.execute(
            "SELECT payload,payload_digest,manifest_digest FROM evidence_bundles "
            "WHERE run_id=%s AND manifest_digest=%s "
            "AND expires_at>clock_timestamp()",
            (run.run_id, str(run.evidence_manifest_ref).removeprefix("sha256:")),
        ).fetchone()
        if evidence is None:
            raise ApprovalDenied("APPROVAL_VERIFIED_EVIDENCE_REQUIRED")
        bundle, _ = decode_evidence(evidence)
        if bundle.run_id != run.run_id or bundle.source_commit != run.source_commit:
            raise ApprovalDenied("APPROVAL_VERIFIED_EVIDENCE_REQUIRED")
        now, expiry = verification["now"], verification["expiry"]
        if not isinstance(now, datetime) or not isinstance(expiry, datetime):
            raise ApprovalDenied("APPROVAL_CLOCK_INVALID")
        return run, now, expiry

    def record(
        self,
        expected: ApprovalBinding,
        permission: ReviewPermission,
        *,
        decision: Literal["approve", "reject"],
        nonce: UUID,
        expected_version: int,
    ) -> HumanDecision:
        expected = ApprovalBinding.model_validate(expected.model_dump())
        permission = ReviewPermission.model_validate(permission.model_dump())
        if (
            decision not in {"approve", "reject"}
            or not isinstance(nonce, UUID)
            or type(expected_version) is not int
        ):
            raise ApprovalDenied("APPROVAL_REQUEST_INVALID")
        with self.store._connection() as conn:
            run, now, expiry = self._current(conn, expected, permission)
            prior = conn.execute(
                "SELECT payload,digest,from_version,expires_at>clock_timestamp() "
                "AS active FROM approval_decisions WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            stage = Stage.AWAITING_APPROVAL if decision == "approve" else Stage.CLOSED
            if prior is not None:
                cached = _decode(prior, HumanDecision)
                if (
                    not prior["active"]
                    or cached.binding != expected
                    or cached.reviewer_id != permission.reviewer_id
                    or cached.nonce != nonce
                    or cached.decision != decision
                    or prior["from_version"] != expected_version
                    or run.version != expected_version + 1
                    or run.current_stage != stage
                ):
                    raise ApprovalDenied("APPROVAL_RECORD_CONFLICT")
                return cached
            if run.current_stage != Stage.VERIFIED or run.version != expected_version:
                raise ApprovalDenied("APPROVAL_STAGE_OR_VERSION_DENIED")
            value = HumanDecision(
                binding=expected,
                reviewer_id=permission.reviewer_id,
                decision=decision,
                nonce=nonce,
                issued_at=now,
                expires_at=min(now + timedelta(seconds=900), expiry),
            )
            if decision == "approve":
                validate_decision(value, expected, permission, now=now, consumed=False)
            body = canonical(value)
            conn.execute(
                "INSERT INTO "
                "approval_decisions(run_id,nonce,digest,payload,"
                "from_version,expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    run.run_id,
                    nonce,
                    sha256(body).hexdigest(),
                    body,
                    expected_version,
                    value.expires_at,
                ),
            )
            updated = run.model_copy(
                update={"current_stage": stage, "version": run.version + 1}
            )
            self.store._save(conn, updated)
            self.store._event(conn, updated, "APPROVAL_RECORDED")
            return value

    def claim(
        self,
        expected: ApprovalBinding,
        permission: ReviewPermission,
        *,
        nonce: UUID,
        expected_version: int,
    ) -> PublicationIntent:
        expected = ApprovalBinding.model_validate(expected.model_dump())
        permission = ReviewPermission.model_validate(permission.model_dump())
        if not isinstance(nonce, UUID) or type(expected_version) is not int:
            raise ApprovalDenied("APPROVAL_REQUEST_INVALID")
        with self.store._connection() as conn:
            run, now, _ = self._current(conn, expected, permission)
            row = conn.execute(
                "SELECT payload,digest,expires_at>clock_timestamp() AS active FROM "
                "approval_decisions WHERE run_id=%s AND nonce=%s",
                (run.run_id, nonce),
            ).fetchone()
            if row is None or not row["active"]:
                raise ApprovalDenied("APPROVAL_NONCE_UNAVAILABLE")
            decision = _decode(row, HumanDecision)
            if decision.nonce != nonce:
                raise ApprovalDenied("APPROVAL_NONCE_UNAVAILABLE")
            # Validate current identity/bindings/time even for an existing intent.
            # Idempotent reconciliation returns the already-consumed intent; it
            # cannot insert a second nonce consumption or select another branch.
            validate_decision(decision, expected, permission, now=now, consumed=False)
            prior = conn.execute(
                "SELECT payload,digest,external_id,approval_nonce,from_version "
                "FROM publication_intents WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()
            if prior is not None:
                cached = _decode(prior, PublicationIntent)
                if (
                    cached.binding != expected
                    or cached.head_branch
                    != f"replayforge/{run.run_id.hex}-{expected.patch_ref[-12:]}"
                    or cached.reviewer_id != permission.reviewer_id
                    or cached.approval_nonce != nonce
                    or cached.external_id != prior["external_id"]
                    or cached.approval_nonce != prior["approval_nonce"]
                    or prior["from_version"] != expected_version
                    or run.version != expected_version + 1
                    or run.current_stage != Stage.PR_CREATING
                ):
                    raise ApprovalDenied("PUBLICATION_INTENT_CONFLICT")
                return cached
            if (
                run.current_stage != Stage.AWAITING_APPROVAL
                or run.version != expected_version
            ):
                raise ApprovalDenied("APPROVAL_STAGE_OR_VERSION_DENIED")
            value = PublicationIntent(
                external_id=uuid4(),
                approval_nonce=nonce,
                binding=expected,
                reviewer_id=permission.reviewer_id,
                head_branch=f"replayforge/{run.run_id.hex}-{expected.patch_ref[-12:]}",
            )
            body = canonical(value)
            conn.execute(
                "INSERT INTO "
                "publication_intents(run_id,approval_nonce,external_id,"
                "digest,payload,from_version) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    run.run_id,
                    nonce,
                    value.external_id,
                    sha256(body).hexdigest(),
                    body,
                    expected_version,
                ),
            )
            updated = run.model_copy(
                update={"current_stage": Stage.PR_CREATING, "version": run.version + 1}
            )
            self.store._save(conn, updated)
            self.store._event(conn, updated, "PUBLICATION_CLAIMED")
            return value
