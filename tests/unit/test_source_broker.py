"""Worker client never receives GitHub credentials and never retries POST."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from services.orchestrator.source_broker import BrokerClient, BrokerError, dispatch_once
from services.persistence.store import Lease


def lease():
    return Lease(
        run_id=uuid4(),
        worker_id=uuid4(),
        token=uuid4(),
        leased_until=datetime.now(UTC) + timedelta(minutes=5),
    )


def test_broker_client_correlates_result_and_keeps_uncertain_post_single():
    claimed = lease()
    calls = []

    def handle(r):
        calls.append(r)
        assert r.headers["authorization"] == "Bearer " + "s" * 43
        if r.method == "GET":
            return httpx.Response(200, json={"enabled": True})
        return httpx.Response(
            200,
            json={
                "status": "PROCESSED",
                "run_id": str(claimed.run_id),
                "stage": "TRIAGING",
            },
        )

    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as c:
        c.ready()
        assert c.dispatch(claimed, 0, ("README.md",)).run_id == claimed.run_id
    assert [r.method for r in calls] == ["GET", "POST"]
    count = []

    def timeout(r):
        count.append(1)
        raise httpx.ReadTimeout("synthetic private")

    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(timeout),
    ) as c:
        with pytest.raises(BrokerError, match="UNCONFIRMED"):
            c.dispatch(claimed, 0, ("README.md",))
    assert len(count) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example",
        "http://localhost:8000",
        "http://127.0.0.1:8000/x",
        "http://127.0.0.1:8000?x=1",
    ],
)
def test_broker_client_rejects_untrusted_destinations(url):
    with pytest.raises(ValueError):
        BrokerClient(url, SecretStr("s" * 43))


def test_broker_not_ready_never_claims_a_job():
    class S:
        def claim(self, *a):
            pytest.fail("claimed before readiness")

    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"enabled": False})
        ),
    ) as c:
        with pytest.raises(BrokerError):
            dispatch_once(S(), uuid4(), c, ("README.md",))


def test_authenticated_worker_default_performs_no_io(monkeypatch, capsys):
    from scripts import run_authenticated_worker as worker

    monkeypatch.setattr("sys.argv", ["worker"])
    monkeypatch.setattr(
        worker.BrokerWorkerConfig, "from_env", lambda: pytest.fail("read secrets")
    )
    assert worker.main() == 2
    assert "NOT_RUN" in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["vault", "remote", "key", "path"])
def test_worker_configuration_enforces_credential_isolation(fault):
    from scripts.run_authenticated_worker import BrokerWorkerConfig, ConfigError

    values = {
        "REPLAYFORGE_DATABASE_URL": (
            "host=127.0.0.1 dbname=demo user=worker password=synthetic"
        ),
        "REPLAYFORGE_COLLECTION_WORKER_KEY": "s" * 43,
    }
    if fault == "vault":
        values["REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX"] = "11" * 32
    if fault == "remote":
        values["REPLAYFORGE_DATABASE_URL"] = (
            "host=evil.example dbname=demo user=worker password=synthetic"
        )
    if fault == "key":
        values["REPLAYFORGE_COLLECTION_WORKER_KEY"] = ""
    if fault == "path":
        values["REPLAYFORGE_WORKER_PATHS"] = '["../secret"]'
    with pytest.raises(ConfigError):
        BrokerWorkerConfig.from_env(values)


def test_worker_configuration_masks_credentials_and_uses_broker_only():
    from scripts.run_authenticated_worker import BrokerWorkerConfig

    c = BrokerWorkerConfig.from_env(
        {
            "REPLAYFORGE_DATABASE_URL": (
                "host=127.0.0.1 dbname=demo user=worker password=synthetic"
            ),
            "REPLAYFORGE_COLLECTION_WORKER_KEY": "s" * 43,
        }
    )
    assert c.paths == ("README.md",) and "synthetic" not in repr(c)


def test_launcher_excludes_inherited_provider_secrets(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import launch_authenticated_worker as launch

    config = tmp_path / ".env.worker"
    config.write_text(
        "REPLAYFORGE_DATABASE_URL=host=127.0.0.1 dbname=demo "
        "user=worker password=synthetic\nREPLAYFORGE_COLLECTION_WORKER_KEY="
        + ("s" * 43)
        + "\n"
    )
    monkeypatch.setenv("NEBIUS_API_KEY", "synthetic-provider-secret")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "synthetic-client-secret")
    captured = []

    def child(command, **kwargs):
        captured.append(kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch.subprocess, "run", child)
    monkeypatch.setattr("sys.argv", ["launch", "--run", "--env-file", str(config)])
    assert launch.main() == 0
    assert not {"NEBIUS_API_KEY", "GITHUB_CLIENT_SECRET"} & captured[0].keys()
    assert captured[0]["REPLAYFORGE_COLLECTION_WORKER_KEY"] == "s" * 43


def test_launcher_default_never_reads_private_config(monkeypatch, capsys):
    from scripts import launch_authenticated_worker as launch

    monkeypatch.setattr("sys.argv", ["launch", "--env-file", "missing-private-file"])
    assert launch.main() == 2
    assert "NOT_RUN" in capsys.readouterr().out
