"""Worker startup never discovers credentials or starts I/O by default."""

import json

import pytest

from scripts import run_worker


def env():
    return {
        "REPLAYFORGE_DATABASE_URL": (
            "host=127.0.0.1 dbname=fixture user=runtime password=synthetic"
        ),
        "REPLAYFORGE_GITHUB_READ_TOKEN": "synthetic-read-token",
        "REPLAYFORGE_WORKER_PATHS": '["src/a.py"]',
    }


def test_default_does_not_read_config_or_start_worker(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["run_worker"])
    monkeypatch.setattr(
        run_worker.WorkerConfig, "from_env", lambda: pytest.fail("read configuration")
    )
    assert run_worker.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_RUN"


def test_config_pins_address_and_masks_secrets(monkeypatch) -> None:
    from psycopg.conninfo import conninfo_to_dict

    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    config = run_worker.WorkerConfig.from_env(env())
    info = conninfo_to_dict(config.dsn.get_secret_value())
    assert info["hostaddr"] == "127.0.0.1" and info["connect_timeout"] == "5"
    assert "synthetic" not in repr(config)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "remote", "hostaddr", "options", "paths", "secret_path", "token"],
)
def test_invalid_configuration_is_safe(mutation: str) -> None:
    values = env()
    if mutation == "missing":
        values.clear()
    elif mutation == "remote":
        values["REPLAYFORGE_DATABASE_URL"] = "host=remote.example dbname=fixture"
    elif mutation in {"hostaddr", "options"}:
        values["REPLAYFORGE_DATABASE_URL"] += f" {mutation}=bad"
    elif mutation == "paths":
        values["REPLAYFORGE_WORKER_PATHS"] = '["src/a.py", "src/a.py"]'
    elif mutation == "secret_path":
        values["REPLAYFORGE_WORKER_PATHS"] = '[".env"]'
    else:
        values["REPLAYFORGE_GITHUB_READ_TOKEN"] = "bad\nheader"
    with pytest.raises(run_worker.WorkerConfigError, match="WORKER_CONFIG_INVALID"):
        run_worker.WorkerConfig.from_env(values)


def test_startup_rejects_config_before_io_without_secret_output(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.argv", ["run_worker", "--run"])
    monkeypatch.setattr(
        run_worker.WorkerConfig,
        "from_env",
        lambda: (_ for _ in ()).throw(
            run_worker.WorkerConfigError("WORKER_CONFIG_INVALID")
        ),
    )
    assert run_worker.main() == 2
    assert json.loads(capsys.readouterr().out)["error_class"] == "WORKER_CONFIG_INVALID"


@pytest.mark.parametrize(
    "extra", [[], ["--harness"], ["--harness", "--estimated-call-microusd", "0"]]
)
def test_live_harness_rejects_missing_authorization_configuration(monkeypatch, extra):
    monkeypatch.setattr(
        "sys.argv", ["run_worker", "--run", "--allow-live-inference", *extra]
    )
    monkeypatch.setattr(
        run_worker.WorkerConfig,
        "from_env",
        lambda: pytest.fail("read config before permission"),
    )
    with pytest.raises(SystemExit) as error:
        run_worker.main()
    assert error.value.code == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_harness_cli_preserves_inference_choice_and_restores_signals(
    monkeypatch, capsys, enabled
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from services.orchestrator.harness import HarnessResult

    argv = ["run_worker", "--run", "--harness"]
    if enabled:
        argv += ["--allow-live-inference", "--estimated-call-microusd", "100000"]
    monkeypatch.setattr("sys.argv", argv)
    config = run_worker.WorkerConfig.from_env(env())
    monkeypatch.setattr(run_worker.WorkerConfig, "from_env", lambda: config)

    model = run_worker.NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    monkeypatch.setattr(
        run_worker.NebiusConfig,
        "from_env",
        lambda: model if enabled else pytest.fail("read model key"),
    )

    @contextmanager
    def connect(*a, **k):
        yield SimpleNamespace(
            execute=lambda *a: SimpleNamespace(
                fetchone=lambda: (False, False, False, False)
            )
        )

    monkeypatch.setattr(run_worker.psycopg, "connect", connect)
    signals = []
    monkeypatch.setattr(
        run_worker.signal,
        "signal",
        lambda sig, handler: signals.append((sig, handler)) or "old",
    )

    def harness(*a, **k):
        assert (k["model_config"] is not None) == enabled
        assert (k["cost_policy"] is not None) == enabled
        assert k["allow_live_inference"] == enabled
        if enabled:
            assert k["cost_policy"].estimated_spend_microusd == 100000
        return HarnessResult(
            steps=1,
            processed=0,
            collected=0,
            triaged=0,
            idle=1,
            inference_enabled=enabled,
            stop_requested=False,
        )

    monkeypatch.setattr(run_worker, "serve_harness", harness)
    assert run_worker.main() == 0
    assert json.loads(capsys.readouterr().out)["inference_enabled"] == enabled
    assert len(signals) == 4 and all(handler == "old" for _, handler in signals[2:])
