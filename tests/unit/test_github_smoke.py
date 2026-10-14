"""Offline-first probe cannot silently acquire credentials or call GitHub."""

import pytest
from pydantic import SecretStr

from services.repository.smoke import probe


def test_no_live_authorization_returns_not_run() -> None:
    result = probe("a" * 40, SecretStr("synthetic-test-token"))
    assert result.status == "NOT_RUN"
    assert result.evidence_origin == "NOT_RUN"
    assert result.policy_decision == "DENY_LIVE_NOT_AUTHORIZED"


def test_live_without_credential_fails_safely() -> None:
    result = probe("a" * 40, live=True)
    assert result.status == "NOT_RUN"
    assert result.error_class == "CREDENTIAL_MISSING"


def test_invalid_commit_is_rejected_before_http() -> None:
    result = probe("main", SecretStr("synthetic-test-token"), live=True)
    assert result.status == "NOT_RUN"
    assert result.error_class == "COMMIT_INVALID"


def test_offline_cli_never_reads_cli_credentials(monkeypatch, capsys) -> None:
    from scripts.check_github import main

    monkeypatch.setattr("sys.argv", ["check_github", "--use-gh-cli"])

    def forbidden(*args, **kwargs):
        raise AssertionError("offline command accessed credential")

    monkeypatch.setattr("scripts.check_github.subprocess.run", forbidden)
    assert main() == 0
    assert '"status":"NOT_RUN"' in capsys.readouterr().out


def test_invalid_cli_commit_precedes_credential_access(monkeypatch) -> None:
    from scripts.check_github import main

    monkeypatch.setattr(
        "sys.argv", ["check_github", "--live", "--use-gh-cli", "--commit", "main"]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid commit accessed credential")

    monkeypatch.setattr("scripts.check_github.subprocess.run", forbidden)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
