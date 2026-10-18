"""Contree SDK runner for bounded, disposable ReplayForge test executions.

This adapter intentionally accepts direct argv only. Repository credentials are
not embedded in clone URLs; private-source access must be materialized by a
separate trusted source service.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Protocol

from contree_sdk import ContreeSync  # type: ignore[import-untyped]
from contree_sdk.sdk.exceptions import ContreeError  # type: ignore[import-untyped]
from contree_sdk.utils.models.file import UploadFileSpec  # type: ignore[import-untyped]


class SandboxRunnerError(Exception):
    """Stable failure code without provider payloads or credentials."""


@dataclass(frozen=True)
class GitWorkspace:
    url: str
    revision: str
    token: None = None

    def __post_init__(self) -> None:
        if not self.url.startswith("https://") or not self.url.endswith(".git"):
            raise SandboxRunnerError("GIT_URL_DENIED")
        if re.fullmatch(r"[a-f0-9]{40}", self.revision) is None:
            raise SandboxRunnerError("GIT_REVISION_DENIED")

    def manifest_sha256(self) -> str:
        """Bind the remote origin and immutable commit without retaining a token."""
        return sha256(f"{self.url}\n{self.revision}\n".encode()).hexdigest()


@dataclass(frozen=True)
class DirectoryWorkspace:
    path: Path

    def manifest_sha256(self) -> str:
        """Hash admitted paths and bytes in stable order before upload."""
        digest = sha256()
        root = self.path.resolve()
        for spec in self.files():
            source = Path(spec.source)
            relative = source.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            data = source.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def files(self) -> list[UploadFileSpec]:
        root = self.path.resolve()
        if not root.is_dir():
            raise SandboxRunnerError("DIRECTORY_NOT_FOUND")
        specs: list[UploadFileSpec] = []
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.is_symlink():
                continue
            relative = file.relative_to(root)
            if ".git" in relative.parts or any(
                part.startswith(".env") for part in relative.parts
            ):
                continue
            specs.append(
                UploadFileSpec(source=file, path=PurePosixPath("/workspace") / relative)
            )
        if not specs or len(specs) > 1024:
            raise SandboxRunnerError("DIRECTORY_CONTENT_DENIED")
        return specs


Workspace = GitWorkspace | DirectoryWorkspace


@dataclass(frozen=True)
class ExecutionResult:
    workspace_sha256: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    truncated: bool


class ImageLike(Protocol):
    def run(
        self,
        command: str,
        *,
        args: list[str],
        cwd: str | None = None,
        files: list[UploadFileSpec] | None = None,
        timeout: float | None = None,
        disposable: bool = True,
        preserve_env: bool = False,
        stdout: type[str] = str,
        stderr: type[str] = str,
        truncate_output_at: int = 65536,
    ) -> ImageLike: ...
    def wait(self) -> ImageLike: ...
    @property
    def result(self) -> ContreeResultLike: ...


class ContreeResultLike(Protocol):
    exit_code: int
    stdout: str | None
    stderr: str | None
    truncated: bool


class NebiusSandboxRunner:
    """One-use Contree execution session with deterministic cleanup semantics."""

    def __init__(
        self,
        image: str,
        *,
        client: ContreeSync | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if not image or timeout_seconds < 1 or timeout_seconds > 900:
            raise SandboxRunnerError("RUNNER_CONFIG_DENIED")
        self._client = client or ContreeSync()
        self._image_name = image
        self._timeout = timeout_seconds
        self._image: ImageLike | None = None

    def __enter__(self) -> NebiusSandboxRunner:
        self._image = self._client.images.use(self._image_name)
        return self

    def __exit__(self, *_: object) -> None:
        self._image = None

    def execute(
        self, workspace: Workspace, command: tuple[str, ...]
    ) -> ExecutionResult:
        if self._image is None:
            raise SandboxRunnerError("RUNNER_NOT_STARTED")
        if not self._allowed_test_command(command):
            raise SandboxRunnerError("COMMAND_DENIED")
        start = time.monotonic()
        try:
            image = self._materialize(workspace)
            completed = image.run(
                command[0],
                args=list(command[1:]),
                cwd="/workspace",
                timeout=self._timeout,
                disposable=True,
                preserve_env=False,
                stdout=str,
                stderr=str,
                truncate_output_at=65536,
            ).wait()
            result = completed.result
            return ExecutionResult(
                workspace_sha256=workspace.manifest_sha256(),
                exit_code=int(result.exit_code),
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
                duration_seconds=time.monotonic() - start,
                truncated=bool(result.truncated),
            )
        except (ContreeError, OSError, RuntimeError, ValueError) as error:
            raise SandboxRunnerError("SANDBOX_UNAVAILABLE") from error

    @staticmethod
    def _allowed_test_command(command: tuple[str, ...]) -> bool:
        """Allow only direct pytest or unittest entrypoints and ordinary args."""
        if not command or any(not arg or "\x00" in arg for arg in command):
            return False
        executable, *args = command
        entrypoint = executable == "pytest" or (
            executable in {"python", "python3"}
            and len(args) >= 2
            and tuple(args[:2]) in (("-m", "pytest"), ("-m", "unittest"))
        )
        safe_args = all(
            not arg.startswith("-")
            or arg in {"-m", "-q", "-s", "-v"}
            or arg.startswith("--")
            for arg in args
        )
        return entrypoint and safe_args

    def _materialize(self, workspace: Workspace) -> ImageLike:
        assert self._image is not None
        if isinstance(workspace, DirectoryWorkspace):
            uploaded = self._image.run(
                "/usr/bin/true",
                args=[],
                files=workspace.files(),
                timeout=self._timeout,
                disposable=False,
                preserve_env=False,
                stdout=str,
                stderr=str,
            ).wait()
            self._require_prepared(uploaded)
            return uploaded
        if workspace.token is not None:
            raise SandboxRunnerError("GIT_TOKEN_DENIED")
        cloned = self._image.run(
            "git",
            args=["clone", "--no-checkout", workspace.url, "/workspace"],
            timeout=self._timeout,
            disposable=False,
            preserve_env=False,
            stdout=str,
            stderr=str,
        ).wait()
        self._require_prepared(cloned)
        checked_out = cloned.run(
            "git",
            args=["checkout", "--detach", workspace.revision],
            cwd="/workspace",
            timeout=self._timeout,
            disposable=False,
            preserve_env=False,
            stdout=str,
            stderr=str,
        ).wait()
        self._require_prepared(checked_out)
        return checked_out

    @staticmethod
    def _require_prepared(image: ImageLike) -> None:
        """Stop before tests when any workspace preparation command failed."""
        if image.result.exit_code != 0:
            raise SandboxRunnerError("WORKSPACE_PREPARATION_FAILED")
