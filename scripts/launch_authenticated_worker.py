"""Explicit local launcher: pass only collection workload credentials to worker."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from packages.providers.nebius import NebiusConfig, ProbeError
from scripts.run_authenticated_worker import BrokerWorkerConfig, ConfigError

ALLOWED = {
    "REPLAYFORGE_DATABASE_URL",
    "REPLAYFORGE_COLLECTION_WORKER_KEY",
    "REPLAYFORGE_COLLECTION_BROKER_URL",
    "REPLAYFORGE_WORKER_PATHS",
}
SYSTEM = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "COMSPEC",
}
MODEL_KEYS = {"NEBIUS_API_KEY", "NEBIUS_BASE_URL", "NEBIUS_MODEL_PRIMARY"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true")
    p.add_argument("--env-file", type=Path, default=Path(".env.worker"))
    p.add_argument("--max-ticks", type=int, choices=range(1, 1001), default=1)
    p.add_argument("--triage", action="store_true")
    p.add_argument("--allow-live-inference", action="store_true")
    p.add_argument("--estimated-call-microusd", type=int)
    args = p.parse_args()
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    if (
        args.triage != args.allow_live_inference
        or (
            args.triage
            and not (
                args.estimated_call_microusd is not None
                and 1 <= args.estimated_call_microusd <= 2_000_000
            )
        )
        or (not args.triage and args.estimated_call_microusd is not None)
    ):
        p.error("triage requires explicit inference opt-in and estimate 1..2000000")
    try:
        path = args.env_file
        if path.is_symlink() or path.stat().st_size > 8192:
            raise ValueError
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if (
                key not in (ALLOWED | (MODEL_KEYS if args.triage else set()))
                or key in values
            ):
                raise ValueError
            values[key] = value
        BrokerWorkerConfig.from_env(
            {k: v for k, v in values.items() if k not in MODEL_KEYS}
        )
        if args.triage:
            NebiusConfig.from_env(values)
        child = {k: v for k, v in os.environ.items() if k.upper() in SYSTEM}
        child.update(values)
        child["PYTHONIOENCODING"] = "utf-8"
        command = [
            sys.executable,
            "-m",
            "scripts.run_authenticated_worker",
            "--run",
            "--max-ticks",
            str(args.max_ticks),
        ]
        if args.triage:
            command += [
                "--triage",
                "--allow-live-inference",
                "--estimated-call-microusd",
                str(args.estimated_call_microusd),
            ]
        result = subprocess.run(
            command,
            env=child,
            check=False,
            timeout=300,
        )
        return result.returncode
    except (OSError, ValueError, ConfigError, ProbeError, subprocess.TimeoutExpired):
        print(json.dumps({"status": "STOPPED", "reason": "COLLECTION_LAUNCH_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
