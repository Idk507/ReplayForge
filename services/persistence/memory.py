"""Repository-scoped read-through memory over durable verification records."""

import json
from datetime import datetime
from uuid import UUID

from packages.contracts.memory import MemoryRecall, RepairLesson
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState
from packages.policies.repository import admit
from services.persistence.store import Store, StoreError
from services.persistence.verification import _decode


class RunMemory:
    """Recall bounded prior outcomes without copying/renewing evidence or state.

    Caller identity is supplied by trusted worker code. Current consent must
    cover both commits. Only same-owner/repository/policy records are considered.
    Durable accepted records are the memory: no vector service or model call.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def recall(self, run_id: UUID, owner: UUID) -> MemoryRecall:
        """Read at most three unexpired lessons in deterministic newest order."""
        with self._store._connection() as conn:
            current = conn.execute(
                "SELECT checkpoint,clock_timestamp() AS observed_at FROM runs "
                "WHERE id=%s AND owner_id=%s FOR SHARE",
                (run_id, owner),
            ).fetchone()
            if current is None:
                raise StoreError("NOT_FOUND")
            run = RunState.model_validate(current["checkpoint"])
            observed_at = current["observed_at"]
            if run.run_id != run_id or not isinstance(observed_at, datetime):
                raise StoreError("MEMORY_INTEGRITY")
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if consent is None or run.cancellation_requested:
                raise StoreError("MEMORY_SCOPE_DENIED")
            grant = RepositoryGrant.model_validate(consent["grant_json"])
            if not admit(grant, owner, run.repository_id, run.source_commit).allowed:
                raise StoreError("MEMORY_SCOPE_DENIED")
            rows = conn.execute(
                "SELECT v.payload,v.digest,"
                "LEAST(v.expires_at,b.expires_at) AS expires_at,r.checkpoint "
                "FROM verification_results v JOIN runs r ON r.id=v.run_id "
                "JOIN evidence_bundles b ON b.run_id=r.id "
                "WHERE r.owner_id=%s AND r.id<>%s "
                "AND r.checkpoint->>'repository_id'=%s "
                "AND r.checkpoint->>'policy_version'=%s "
                "AND r.checkpoint->>'source_commit'=ANY(%s) "
                "AND v.expires_at>clock_timestamp() "
                "AND b.expires_at>clock_timestamp() "
                "ORDER BY v.expires_at DESC,r.id DESC LIMIT 3",
                (
                    owner,
                    run_id,
                    str(run.repository_id),
                    run.policy_version,
                    list(grant.commits),
                ),
            ).fetchall()
            lessons: list[RepairLesson] = []
            for row in rows:
                previous = RunState.model_validate(row["checkpoint"])
                record = _decode(row, previous)
                expires_at = row["expires_at"]
                if (
                    previous.current_stage.value != record.verdict.status
                    or previous.cancellation_requested
                    or record.signed.payload.origin != "REMOTE_SERVICE"
                    or not isinstance(expires_at, datetime)
                ):
                    raise StoreError("MEMORY_INTEGRITY")
                context = record.verdict.context
                lessons.append(
                    RepairLesson(
                        source_run_id=previous.run_id,
                        source_commit=previous.source_commit,
                        patch_ref=context.patch_ref,
                        verification_ref="sha256:" + str(row["digest"]),
                        manifest_ref=context.manifest_ref,
                        outcome=record.verdict.status,
                        failed_gates=record.verdict.failed,
                        missing_gates=record.verdict.missing,
                        observed_at=record.verdict.observed_at,
                        expires_at=expires_at,
                    )
                )
            return MemoryRecall(
                status="RECALLED" if lessons else "EMPTY",
                run_id=run.run_id,
                repository_id=run.repository_id,
                lessons=tuple(lessons),
                observed_at=observed_at,
                trace_id=run.trace_id,
                artifacts=tuple(item.verification_ref for item in lessons),
            )


def with_advisory_memory(
    context: dict[str, str], recall: MemoryRecall
) -> dict[str, str]:
    """Add newest lessons that fit; never displace current incident evidence.

    Omission under the existing context ceiling is deliberate. Memory cannot
    consume an extra call or make otherwise valid current evidence oversized.
    """
    recall = MemoryRecall.model_validate(recall.model_dump())
    result = dict(context)
    if "advisory_memory" in result:
        raise StoreError("MEMORY_CONTEXT_CONFLICT")
    selected: list[RepairLesson] = []
    if not result or len(result) >= 8:
        return result
    for lesson in recall.lessons:
        candidate = {
            **result,
            "advisory_memory": json.dumps(
                [item.model_dump(mode="json") for item in (*selected, lesson)],
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        if (
            len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode())
            > 4096
        ):
            break
        selected.append(lesson)
        result = candidate
    return result
