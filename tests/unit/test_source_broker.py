"""Worker client never receives GitHub credentials and never retries POST."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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


def test_worker_restart_after_uncertain_collection_keeps_post_single():
    claimed = lease()
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"enabled": True})
        raise httpx.ReadTimeout("synthetic lost collection response")

    class StickyLeaseStore:
        """Model the durable lease remaining held after an unknown POST result."""

        def __init__(self):
            self.claim_attempts = 0

        def claim(self, *_args, **_kwargs):
            self.claim_attempts += 1
            return claimed if self.claim_attempts == 1 else None

        def claimed_run(self, _lease):
            return uuid4(), SimpleNamespace(version=0)

    store = StickyLeaseStore()
    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as first_worker:
        with pytest.raises(BrokerError, match="UNCONFIRMED"):
            dispatch_once(store, claimed.worker_id, first_worker, ("README.md",))

    # A restarted worker may check readiness, but cannot reclaim the live lease
    # or repeat the collection POST whose outcome is unknown.
    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(handle),
    ) as restarted_worker:
        assert (
            dispatch_once(store, uuid4(), restarted_worker, ("README.md",)).status
            == "IDLE"
        )

    assert store.claim_attempts == 2
    assert calls == ["GET", "POST", "GET"]


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


@pytest.mark.parametrize(
    "module", ["run_authenticated_worker", "launch_authenticated_worker"]
)
@pytest.mark.parametrize(
    "flags",
    [
        ["--triage"],
        ["--patch"],
        ["--patch", "--allow-live-inference"],
        [
            "--patch",
            "--triage",
            "--allow-live-inference",
            "--estimated-call-microusd",
            "1",
        ],
        ["--allow-live-inference"],
        ["--triage", "--allow-live-inference"],
        ["--estimated-call-microusd", "1"],
    ],
)
def test_triage_requires_all_authorization_flags_before_io(module, flags, monkeypatch):
    import importlib

    script = importlib.import_module("scripts." + module)
    monkeypatch.setattr("sys.argv", [module, "--run", *flags])
    with pytest.raises(SystemExit) as stopped:
        script.main()
    assert stopped.value.code == 2


def test_triage_admission_is_bound_to_exact_run_and_version():
    claimed = lease()
    for response in [
        {"status": "ADMITTED", "run_id": str(uuid4()), "version": 3},
        {"status": "ADMITTED", "run_id": str(claimed.run_id), "version": 4},
    ]:
        with BrokerClient(
            "http://127.0.0.1:8000",
            SecretStr("s" * 43),
            transport=httpx.MockTransport(
                lambda r, response=response: httpx.Response(200, json=response)
            ),
        ) as c:
            with pytest.raises(BrokerError, match="UNCONFIRMED"):
                c.authorize_triage(claimed, 3)


def test_patch_launcher_passes_explicit_mode_and_only_worker_model_credentials(
    tmp_path, monkeypatch
):
    from scripts import launch_authenticated_worker as launch

    path = tmp_path / ".env.worker"
    path.write_text(
        (
            "REPLAYFORGE_DATABASE_URL=host=127.0.0.1 dbname=demo "
            "user=worker password=synthetic\n"
        )
        + "REPLAYFORGE_COLLECTION_WORKER_KEY="
        + "s" * 43
        + "\nNEBIUS_API_KEY=synthetic-only\n"
    )
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "not-forwarded")
    captured = []

    def start(command, **kwargs):
        captured.append((command, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launch.subprocess, "run", start)
    monkeypatch.setattr(
        "sys.argv",
        [
            "launch",
            "--run",
            "--env-file",
            str(path),
            "--patch",
            "--allow-live-inference",
            "--estimated-call-microusd",
            "1",
        ],
    )
    assert launch.main() == 0
    command, env = captured[0]
    assert "--patch" in command and "--triage" not in command
    assert (
        env["NEBIUS_API_KEY"] == "synthetic-only" and "GITHUB_CLIENT_SECRET" not in env
    )


def test_materialization_source_client_checks_manifest_and_git_blob_identity():
    import base64
    import hashlib
    from datetime import UTC, datetime

    from packages.contracts.source import SourceFile, SourceManifest
    from services.runner.receipts import canonical

    body = b"def sample():\n    return 1\n"
    blob_id = hashlib.sha1(
        f"blob {len(body)}\0".encode() + body, usedforsecurity=False
    ).hexdigest()
    claimed = lease()
    manifest = SourceManifest(
        repository_id=uuid4(),
        source_commit="a" * 40,
        root_tree="b" * 40,
        evidence_origin="REMOTE_SERVICE",
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
        files=(
            SourceFile(
                path="src/a.py",
                blob_id=blob_id,
                sha256=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
            ),
        ),
    )
    response = {
        "run_id": str(claimed.run_id),
        "version": 7,
        "source_commit": manifest.source_commit,
        "manifest_ref": "sha256:" + hashlib.sha256(canonical(manifest)).hexdigest(),
        "files": [{"path": "src/a.py", "content_b64": base64.b64encode(body).decode()}],
        "status": "ADMITTED",
    }
    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        ),
    ) as client:
        assert client.fetch_materialization_source(claimed, 7, manifest) == {
            "src/a.py": body
        }
    response["files"][0]["content_b64"] = base64.b64encode(body + b"evil").decode()
    with BrokerClient(
        "http://127.0.0.1:8000",
        SecretStr("s" * 43),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        ),
    ) as client:
        with pytest.raises(BrokerError, match="SOURCE_UNCONFIRMED"):
            client.fetch_materialization_source(claimed, 7, manifest)
