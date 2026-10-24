"""Explicit local launcher: pass only collection workload credentials to worker."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true")
    p.add_argument("--env-file", type=Path, default=Path(".env.worker"))
    p.add_argument("--max-ticks", type=int, choices=range(1, 1001), default=1)
    args = p.parse_args()
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    try:
        path = args.env_file
        if path.is_symlink() or path.stat().st_size > 8192:
            raise ValueError
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key not in ALLOWED or key in values:
                raise ValueError
            values[key] = value
        BrokerWorkerConfig.from_env(values)
        child = {k: v for k, v in os.environ.items() if k.upper() in SYSTEM}
        child.update(values)
        child["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.run_authenticated_worker",
                "--run",
                "--max-ticks",
                str(args.max_ticks),
            ],
            env=child,
            check=False,
            timeout=300,
        )
        return result.returncode
    except (OSError, ValueError, ConfigError, subprocess.TimeoutExpired):
        print(json.dumps({"status": "STOPPED", "reason": "COLLECTION_LAUNCH_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
