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
