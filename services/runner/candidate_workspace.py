"""Exact patch application to original bytes, with the retained test unchanged.

This broker transforms bounded data in memory. It performs no checkout, host
execution, filesystem extraction, provider request or execution admission.
"""

import io
import re
import tarfile
from collections.abc import Mapping
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState, Stage
from packages.contracts.source import SourceManifest
from packages.policies.repository import admit, allowed_entry
from services.orchestrator.reproduction import ReproductionArtifact
from services.persistence.patch import RetainedPatch
from services.runner.materialization import (
    MaterializationPlan,
    MaterializationUpload,
    prepare_upload,
)
from services.runner.patches import PatchDenied, _paths
from services.runner.workspace import archive_identity


class CandidateWorkspace(BaseModel):
    """Private packaging, not a saved-image measurement or dispatch permit."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    original: MaterializationPlan
    upload: MaterializationUpload
    patch_ref: ArtifactID
    can_authorize_execution: Literal[False] = False


def _apply(files: Mapping[str, bytes], diff: str) -> dict[str, bytes]:
    """Apply already syntax-checked modify-only hunks with no fuzz or offsets."""
    result = dict(files)
    lines = diff.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        header = re.fullmatch(r"diff --git a/([^ ]+) b/([^ ]+)\n", lines[index])
        if header is None or header[1] not in files:
            raise PatchDenied("PATCH_ORIGINAL_FILE_REQUIRED")
        path = header[1]
        try:
            original = files[path].decode("utf-8").splitlines(keepends=True)
        except UnicodeError:
            raise PatchDenied("PATCH_SOURCE_ENCODING") from None
        index += 1
        if lines[index].startswith("index "):
            index += 1
        index += 2  # Exact ---/+++ headers were validated by _paths.
        output: list[str] = []
        cursor = 0
        while index < len(lines) and lines[index].startswith("@@ "):
            hunk = re.fullmatch(
                r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n", lines[index]
            )
            if hunk is None:
                raise PatchDenied("PATCH_HUNK")
            old_start, old_count = int(hunk[1]), int(hunk[2] or "1")
            new_start, new_count = int(hunk[3]), int(hunk[4] or "1")
            old_position = old_start - int(old_count != 0)
            new_position = new_start - int(new_count != 0)
            if not cursor <= old_position <= len(original):
                raise PatchDenied("PATCH_SOURCE_CONTEXT_MISMATCH")
            output.extend(original[cursor:old_position])
            cursor = old_position
            if new_position != len(output):
                raise PatchDenied("PATCH_SOURCE_CONTEXT_MISMATCH")
            index += 1
            old_used = new_used = 0
            while old_used < old_count or new_used < new_count:
                line = lines[index]
                marker, text = line[0], line[1:]
                if marker != "+":
                    if cursor >= len(original) or original[cursor] != text:
                        raise PatchDenied("PATCH_SOURCE_CONTEXT_MISMATCH")
                    cursor += 1
                    old_used += 1
                if marker != "-":
                    output.append(text)
                    new_used += 1
                index += 1
            if len("".join(output).encode()) > 65536:
                raise PatchDenied("PATCH_WORKSPACE_LIMIT")
        output.extend(original[cursor:])
        result[path] = "".join(output).encode()
    if sum(map(len, result.values())) > 65536:
        raise PatchDenied("PATCH_WORKSPACE_LIMIT")
    return result


def prepare_candidate(
    image_uuid: UUID,
    manifest: SourceManifest,
    files: Mapping[str, bytes],
    reproduction: ReproductionArtifact,
    *,
    patch: RetainedPatch,
    run: RunState,
    grant: RepositoryGrant,
    owner: UUID,
) -> CandidateWorkspace:
    """Rebuild from original manifest bytes, then apply the exact retained diff.

    Both candidate and clean replay can consume this immutable package. Neither
    may start from a previously executed/mutated workspace. Upload needs a
    separate durable phase-specific intent; baseline materialization cannot be
    reused to grant a candidate submission.
    """
    run = RunState.model_validate(run.model_dump())
    patch = RetainedPatch.model_validate(patch.model_dump())
    # Copy once so archive verification and patch application see identical bytes
    # even when the caller owns a mutable Mapping implementation.
    files = dict(files)
    original = prepare_upload(image_uuid, manifest, files, reproduction)
    admission = patch.admission
    diff = patch.proposal.unified_diff
    reference = "sha256:" + sha256(diff.encode()).hexdigest()
    paths, changed = _paths(diff)
    if (
        run.current_stage != Stage.VERIFYING
        or run.cancellation_requested
        or not admit(grant, owner, run.repository_id, run.source_commit).allowed
        or original.plan.run_id != run.run_id
        or original.plan.repository_id != run.repository_id
        or original.plan.source_commit != run.source_commit
        or original.plan.source_manifest_ref != run.source_manifest_ref
        or original.plan.reproduction_test_ref != run.reproduction_test_ref
        or reproduction.context_refs != run.context_refs
        or not run.patch_attempt_refs
        or run.patch_attempt_refs[-1] != reference
        or admission.run_id != run.run_id
        or admission.source_commit != run.source_commit
        or admission.source_manifest_ref != run.source_manifest_ref
        or admission.reproduction_test_ref != run.reproduction_test_ref
        or admission.baseline_result_ref != run.baseline_result_ref
        or admission.patch_ref != reference
        or admission.files_changed != paths
        or patch.proposal.files_changed != paths
        or admission.changed_lines != changed
        or any(not allowed_entry(grant, p, "100644") for p in files)
        or any(
            not p.startswith(("src/", "app/"))
            or not p.endswith(".py")
            or not allowed_entry(grant, p, "100644")
            for p in paths
        )
    ):
        raise PatchDenied("PATCH_WORKSPACE_BINDING_DENIED")
    patched = _apply(files, diff)
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(original.archive), mode="r:") as source:
        with tarfile.open(
            fileobj=output, mode="w", format=tarfile.USTAR_FORMAT
        ) as target:
            for member in source.getmembers():
                if member.isfile() and member.name in patched:
                    body = patched[member.name]
                    member.size = len(body)
                    target.addfile(member, io.BytesIO(body))
                else:
                    target.addfile(
                        member, source.extractfile(member) if member.isfile() else None
                    )
    body = output.getvalue()
    workspace, _, _ = archive_identity(body)
    upload = MaterializationUpload(
        plan=original.plan.model_copy(
            update={
                "archive_ref": "sha256:" + sha256(body).hexdigest(),
                "workspace_ref": workspace,
            }
        ),
        archive=body,
    )
    return CandidateWorkspace(
        original=original.plan, upload=upload, patch_ref=reference
    )
