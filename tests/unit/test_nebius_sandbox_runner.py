"""Offline contracts for the Contree workspace adapter."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from services.runner.nebius_sandbox import (
    DirectoryWorkspace,
    GitWorkspace,
    NebiusSandboxRunner,
    SandboxRunnerError,
)


@dataclass
class Result:
    exit_code: int = 0
    stdout: str = "ok"
    stderr: str = ""
    truncated: bool = False


class Image:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = Result()

    def run(self, command: str, **kwargs: object) -> "Image":
        self.calls.append((command, kwargs))
        return self

    def wait(self) -> "Image":
        return self


class Images:
    def __init__(self, image: Image) -> None:
        self.image = image
        self.used: str | None = None

    def use(self, image: str) -> Image:
        self.used = image
        return self.image


class Client:
    def __init__(self, image: Image) -> None:
        self.images = Images(image)


def test_directory_upload_and_execution_are_direct_argv(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / ".env").write_text("secret")
    image = Image()
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(image))  # type: ignore[arg-type]
    with runner:
        result = runner.execute(DirectoryWorkspace(tmp_path), ("pytest", "-q"))
    assert result.exit_code == 0
    assert len(result.workspace_sha256) == 64
    assert image.calls[0][0] == "/usr/bin/true"
    files = image.calls[0][1]["files"]
    assert len(files) == 1 and str(files[0].path) == "/workspace/app.py"
    assert image.calls[-1][0] == "pytest"
    assert image.calls[-1][1]["disposable"] is True


def test_directory_manifest_is_stable_and_excludes_secret_files(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "a.py").write_text("a")
    workspace = DirectoryWorkspace(tmp_path)
    original = workspace.manifest_sha256()
    (tmp_path / ".env").write_text("ignored")
    assert workspace.manifest_sha256() == original
    (tmp_path / "a.py").write_text("changed")
    assert workspace.manifest_sha256() != original


def test_git_checkout_is_pinned_and_token_is_denied() -> None:
    image = Image()
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(image))  # type: ignore[arg-type]
    source = GitWorkspace("https://github.com/example/fixture.git", "a" * 40)
    with runner:
        result = runner.execute(source, ("python", "-m", "pytest"))
    assert result.workspace_sha256 == source.manifest_sha256()
    assert image.calls[0][1]["args"] == [
        "clone",
        "--no-checkout",
        source.url,
        "/workspace",
    ]
    assert image.calls[1][1]["args"] == ["checkout", "--detach", source.revision]
    with runner, pytest.raises(SandboxRunnerError, match="GIT_TOKEN_DENIED"):
        runner.execute(
            GitWorkspace(source.url, source.revision, token="x"),  # type: ignore[arg-type]
            ("pytest",),
        )


def test_runner_rejects_unstarted_or_invalid_commands(tmp_path: Path) -> None:
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(Image()))  # type: ignore[arg-type]
    with pytest.raises(SandboxRunnerError, match="RUNNER_NOT_STARTED"):
        runner.execute(DirectoryWorkspace(tmp_path), ("pytest",))


@pytest.mark.parametrize(
    "command",
    [("git", "status"), ("python", "-c", "print(1)"), ("pytest", "\x00")],
)
def test_runner_rejects_non_test_commands(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(Image()))  # type: ignore[arg-type]
    with runner, pytest.raises(SandboxRunnerError, match="COMMAND_DENIED"):
        runner.execute(DirectoryWorkspace(tmp_path), command)


def test_git_workspace_rejects_mutable_branch_or_tag() -> None:
    with pytest.raises(SandboxRunnerError, match="GIT_REVISION_DENIED"):
        GitWorkspace("https://github.com/example/fixture.git", "main")


def test_unittest_discovery_command_is_admitted() -> None:
    assert NebiusSandboxRunner._allowed_test_command(
        ("python", "-m", "unittest", "discover", "-s", "visible_tests")
    )


@pytest.mark.parametrize("failed_step", [0, 1])
def test_failed_git_preparation_prevents_test_execution(failed_step: int) -> None:
    class FailingImage(Image):
        def run(self, command: str, **kwargs: object) -> "FailingImage":
            super().run(command, **kwargs)
            self.result = Result(
                exit_code=1 if len(self.calls) - 1 == failed_step else 0
            )
            return self

    image = FailingImage()
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(image))  # type: ignore[arg-type]
    with (
        runner,
        pytest.raises(SandboxRunnerError, match="WORKSPACE_PREPARATION_FAILED"),
    ):
        runner.execute(
            GitWorkspace("https://github.com/example/fixture.git", "a" * 40),
            ("pytest",),
        )
    assert len(image.calls) == failed_step + 1
    assert all(command == "git" for command, _ in image.calls)


def test_failed_upload_prevents_test_execution(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("pass\n")
    image = Image()
    image.result = Result(exit_code=1)
    runner = NebiusSandboxRunner("python:3.12-slim", client=Client(image))  # type: ignore[arg-type]
    with (
        runner,
        pytest.raises(SandboxRunnerError, match="WORKSPACE_PREPARATION_FAILED"),
    ):
        runner.execute(DirectoryWorkspace(tmp_path), ("pytest",))
    assert len(image.calls) == 1
