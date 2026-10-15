"""Pure command broker planning; no shell, filesystem, network or execution."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from packages.contracts.execution import (
    TestCommandPlan,
    TestCommandRequest,
    pytest_argv,
)
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry


class CommandPolicyError(Exception):
    """Stable safe planning denial; no raw source, logs or caller data."""


def plan_pytest(
    request: TestCommandRequest, run: RunState, grant: RepositoryGrant, caller: UUID
) -> TestCommandPlan:
    """Bind a fixed command to consent/checkpoint; reserve nothing and run nothing.

    Future dispatch must recheck live cancellation, reserve command budget,
    validate artifacts and independently admit its remote runner capabilities.
    """
    request = TestCommandRequest.model_validate(request.model_dump())
    run = RunState.model_validate(run.model_dump())
    if not admit(grant, caller, run.repository_id, run.source_commit).allowed:
        raise CommandPolicyError("COMMAND_SCOPE_DENIED")
    if any(not allowed_entry(grant, path, "100644") for path in request.test_paths):
        raise CommandPolicyError("COMMAND_PATH_DENIED")
    if (
        request.run_id != run.run_id
        or request.repository_id != run.repository_id
        or request.source_commit != run.source_commit
        or request.source_manifest_ref != run.source_manifest_ref
        or request.reproduction_test_ref != run.reproduction_test_ref
        or run.cancellation_requested
        or run.remaining_budget.sandbox_commands < 1
    ):
        raise CommandPolicyError("COMMAND_BINDING_DENIED")
    if request.phase == "BASELINE":
        if run.current_stage != Stage.REPRODUCING:
            raise CommandPolicyError("COMMAND_STAGE_DENIED")
    elif (
        run.current_stage != Stage.VERIFYING
        or run.baseline_result_ref is None
        or not run.patch_attempt_refs
        or request.patch_ref != run.patch_attempt_refs[-1]
    ):
        raise CommandPolicyError("COMMAND_PATCH_DENIED")
    return TestCommandPlan(
        request=request,
        argv=pytest_argv(request),
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
    )
