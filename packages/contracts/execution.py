"""Inert pytest planning vocabulary; none of these objects permit execution."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.run import ArtifactID, CommitSHA
from packages.policies.repository import safe_tree_path


class TestCommandRequest(BaseModel):
    """Model may propose files, never argv, shell, env, executable or timeout."""

    __test__ = False
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    repository_id: UUID
    source_commit: CommitSHA
    source_manifest_ref: ArtifactID
    reproduction_test_ref: ArtifactID
    image_digest: ArtifactID
    phase: Literal["BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"]
    patch_ref: ArtifactID | None = None
    reproduction_path: str = Field(max_length=240)
    test_paths: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def targets(self) -> Self:
        if (
            any(
                not safe_tree_path(p)
                or not p.startswith("tests/")
                or not p.endswith(".py")
                for p in self.test_paths
            )
            or len({p.lower() for p in self.test_paths}) != len(self.test_paths)
            or self.reproduction_path not in self.test_paths
            or ((self.phase == "BASELINE") != (self.patch_ref is None))
        ):
            raise ValueError("COMMAND_TARGET_DENIED")
        return self


def pytest_argv(
    request: TestCommandRequest, *, no_bytecode: bool = False
) -> tuple[str, ...]:
    """Application-owned version-one template; no shell interpolation."""
    return (
        "/usr/local/bin/python",
        *(("-B",) if no_bytecode else ()),
        "-I",
        "-m",
        "pytest",
        "-c",
        "/dev/null",
        "-o",
        "addopts=",
        "-q",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "--junitxml=/tmp/replayforge-result.xml",
        "--",
        *request.test_paths,
    )


class TestCommandPlan(BaseModel):
    """A planned tool response, not a signed runner observation or authorization."""

    __test__ = False
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    command_id: Literal[
        "PYTEST_FILES_V1", "PYTEST_FILES_SAVED_JUNIT_V1", "PYTEST_FILES_MEASURED_V1"
    ] = "PYTEST_FILES_V1"
    status: Literal["PLANNED"] = "PLANNED"
    observed_at: datetime
    trace_id: UUID
    policy_decision: Literal["ALLOW_COMMAND_PLAN"] = "ALLOW_COMMAND_PLAN"
    retryable: Literal[False] = False
    error_class: None = None
    artifacts: tuple[ArtifactID, ...] = ()
    request: TestCommandRequest
    argv: tuple[str, ...]
    shell: Literal[False] = False
    cwd: Literal["/workspace/repo"] = "/workspace/repo"
    env: tuple[tuple[str, str], ...] = (("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1"),)
    inherit_env: Literal[False] = False
    timeout_seconds: Literal[60] = 60
    can_authorize_execution: Literal[False] = False

    @model_validator(mode="after")
    def exact_template(self) -> Self:
        if self.argv != pytest_argv(
            self.request, no_bytecode=self.command_id == "PYTEST_FILES_MEASURED_V1"
        ) or self.env != (("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1"),):
            raise ValueError("COMMAND_TEMPLATE_DENIED")
        return self
