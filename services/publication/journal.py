"""Private result custody and exact publication input loading, without HTTP."""

from hashlib import sha256
from typing import Any
from uuid import UUID

from packages.contracts.run import RunState, Stage
from services.approval.journal import ApprovalJournal, PublicationIntent, _decode
from services.approval.policy import (
    ApprovalBinding,
    ApprovalDenied,
    HumanDecision,
    ReviewPermission,
)
from services.persistence.evidence import EvidenceStore
from services.persistence.store import Store, StoreError
from services.publication.github import PublicationWork, PullRequestResult
from services.publication.patch import patch_paths
from services.runner.receipts import canonical


class PublicationJournal:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _result(row: dict[str, Any], intent: PublicationIntent) -> PullRequestResult:
        result = _decode(row, PullRequestResult)
        binding = intent.binding
        if (
            result.run_id != binding.run_id
            or result.external_id != intent.external_id
            or result.source_commit != binding.source_commit
            or result.patch_ref != binding.patch_ref
            or result.head_branch != intent.head_branch
            or result.origin != "REMOTE_SERVICE"
        ):
            raise StoreError("PUBLICATION_RESULT_INTEGRITY")
        return result

    def get(
        self, binding: ApprovalBinding, nonce: UUID, permission: ReviewPermission
    ) -> PullRequestResult | None:
        """Owner/member-scoped historical read, never a new publication action."""
        if (
            permission.owner_id != binding.owner_id
            or permission.repository_id != binding.repository_id
        ):
            raise ApprovalDenied("APPROVAL_REVIEWER_DENIED")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT "
                "p.payload,p.digest,p.external_id,p.expires_at>clock_timestamp() AS "
                "active,r.checkpoint FROM publication_results p JOIN runs r ON "
                "r.id=p.run_id WHERE p.run_id=%s AND r.owner_id=%s",
                (binding.run_id, binding.owner_id),
            ).fetchone()
            if row is None:
                return None
            if not row["active"]:
                raise StoreError("PUBLICATION_RESULT_EXPIRED")
            run = RunState.model_validate(row["checkpoint"])
            if (
                run.source_commit != binding.source_commit
                or run.repository_id != binding.repository_id
                or not run.patch_attempt_refs
                or run.patch_attempt_refs[-1] != binding.patch_ref
                or run.verification_ref != binding.verification_ref
                or run.policy_version != binding.policy_version
            ):
                raise ApprovalDenied("APPROVAL_BINDING_DENIED")
            saved = conn.execute(
                "SELECT payload,digest FROM publication_intents WHERE run_id=%s",
                (binding.run_id,),
            ).fetchone()
            if saved is None:
                raise StoreError("PUBLICATION_INTENT_REQUIRED")
            intent = _decode(saved, PublicationIntent)
            if (
                intent.binding != binding
                or intent.approval_nonce != nonce
                or intent.external_id != row["external_id"]
            ):
                raise StoreError("PUBLICATION_RESULT_INTEGRITY")
            return self._result(row, intent)

    def work(
        self,
        intent: PublicationIntent,
        permission: ReviewPermission,
        *,
        expected_version: int,
    ) -> PublicationWork:
        """Load reviewed diff bytes and original authoring time from custody."""
        binding = intent.binding
        approval = ApprovalJournal(self.store, target_branch=binding.target_branch)
        with self.store._connection() as conn:
            run, _, _ = approval._current(conn, binding, permission)
            if (
                run.current_stage != Stage.PR_CREATING
                or run.version != expected_version + 1
            ):
                raise StoreError("PUBLICATION_STAGE_DENIED")
            saved = conn.execute(
                "SELECT payload,digest,from_version FROM publication_intents "
                "WHERE run_id=%s",
                (binding.run_id,),
            ).fetchone()
            if (
                saved is None
                or _decode(saved, PublicationIntent) != intent
                or saved["from_version"] != expected_version
            ):
                raise StoreError("PUBLICATION_INTENT_CONFLICT")
            row = conn.execute(
                "SELECT payload,digest FROM approval_decisions WHERE run_id=%s AND "
                "nonce=%s AND expires_at>clock_timestamp()",
                (binding.run_id, intent.approval_nonce),
            ).fetchone()
            if row is None:
                raise StoreError("PUBLICATION_DECISION_REQUIRED")
            decision = _decode(row, HumanDecision)
            if (
                decision.nonce != intent.approval_nonce
                or decision.binding != binding
                or decision.reviewer_id != permission.reviewer_id
                or decision.decision != "approve"
            ):
                raise ApprovalDenied("APPROVAL_BINDING_DENIED")
            manifest_ref = run.evidence_manifest_ref
        if manifest_ref is None:
            raise StoreError("PUBLICATION_EVIDENCE_REQUIRED")
        _, files = EvidenceStore(self.store).read(
            binding.run_id, binding.owner_id, manifest_ref
        )
        candidates = {
            f.content
            for f in files
            if "sha256:" + sha256(f.content).hexdigest() == binding.patch_ref
        }
        if len(candidates) != 1:
            raise StoreError("PUBLICATION_PATCH_REQUIRED")
        try:
            diff = candidates.pop().decode("utf-8")
        except UnicodeError:
            raise StoreError("PUBLICATION_PATCH_INVALID") from None
        grant = self.store.repository(binding.repository_id, binding.owner_id)
        patch_paths(diff, grant)
        return PublicationWork(
            intent=intent,
            issued_at=decision.issued_at,
            diff=diff,
            grant=grant,
        )

    def complete(
        self,
        intent: PublicationIntent,
        result: PullRequestResult,
        *,
        expected_version: int,
    ) -> PullRequestResult:
        """Retain trusted-port facts after cancellation, without a remote action.

        Expiry/revocation after an authorized request cannot erase its outcome.
        A changed/cancelled checkpoint is preserved, never reopened as PR_OPENED.
        """
        result = PullRequestResult.model_validate(result.model_dump())
        binding = intent.binding
        if result.origin != "REMOTE_SERVICE":
            raise StoreError("PUBLICATION_TEST_EVIDENCE_DENIED")
        self._result(
            {
                "payload": canonical(result),
                "digest": sha256(canonical(result)).hexdigest(),
            },
            intent,
        )
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (binding.run_id, binding.owner_id),
            ).fetchone()
            saved = conn.execute(
                "SELECT payload,digest,from_version FROM publication_intents WHERE "
                "run_id=%s",
                (binding.run_id,),
            ).fetchone()
            if (
                row is None
                or saved is None
                or _decode(saved, PublicationIntent) != intent
                or saved["from_version"] != expected_version
            ):
                raise StoreError("PUBLICATION_INTENT_CONFLICT")
            run = RunState.model_validate(row["checkpoint"])
            prior = conn.execute(
                "SELECT payload,digest FROM publication_results WHERE run_id=%s",
                (binding.run_id,),
            ).fetchone()
            if prior is not None:
                cached = self._result(prior, intent)
                if cached.model_dump(
                    exclude={"observed_at", "state"}
                ) != result.model_dump(exclude={"observed_at", "state"}):
                    raise StoreError("PUBLICATION_RESULT_CONFLICT")
                return cached
            body = canonical(result)
            conn.execute(
                "INSERT INTO publication_results(run_id,external_id,digest,payload) "
                "VALUES (%s,%s,%s,%s)",
                (binding.run_id, intent.external_id, sha256(body).hexdigest(), body),
            )
            self.store._event(conn, run, "PUBLICATION_OBSERVED")
            if (
                run.current_stage == Stage.PR_CREATING
                and run.version == expected_version + 1
                and not run.cancellation_requested
                and run.source_commit == binding.source_commit
                and run.patch_attempt_refs[-1] == binding.patch_ref
                and run.verification_ref == binding.verification_ref
            ):
                updated = run.model_copy(
                    update={
                        "current_stage": Stage.PR_OPENED,
                        "version": run.version + 1,
                    }
                )
                self.store._save(conn, updated)
            return result
