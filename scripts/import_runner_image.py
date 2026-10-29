"""Opt-in operator command to import or recover one pinned sandbox image."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Sequence

from packages.contracts.sandbox import RunnerError
from services.persistence.store import Store, StoreError
from services.runner.image_import_controller import ImageImportController
from services.runner.one_shot_image_import import ImageImportConfig, ImageImportPlan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="Digest-pinned docker:// OCI ref"
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-polls", type=int, default=20)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize one import POST if absent, or GET-only recovery if known",
    )
    return parser


async def _run_live(args: argparse.Namespace) -> int:
    config = ImageImportConfig.from_env(os.environ)
    dsn = os.environ.get("REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL")
    if not dsn:
        raise RunnerError("S_IMPORT_DATABASE_CONFIG_MISSING")
    controller = ImageImportController(Store(dsn), config)
    report = await controller.run(
        ImageImportPlan(
            source=args.source,
            timeout_seconds=args.timeout_seconds,
        ),
        allow_live=True,
        max_polls=args.max_polls,
    )
    print(report.model_dump_json())
    return 0 if report.status == "SUCCESS" else 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run offline by default; `--live` is the only provider-call switch."""
    args = _parser().parse_args(argv)
    try:
        plan = ImageImportPlan(source=args.source, timeout_seconds=args.timeout_seconds)
        request_sha256 = plan.request_digest()
        source_digest = plan.source.rsplit("@sha256:", 1)[-1]
        if not 1 <= args.max_polls <= 20:
            raise RunnerError("S_POLICY_DENIED")
        if not args.live:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": "NOT_RUN",
                        "policy_decision": "DENY_LIVE_NOT_AUTHORIZED",
                        "source_digest": source_digest,
                        "request_sha256": request_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0
        return asyncio.run(_run_live(args))
    except (RunnerError, StoreError) as error:
        code = str(error)
        safe_code = (
            code if re.fullmatch(r"[A-Z0-9_]{1,64}", code) else type(error).__name__
        )
        print(
            json.dumps(
                {"schema_version": "1.0", "status": "ERROR", "error_code": safe_code},
                sort_keys=True,
            )
        )
        return 2
    except ValueError:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "ERROR",
                    "error_code": "INVALID_INPUT",
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
