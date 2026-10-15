"""Command planning is inert; never execute the argv in these tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.execution import TestCommandRequest
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from services.runner.commands import CommandPolicyError, plan_pytest


def inputs():
    owner = uuid4()
    run = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        source_commit="a" * 40,
        policy_version="local-v1",
        trace_id=uuid4(),
        current_stage=Stage.REPRODUCING,
        source_manifest_ref="sha256:" + "b" * 64,
        reproduction_test_ref="sha256:" + "c" * 64,
    )
    grant = RepositoryGrant(
        repository_id=run.repository_id,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=(run.source_commit,),
        approved_paths=("tests/",),
        consent=True,
    )
    request = TestCommandRequest(
        run_id=run.run_id,
        repository_id=run.repository_id,
        source_commit=run.source_commit,
        source_manifest_ref=run.source_manifest_ref,
        reproduction_test_ref=run.reproduction_test_ref,
        image_digest="sha256:" + "d" * 64,
        phase="BASELINE",
        reproduction_path="tests/test_regression.py",
        test_paths=("tests/test_regression.py",),
    )
    return run, grant, owner, request


def test_exact_command_and_bindings_without_execution_authority() -> None:
    run, grant, owner, request = inputs()
    result = plan_pytest(request, run, grant, owner)
    assert result.argv[0:4] == ("/usr/local/bin/python", "-I", "-m", "pytest")
    assert result.argv[-2:] == ("--", "tests/test_regression.py")
    assert result.shell is False and result.can_authorize_execution is False
    assert result.request == request and result.retryable is False
    assert result.observed_at.utcoffset() is not None
    assert "addopts=" in result.argv and "/dev/null" in result.argv


@pytest.mark.parametrize(
    "path",
    [
        "../tests/a.py",
        "tests/../a.py",
        "-p",
        "tests/a.py;id",
        "tests/a.py::test_x",
        "tests/a.py\n",
        "tests/link/../../a.py",
        "app/test_a.py",
        "tests/.env",
        "tests/a.py -k all",
    ],
)
def test_unsafe_test_targets_rejected(path: str) -> None:
    _, _, _, request = inputs()
    with pytest.raises(ValidationError):
        TestCommandRequest.model_validate(
            {**request.model_dump(), "test_paths": (path,), "reproduction_path": path}
        )


@pytest.mark.parametrize(
    "change",
    ["owner", "commit", "run", "manifest", "test", "stage", "cancel", "consent"],
)
def test_scope_stage_and_artifact_binding_fail_closed(change: str) -> None:
    run, grant, owner, request = inputs()
    if change == "owner":
        owner = uuid4()
    elif change == "commit":
        run = run.model_copy(update={"source_commit": "e" * 40})
    elif change == "run":
        run = run.model_copy(update={"run_id": uuid4()})
    elif change == "manifest":
        run = run.model_copy(update={"source_manifest_ref": None})
    elif change == "test":
        run = run.model_copy(update={"reproduction_test_ref": "sha256:" + "f" * 64})
    elif change == "stage":
        run = run.model_copy(update={"current_stage": Stage.TRIAGING})
    elif change == "cancel":
        run = run.model_copy(update={"cancellation_requested": True})
    else:
        grant = grant.model_copy(update={"consent": False})
    with pytest.raises(CommandPolicyError):
        plan_pytest(request, run, grant, owner)


def test_unknown_flags_and_missing_regression_rejected() -> None:
    _, _, _, request = inputs()
    with pytest.raises(ValidationError):
        TestCommandRequest.model_validate(
            {**request.model_dump(), "args": ["-k", "skip"]}
        )
    with pytest.raises(ValidationError):
        TestCommandRequest.model_validate(
            {**request.model_dump(), "test_paths": ("tests/other.py",)}
        )


@pytest.mark.parametrize("phase", ["CANDIDATE", "CLEAN_REPLAY"])
def test_patched_phases_require_latest_attempt(phase: str) -> None:
    run, grant, owner, request = inputs()
    patch = "sha256:" + "e" * 64
    run = run.model_copy(
        update={
            "current_stage": Stage.VERIFYING,
            "baseline_result_ref": "sha256:" + "f" * 64,
            "patch_attempt_refs": (patch,),
        }
    )
    request = request.model_copy(update={"phase": phase, "patch_ref": patch})
    assert plan_pytest(request, run, grant, owner).request.patch_ref == patch
    run = run.model_copy(update={"patch_attempt_refs": (patch, "sha256:" + "1" * 64)})
    with pytest.raises(CommandPolicyError, match="COMMAND_PATCH_DENIED"):
        plan_pytest(request, run, grant, owner)


def test_exhausted_budget_and_unapproved_test_path_denied() -> None:
    run, grant, owner, request = inputs()
    empty = run.remaining_budget.model_copy(update={"sandbox_commands": 0})
    with pytest.raises(CommandPolicyError):
        plan_pytest(
            request, run.model_copy(update={"remaining_budget": empty}), grant, owner
        )
    with pytest.raises(CommandPolicyError, match="COMMAND_PATH_DENIED"):
        plan_pytest(
            request, run, grant.model_copy(update={"approved_paths": ("src/",)}), owner
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("argv", ("sh", "-c", "id")),
        ("env", (("GITHUB_TOKEN", "synthetic"),)),
        ("can_authorize_execution", True),
    ],
)
def test_forged_plan_cannot_change_template_or_authority(field, value) -> None:
    from packages.contracts.execution import TestCommandPlan

    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    with pytest.raises(ValidationError):
        TestCommandPlan.model_validate({**plan.model_dump(), field: value})
