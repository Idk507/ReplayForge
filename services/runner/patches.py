"""Bounded textual diff admission only; never apply patches or execute code."""

import hashlib
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, CommitSHA, RunState, Stage
from packages.policies.repository import admit, allowed_entry


class PatchDenied(Exception):
    """Closed safe denial with no diff/source content."""


class PatchAdmission(BaseModel):
    """Metadata for a future broker; not application or verification evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    source_commit: CommitSHA
    source_manifest_ref: ArtifactID
    baseline_result_ref: ArtifactID
    reproduction_test_ref: ArtifactID
    patch_ref: ArtifactID
    files_changed: tuple[str, ...] = Field(min_length=1, max_length=25)
    changed_lines: int = Field(strict=True, ge=1, le=5000)
    observed_at: AwareDatetime
    trace_id: UUID
    status: Literal["ADMITTED_FOR_CHECK"] = "ADMITTED_FOR_CHECK"
    policy_decision: Literal["ALLOW_PATCH_CHECK"] = "ALLOW_PATCH_CHECK"
    retryable: Literal[False] = False
    error_class: None = None
    artifacts: tuple[ArtifactID, ...] = ()
    can_authorize_execution: Literal[False] = False


def _paths(diff: str) -> tuple[tuple[str, ...], int]:
    """Strict modify-only Git unified diff, with exact hunk line accounting."""
    if len(diff) > 500000:
        raise PatchDenied("PATCH_LIMIT")
    try:
        if len(diff.encode()) > 500000:
            raise PatchDenied("PATCH_LIMIT")
    except UnicodeEncodeError:
        raise PatchDenied("PATCH_ENCODING") from None
    if not diff.endswith("\n") or "\0" in diff or "\r" in diff:
        raise PatchDenied("PATCH_FORMAT")
    lines = diff.split("\n")[:-1]
    if len(lines) > 10000:
        raise PatchDenied("PATCH_LIMIT")
    paths: list[str] = []
    changed = i = 0
    while i < len(lines):
        header = re.fullmatch(r"diff --git a/([^ ]+) b/([^ ]+)", lines[i])
        if header is None or header[1] != header[2]:
            raise PatchDenied("PATCH_FORMAT")
        path = header[1]
        if path.lower() in {p.lower() for p in paths} or len(paths) >= 25:
            raise PatchDenied("PATCH_FILES")
        paths.append(path)
        i += 1
        if i < len(lines) and lines[i].startswith("index "):
            if not re.fullmatch(
                r"index [a-f0-9]{7,40}\.\.[a-f0-9]{7,40}(?: 100644)?", lines[i]
            ):
                raise PatchDenied("PATCH_METADATA")
            i += 1
        if lines[i : i + 2] != [f"--- a/{path}", f"+++ b/{path}"]:
            raise PatchDenied("PATCH_FORMAT")
        i += 2
        hunks = 0
        old_end = new_end = -1
        file_changed = 0
        while i < len(lines) and lines[i].startswith("@@ "):
            hunk = re.fullmatch(
                r"@@ -(\d{1,6})(?:,(\d{1,6}))? \+(\d{1,6})(?:,(\d{1,6}))? @@(?: .*)?",
                lines[i],
            )
            if hunk is None:
                raise PatchDenied("PATCH_HUNK")
            old_start, old_count = int(hunk[1]), int(hunk[2] or "1")
            new_start, new_count = int(hunk[3]), int(hunk[4] or "1")
            if (
                old_start < old_end
                or new_start < new_end
                or (old_count and old_start == 0)
                or (new_count and new_start == 0)
            ):
                raise PatchDenied("PATCH_HUNK")
            old_end, new_end = old_start + old_count, new_start + new_count
            i += 1
            old = new = 0
            while old < old_count or new < new_count:
                if i >= len(lines) or not lines[i] or lines[i][0] not in " +-":
                    raise PatchDenied("PATCH_HUNK")
                prefix = lines[i][0]
                old += prefix != "+"
                new += prefix != "-"
                file_changed += prefix != " "
                if old > old_count or new > new_count:
                    raise PatchDenied("PATCH_HUNK")
                i += 1
            hunks += 1
        if not hunks or not file_changed:
            raise PatchDenied("PATCH_EMPTY")
        changed += file_changed
        if changed > 5000:
            raise PatchDenied("PATCH_LIMIT")
    if not paths:
        raise PatchDenied("PATCH_EMPTY")
    return tuple(paths), changed


def admit_patch(
    diff: str,
    run: RunState,
    grant: RepositoryGrant,
    caller: UUID,
    *,
    source_commit: str,
    source_manifest_ref: str | None,
    baseline_result_ref: str | None,
    reproduction_test_ref: str | None,
) -> PatchAdmission:
    """Check consent/checkpoint and return hashes; no budget/state mutation."""
    run = RunState.model_validate(run.model_dump())
    if not admit(grant, caller, run.repository_id, run.source_commit).allowed:
        raise PatchDenied("PATCH_SCOPE_DENIED")
    if (
        run.current_stage != Stage.PATCHING
        or run.cancellation_requested
        or run.remaining_budget.patch_attempts < 1
        or source_commit != run.source_commit
        or source_manifest_ref is None
        or source_manifest_ref != run.source_manifest_ref
        or baseline_result_ref is None
        or baseline_result_ref != run.baseline_result_ref
        or reproduction_test_ref is None
        or reproduction_test_ref != run.reproduction_test_ref
    ):
        raise PatchDenied("PATCH_BINDING_DENIED")
    paths, changed = _paths(diff)
    if any(
        not p.startswith(("src/", "app/"))
        or not p.endswith(".py")
        or not allowed_entry(grant, p, "100644")
        for p in paths
    ):
        raise PatchDenied("PATCH_PATH_DENIED")
    return PatchAdmission(
        run_id=run.run_id,
        source_commit=run.source_commit,
        source_manifest_ref=source_manifest_ref,
        baseline_result_ref=baseline_result_ref,
        reproduction_test_ref=reproduction_test_ref,
        patch_ref="sha256:" + hashlib.sha256(diff.encode()).hexdigest(),
        files_changed=paths,
        changed_lines=changed,
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
    )
