"""Deterministic acceptance of retained fixture observations, never VERIFIED."""

import hashlib
from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.repair import PatchProposal, TriageProposal
from services.runner.checkout_benchmark import FixtureObservation
from services.runner.patches import PatchDenied, _paths


class FixtureExecution(FixtureObservation):
    """Recorded remote operation metadata; not a signed production receipt."""

    operation_id: UUID
    image_uuid: UUID
    compute_terminal_confirmed: bool = Field(strict=True)


class CheckoutAcceptance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["M2_FIXTURE_ACCEPTED", "INCONCLUSIVE"]
    failed_checks: tuple[str, ...]
    evidence_origin: Literal["RETAINED_FIXTURE_OBSERVATIONS"] = (
        "RETAINED_FIXTURE_OBSERVATIONS"
    )
    can_authorize_verification: Literal[False] = False
    can_authorize_execution: Literal[False] = False


def inspect_checkout(
    report: Mapping[str, object], triage: TriageProposal, patch: PatchProposal
) -> CheckoutAcceptance:
    """Check identities and outcomes independently of the report's success label.

    This verifies consistency of captured synthetic benchmark evidence. It does
    not authenticate unsigned guest claims, assess isolation, or issue a repair
    verdict for the production approval path.
    """
    failed: list[str] = []
    try:
        triage = TriageProposal.model_validate(triage.model_dump())
        patch = PatchProposal.model_validate(patch.model_dump())
        baseline, candidate, replay = (
            FixtureExecution.model_validate(report.get(phase))
            for phase in ("baseline", "candidate", "clean_replay")
        )
        paths, _ = _paths(patch.unified_diff)
    except (ValidationError, PatchDenied):
        return CheckoutAcceptance(
            status="INCONCLUSIVE", failed_checks=("SCHEMA_OR_PATCH",)
        )
    phases = (baseline, candidate, replay)
    if report.get("scope") != "FIXED_SYNTHETIC_CHECKOUT_ONLY":
        failed.append("SCOPE")
    if paths != ("app/checkout.py",) or patch.files_changed != paths:
        failed.append("PATCH_SCOPE")
    if (
        report.get("patch_ref")
        != "sha256:" + hashlib.sha256(patch.unified_diff.encode()).hexdigest()
    ):
        failed.append("PATCH_IDENTITY")
    test_hash = triage.reproduction.test_source_sha256()
    if report.get("test_sha256") != test_hash or any(
        p.test_sha256 != test_hash for p in phases
    ):
        failed.append("TEST_IDENTITY")
    if len({p.operation_id for p in phases}) != 3:
        failed.append("INDEPENDENT_OPERATIONS")
    if len({p.image_uuid for p in phases}) != 1:
        failed.append("IMAGE_IDENTITY")
    if (
        len({p.source_commit for p in phases}) != 1
        or len({p.source_tree for p in phases}) != 1
    ):
        failed.append("SOURCE_IDENTITY")
    if (
        baseline.tree != baseline.source_tree
        or candidate.tree != replay.tree
        or candidate.tree == baseline.tree
    ):
        failed.append("TREE_IDENTITY")
    if any(
        p.status != "EXECUTED"
        or not p.compute_terminal_confirmed
        or p.setup_error
        or not p.tests_ran
        or p.uid != 1000
        or p.interfaces != ["lo"]
        or p.credential_env_present
        for p in phases
    ):
        failed.append("EXECUTION_CONDITIONS")
    if baseline.exit_code != 1 or not baseline.behavioral_failure:
        failed.append("BEHAVIORAL_BASELINE")
    if any(p.exit_code != 0 or p.behavioral_failure for p in (candidate, replay)):
        failed.append("PATCHED_PASS")
    return CheckoutAcceptance(
        status="INCONCLUSIVE" if failed else "M2_FIXTURE_ACCEPTED",
        failed_checks=tuple(failed),
    )
