"""Offline-default operator CLI tests for pytest image preparation."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from packages.contracts.sandbox import RunnerError
from scripts.prepare_nebius_pytest_image import _emit_failure_checkpoint, main
from services.runner.image_preparation import ImagePreparationPlan


def test_cli_defaults_to_local_preflight_without_environment_or_network(
    monkeypatch, capsys
) -> None:
    for name in (
        "NEBIUS_API_KEY",
        "NEBIUS_PROJECT_ID",
        "REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "NOT_RUN"
    assert result["policy_decision"] == "DENY_REMOTE_ACTION_NOT_SELECTED"
    assert result["request_sha256"] == (
        "15a1b4350b2a18cf64c2e274dc692bcdf594ddeb801e0552bd8a0b92744d414f"
    )
    assert "image_uuid" not in result


def test_submit_fails_closed_without_dedicated_journal_connection(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "not-used")
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "aiproject-e00bzf586kz2r694kr")
    monkeypatch.delenv("REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL", raising=False)

    assert main(["--submit"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ERROR"
    assert result["error_code"] == "S_IMAGE_JOURNAL_DATABASE_CONFIG_MISSING"


def test_recovery_fails_closed_without_dedicated_journal_connection(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "not-used")
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "aiproject-e00bzf586kz2r694kr")
    monkeypatch.delenv("REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL", raising=False)

    assert main(["--recover"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ERROR"
    assert result["error_code"] == "S_IMAGE_JOURNAL_DATABASE_CONFIG_MISSING"


def test_remote_mode_rejects_unpinned_nebius_project(monkeypatch, capsys) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "not-used")
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "aiproject-other")
    monkeypatch.setenv("REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL", "postgresql://unused")

    assert main(["--submit"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ERROR"
    assert result["error_code"] == "S_PROJECT_BINDING_DENIED"


def test_failure_checkpoint_reports_only_durable_recovery_metadata(
    tmp_path, capsys
) -> None:
    plan = ImagePreparationPlan.from_wheelhouse(
        manifest_path=Path("docs/evidence/nebius-pytest-wheelhouse.json"),
        wheelhouse=Path("artifacts/nebius-pytest-wheelhouse"),
    )
    attempt = SimpleNamespace(
        attempt_id=uuid4(),
        state="SUBMISSION_UNKNOWN",
        operation_id=None,
        events=(
            SimpleNamespace(kind="INTENT"),
            SimpleNamespace(kind="UPLOAD_KNOWN"),
            SimpleNamespace(kind="DIAGNOSTIC"),
        ),
    )
    journal = SimpleNamespace(history=lambda: attempt)

    _emit_failure_checkpoint(RunnerError("S_SUBMISSION_UNKNOWN"), plan, journal)
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["error_code"] == "S_SUBMISSION_UNKNOWN"
    assert result["attempt_id"] == str(attempt.attempt_id)
    assert result["state"] == "SUBMISSION_UNKNOWN"
    assert result["operation_id"] is None
    assert result["known_upload_count"] == 1
    assert result["last_event_kind"] == "DIAGNOSTIC"
    assert "test-secret" not in output
