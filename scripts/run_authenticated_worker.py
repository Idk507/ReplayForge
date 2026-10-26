"""Run a bounded credential-isolated collection worker. No I/O without --run."""

import argparse
import json
import os
import re
import signal
from collections.abc import Mapping
from threading import Event
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, ConfigDict, SecretStr

from packages.contracts.inference import ModelCostPolicy
from packages.policies.repository import safe_tree_path
from packages.providers.nebius import NebiusConfig, ProbeError
from services.orchestrator.source_broker import (
    BrokerClient,
    BrokerError,
    dispatch_once,
    dispatch_patch_once,
    dispatch_triage_once,
)
from services.persistence.store import Store, StoreError


class ConfigError(Exception):
    """Safe operator-configuration failure."""


class BrokerWorkerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    dsn: SecretStr
    key: SecretStr
    url: str
    paths: tuple[str, ...]

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "BrokerWorkerConfig":
        v = os.environ if values is None else values
        forbidden = {
            "GITHUB_CLIENT_SECRET",
            "NEBIUS_API_KEY",
            "TAVILY_API_KEY",
            "REPLAYFORGE_AUTH_DATABASE_URL",
            "REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX",
            "REPLAYFORGE_AUTH_HMAC_KEY_HEX",
            "REPLAYFORGE_AUTH_SETUP_MIGRATION_DATABASE_URL",
            "REPLAYFORGE_GITHUB_READ_TOKEN",
        }
        if any(v.get(k) for k in forbidden):
            raise ConfigError("WORKER_SECRET_ISOLATION_REQUIRED")
        try:
            raw = v.get("REPLAYFORGE_DATABASE_URL", "")
            if not 1 <= len(raw) <= 4096:
                raise ValueError
            info = conninfo_to_dict(raw)
            if (
                info.get("host") != "127.0.0.1"
                or not all(info.get(k) for k in ["dbname", "user", "password"])
                or set(info)
                - {
                    "host",
                    "port",
                    "dbname",
                    "user",
                    "password",
                    "sslmode",
                    "connect_timeout",
                }
            ):
                raise ValueError
            info.update(
                hostaddr="127.0.0.1",
                connect_timeout="5",
                options="-c statement_timeout=5000 -c lock_timeout=3000",
            )
            key = v.get("REPLAYFORGE_COLLECTION_WORKER_KEY", "")
            url = v.get("REPLAYFORGE_COLLECTION_BROKER_URL", "http://127.0.0.1:8000")
            paths = json.loads(v.get("REPLAYFORGE_WORKER_PATHS", '["README.md"]'))
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{43}", key)
                or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", url)
                or not isinstance(paths, list)
                or not 1 <= len(paths) <= 16
                or len(set(paths)) != len(paths)
                or any(not isinstance(p, str) or not safe_tree_path(p) for p in paths)
            ):
                raise ValueError
            if not 1 <= int(url.rsplit(":", 1)[1]) <= 65535:
                raise ValueError
            return cls(
                dsn=SecretStr(make_conninfo("", **info)),
                key=SecretStr(key),
                url=url,
                paths=tuple(paths),
            )
        except (ValueError, TypeError, psycopg.Error):
            raise ConfigError("COLLECTION_WORKER_CONFIG_INVALID") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--max-ticks", type=int, choices=range(1, 1001), default=1)
    parser.add_argument("--triage", action="store_true")
    parser.add_argument("--patch", action="store_true")
    parser.add_argument("--allow-live-inference", action="store_true")
    parser.add_argument("--estimated-call-microusd", type=int)
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    if (
        (args.triage or args.patch) != args.allow_live_inference
        or (args.triage and args.patch)
        or (
            (args.triage or args.patch)
            and not (
                args.estimated_call_microusd is not None
                and 1 <= args.estimated_call_microusd <= 2_000_000
            )
        )
        or (
            not (args.triage or args.patch) and args.estimated_call_microusd is not None
        )
    ):
        parser.error(
            "choose triage or patch; inference requires opt-in and estimate 1..2000000"
        )
    stop = Event()
    previous = {}
    try:
        values = dict(os.environ)
        model = None
        cost = None
        if args.triage or args.patch:
            model = NebiusConfig.from_env(values)
            values.pop("NEBIUS_API_KEY", None)
            cost = ModelCostPolicy(
                model=model.model,
                base_url=model.base_url,
                policy_version="operator-estimate-v1",
                estimated_spend_microusd=args.estimated_call_microusd,
            )
        c = BrokerWorkerConfig.from_env(values)
        with psycopg.connect(c.dsn.get_secret_value()) as db:
            role = db.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,"
                "has_table_privilege(current_user,'repository_grants','INSERT'),"
                "has_table_privilege(current_user,'auth_credentials','SELECT') "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role is None or any(role):
                raise ConfigError("COLLECTION_WORKER_ROLE_DENIED")
        for sig in [signal.SIGINT, signal.SIGTERM]:
            previous[sig] = signal.signal(sig, lambda signum, frame: stop.set())
        store = Store(c.dsn.get_secret_value())
        worker = uuid4()
        with BrokerClient(c.url, c.key) as client:
            for _ in range(args.max_ticks):
                if stop.is_set():
                    break
                result = (
                    dispatch_patch_once(store, worker, client, model, cost)
                    if args.patch and model is not None and cost is not None
                    else dispatch_triage_once(store, worker, client, model, cost)
                    if model is not None and cost is not None
                    else dispatch_once(store, worker, client, c.paths)
                )
                print(result.model_dump_json())
                if result.status == "IDLE" and stop.wait(1):
                    break
        return 0
    except (ConfigError, BrokerError, StoreError, ProbeError, psycopg.Error) as error:
        print(
            json.dumps(
                {
                    "status": "STOPPED",
                    "reason": str(error)
                    if re.fullmatch(r"[A-Z_]{1,64}", str(error))
                    else "COLLECTION_WORKER_FAILED",
                    "retryable": False,
                }
            )
        )
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
