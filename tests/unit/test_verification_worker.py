"""Fail-closed configuration checks for the production verification worker."""

import pytest

from scripts import run_verification_worker
from services.orchestrator.verification_worker import (
    VerificationWorkerConfig,
    VerificationWorkerError,
)


def test_worker_config_requires_explicit_database_identity() -> None:
    with pytest.raises(VerificationWorkerError, match="CONFIG_INVALID"):
        VerificationWorkerConfig.from_env({})


def test_worker_config_pins_loopback_database_and_head() -> None:
    config = VerificationWorkerConfig.from_env(
        {
            "REPLAYFORGE_DATABASE_URL": "postgresql://worker:secret@127.0.0.1:5432/rf_worker",
            "REPLAYFORGE_DATABASE_NAME": "rf_worker",
            "REPLAYFORGE_VERIFICATION_WORKER_ROLE": "rf_verify_worker",
        }
    )
    assert config.expected_database == "rf_worker"
    assert config.expected_revision == "0036"
    assert config.required_role == "rf_verify_worker"
    assert "secret" not in repr(config)


def test_worker_config_requires_verified_tls_for_remote_database() -> None:
    values = {
        "REPLAYFORGE_DATABASE_URL": "postgresql://worker:secret@db.example:5432/rf_worker",
        "REPLAYFORGE_DATABASE_NAME": "rf_worker",
    }
    with pytest.raises(VerificationWorkerError, match="CONFIG_INVALID"):
        VerificationWorkerConfig.from_env(values)
    values["REPLAYFORGE_DATABASE_URL"] += "?sslmode=verify-full"
    assert VerificationWorkerConfig.from_env(values).expected_database == "rf_worker"


def test_worker_cli_does_not_start_without_explicit_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["run_verification_worker"])
    assert run_verification_worker.main() == 2


def test_worker_cli_fails_closed_without_external_trust_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["run_verification_worker", "--run"])
    monkeypatch.setenv("REPLAYFORGE_VERIFICATION_TRUST_PROVIDER", "missing.module")
    assert run_verification_worker.main() == 2


def test_worker_cli_rejects_inherited_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["run_verification_worker", "--run"])
    monkeypatch.setenv("NEBIUS_API_KEY", "not-a-real-key")
    assert run_verification_worker.main() == 2
