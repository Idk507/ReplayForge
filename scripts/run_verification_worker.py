"""Run the retained-evidence verification worker with an external trust host.

No I/O occurs without --run. The operator-owned trust module must expose
provide_verification_trust() and return externally provisioned trust; this
entry point never reads or creates signing keys itself.
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
import signal
from threading import Event
from uuid import UUID

from services.orchestrator.verification_worker import (
    ProductionVerificationWorker,
    VerificationTrust,
    VerificationWorkerError,
)

_FORBIDDEN_INHERITED_SECRETS = (
    "GITHUB_CLIENT_SECRET",
    "NEBIUS_API_KEY",
    "TAVILY_API_KEY",
    "REPLAYFORGE_AUTH_DATABASE_URL",
    "REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX",
    "REPLAYFORGE_AUTH_HMAC_KEY_HEX",
    "REPLAYFORGE_GITHUB_READ_TOKEN",
    "REPLAYFORGE_OPERATOR_BEARER",
)


def _external_trust(module_path: str) -> VerificationTrust:
    """Load trust from an explicitly configured operator module."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,190}", module_path) is None:
        raise VerificationWorkerError("VERIFICATION_TRUST_PROVIDER_INVALID")
    try:
        module = importlib.import_module(module_path)
        factory = module.provide_verification_trust
        trust = factory()
    except Exception:
        raise VerificationWorkerError("VERIFICATION_TRUST_UNAVAILABLE") from None
    if not isinstance(trust, VerificationTrust):
        raise VerificationWorkerError("VERIFICATION_TRUST_INVALID")
    return trust


def main() -> int:
    """Start one bounded production worker process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--max-ticks", type=int, choices=range(1, 1001), default=1)
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if not args.run:
        return 2
    if any(os.environ.get(name) for name in _FORBIDDEN_INHERITED_SECRETS):
        return 2
    try:
        trust = _external_trust(
            os.environ.get("REPLAYFORGE_VERIFICATION_TRUST_PROVIDER", "")
        )
        worker = ProductionVerificationWorker.from_env(trust)
        worker_id = UUID(os.environ["REPLAYFORGE_VERIFICATION_WORKER_ID"])
        stop = Event()
    except (KeyError, ValueError, VerificationWorkerError):
        return 2

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        worker.serve(
            worker_id,
            stop,
            max_ticks=args.max_ticks,
            idle_seconds=args.idle_seconds,
        )
    except VerificationWorkerError:
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
