"""Explicit local worker startup; offline by default, opt-in budgeted triage."""

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
from packages.providers.nebius import NebiusConfig
from services.orchestrator.harness import serve_harness
from services.orchestrator.source import github_inputs
from services.orchestrator.worker import bind_incident, serve
from services.persistence.store import Store


class WorkerConfigError(Exception):
    """Closed public startup failure; never contains configuration values."""


class WorkerConfig(BaseModel):
    """Operator input only; no dotenv, CLI token retrieval or secret discovery."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    dsn: SecretStr
    read_token: SecretStr
    paths: tuple[str, ...]

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "WorkerConfig":
        values = os.environ if values is None else values
        try:
            raw_dsn = values.get("REPLAYFORGE_DATABASE_URL", "")
            raw_paths = values.get("REPLAYFORGE_WORKER_PATHS", "")
            token = values.get("REPLAYFORGE_GITHUB_READ_TOKEN", "")
            if len(raw_dsn) > 4096 or len(raw_paths) > 4096:
                raise ValueError
            info = conninfo_to_dict(raw_dsn)
            paths = json.loads(raw_paths)
            if (
                info.get("host") not in {"127.0.0.1", "localhost", "::1"}
                or not info.get("dbname")
                or not info.get("user")
                or not info.get("password")
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
                or not isinstance(paths, list)
                or not 1 <= len(paths) <= 16
                or any(not isinstance(p, str) or not safe_tree_path(p) for p in paths)
                or len(set(paths)) != len(paths)
                or not re.fullmatch(r"[\x21-\x7e]{1,4096}", token)
            ):
                raise ValueError
            info["connect_timeout"] = "5"
            # Explicit address defeats libpq PGHOSTADDR environment redirection.
            info["hostaddr"] = "::1" if info["host"] == "::1" else "127.0.0.1"
            info.setdefault("port", "5432")
            info["options"] = "-c statement_timeout=5000 -c lock_timeout=3000"
            return cls(
                dsn=SecretStr(make_conninfo("", **info)),
                read_token=SecretStr(token),
                paths=tuple(paths),
            )
        except (ValueError, TypeError, psycopg.Error):
            raise WorkerConfigError("WORKER_CONFIG_INVALID") from None


def main() -> int:
    """Start only with --run; default one tick, cooperative SIGINT/SIGTERM stop."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Enable local DB claims and scoped GitHub reads",
    )
    parser.add_argument(
        "--max-ticks", type=int, default=1, choices=range(1, 1001), metavar="N"
    )
    parser.add_argument(
        "--queue-triage",
        action="store_true",
        help="Queue collected context for a separately authorized triage worker",
    )
    parser.add_argument(
        "--harness",
        action="store_true",
        help="Use bounded collection/triage scheduling (inference off by default)",
    )
    parser.add_argument(
        "--allow-live-inference",
        action="store_true",
        help="Explicitly permit budgeted Nebius inference in harness mode",
    )
    parser.add_argument(
        "--estimated-call-microusd",
        type=int,
        help="Declared per-call estimate, not verified provider billing",
    )
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    if args.allow_live_inference and (
        not args.harness
        or args.estimated_call_microusd is None
        or not 1 <= args.estimated_call_microusd <= 2_000_000
    ):
        parser.error("live inference requires --harness and an estimate in 1..2000000")
    if args.estimated_call_microusd is not None and not args.allow_live_inference:
        parser.error("a call estimate requires explicit --allow-live-inference")
    stop = Event()
    previous = {}
    try:
        config = WorkerConfig.from_env()
        with psycopg.connect(config.dsn.get_secret_value()) as conn:
            role = conn.execute(
                """SELECT rolsuper,rolcreatedb,rolcreaterole,
                has_table_privilege(current_user,'repository_grants','INSERT')
                FROM pg_roles WHERE rolname=current_user"""
            ).fetchone()
            if role is None or any(role):
                raise WorkerConfigError("WORKER_ROLE_DENIED")
        store = Store(config.dsn.get_secret_value())
        loader = bind_incident(store, github_inputs(config.read_token, config.paths))
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda signum, frame: stop.set())
        if args.harness:
            model = NebiusConfig.from_env() if args.allow_live_inference else None
            cost = (
                ModelCostPolicy(
                    model=model.model,
                    base_url=model.base_url,
                    policy_version="operator-estimate-v1",
                    estimated_spend_microusd=args.estimated_call_microusd,
                )
                if model is not None
                else None
            )
            result = serve_harness(
                store,
                uuid4(),
                loader,
                stop,
                model_config=model,
                cost_policy=cost,
                allow_live_inference=args.allow_live_inference,
                max_steps=args.max_ticks,
            )
            print(result.model_dump_json())
        else:
            collection_result = serve(
                store,
                uuid4(),
                loader,
                stop,
                max_ticks=args.max_ticks,
                queue_triage=args.queue_triage,
            )
            print(collection_result.model_dump_json())
        return 0
    except WorkerConfigError as error:
        print(json.dumps({"status": "FAILED", "error_class": str(error)}))
        return 2
    except Exception:
        # CLI perimeter: never print provider/DB exception text or retry work.
        print(json.dumps({"status": "FAILED", "error_class": "WORKER_FAILED"}))
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
