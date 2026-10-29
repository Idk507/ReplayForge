"""Offline-default tests for the operator image-import command."""

import json

from scripts.import_runner_image import main

SOURCE = "docker://docker.io/library/python@sha256:" + ("c" * 64)


def test_cli_defaults_to_not_run_without_credentials_or_database(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_PROJECT_ID", raising=False)
    monkeypatch.delenv("REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL", raising=False)

    assert main(["--source", SOURCE]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "NOT_RUN"
    assert report["policy_decision"] == "DENY_LIVE_NOT_AUTHORIZED"
    assert "docker.io" not in json.dumps(report)


def test_cli_rejects_invalid_input_without_echoing_source(capsys) -> None:
    private_source = "docker://user:private@docker.io/library/python:latest"

    assert main(["--source", private_source, "--live"]) == 2
    output = capsys.readouterr().out
    assert "S_IMAGE_REFERENCE_INVALID" in output
    assert "private" not in output
    assert "user" not in output
