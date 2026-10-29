"""Offline-default operator entrypoint for one pinned pytest image preparation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from packages.contracts.sandbox import RunnerError
from services.persistence.image_imports import ImageImportJournal
from services.persistence.store import Store, StoreError
from services.runner.image_preparation import (
    INSTALL_PROGRAM,
    ImagePreparationPlan,
    NebiusImagePreparation,
)
from services.runner.one_shot_image_import import ImageImportConfig

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/evidence/nebius-pytest-wheelhouse.json"
WHEELHOUSE = ROOT / "artifacts/nebius-pytest-wheelhouse"
DATABASE_ENV = "REPLAYFORGE_IMAGE_IMPORT_DATABASE_URL"
EXPECTED_PROJECT_ID = "aiproject-e00bzf586kz2r694kr"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--submit",
        action="store_true",
        help=(
            "authorize one new preparation only when no matching journal intent exists"
        ),
    )
    action.add_argument(
        "--recover",
        action="store_true",
        help="GET-only recovery of an already journaled known operation",
    )
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _error_code(error: BaseException) -> str:
    code = str(error)
    return code if re.fullmatch(r"[A-Z0-9_]{1,64}", code) else type(error).__name__


async def _execute(
    *,
    plan: ImagePreparationPlan,
    submit: bool,
    env: dict[str, str],
) -> int:
    dsn = env.get(DATABASE_ENV)
    if not dsn:
        raise RunnerError("S_IMAGE_JOURNAL_DATABASE_CONFIG_MISSING")
    config = ImageImportConfig.from_env(env)
    if config.project_id != EXPECTED_PROJECT_ID:
        raise RunnerError("S_PROJECT_BINDING_DENIED")
    journal = ImageImportJournal(Store(dsn), config.project_id, plan)

    existing = None
    try:
        existing = journal.history()
    except StoreError as error:
        if str(error) != "IMAGE_IMPORT_NOT_FOUND":
            raise

    if submit:
        if existing is not None:
            raise RunnerError("S_PREPARATION_ALREADY_CLAIMED")
        try:
            async with NebiusImagePreparation(
                config, plan, journal, allow_live=True
            ) as preparation:
                result = await preparation.prepare()
            recovered = journal.history()
        except (RunnerError, StoreError) as error:
            _emit_failure_checkpoint(error, plan, journal)
            return 2
    else:
        if (
            existing is None
            or existing.state != "OPERATION_KNOWN"
            or existing.operation_id is None
        ):
            raise RunnerError("S_OPERATION_NOT_RECOVERABLE")
        try:
            async with NebiusImagePreparation(
                config,
                plan,
                journal,
                allow_live=True,
                known_operation=existing.operation_id,
            ) as preparation:
                result = await preparation.recover()
            recovered = journal.history()
        except (RunnerError, StoreError) as error:
            _emit_failure_checkpoint(error, plan, journal)
            return 2

    _emit(
        {
            "schema_version": "1.0",
            "status": result.status,
            "attempt_id": str(recovered.attempt_id),
            "operation_id": str(result.operation_id),
            "base_image_uuid": str(result.base_image_uuid),
            "image_uuid": str(result.image_uuid),
            "request_sha256": result.request_sha256,
            "state": recovered.state,
        }
    )
    return 0


def _emit_failure_checkpoint(
    error: BaseException,
    plan: ImagePreparationPlan,
    journal: ImageImportJournal,
) -> None:
    """Report only durable, sanitized recovery identifiers after failure."""
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "status": "ERROR",
        "error_code": _error_code(error),
        "request_sha256": plan.request_digest(),
    }
    try:
        attempt = journal.history()
    except StoreError:
        attempt = None
    if attempt is not None:
        payload.update(
            {
                "attempt_id": str(attempt.attempt_id),
                "state": attempt.state,
                "operation_id": (
                    str(attempt.operation_id) if attempt.operation_id else None
                ),
                "event_count": len(attempt.events),
                "last_event_kind": attempt.events[-1].kind,
                "known_upload_count": sum(
                    event.kind == "UPLOAD_KNOWN" for event in attempt.events
                ),
            }
        )
    _emit(payload)


def main(argv: Sequence[str] | None = None) -> int:
    """Verify local inputs by default; remote work needs an explicit mode."""
    args = _parser().parse_args(argv)
    try:
        plan = ImagePreparationPlan.from_wheelhouse(
            manifest_path=MANIFEST,
            wheelhouse=WHEELHOUSE,
        )
        if not args.submit and not args.recover:
            _emit(
                {
                    "schema_version": "1.0",
                    "status": "NOT_RUN",
                    "policy_decision": "DENY_REMOTE_ACTION_NOT_SELECTED",
                    "base_image_uuid": str(plan.base_image),
                    "source_sha256": plan.journal_source_digest,
                    "request_sha256": plan.request_digest(),
                    "installer_sha256": hashlib.sha256(
                        INSTALL_PROGRAM.encode()
                    ).hexdigest(),
                    "wheel_count": len(plan.wheels),
                }
            )
            return 0
        return asyncio.run(
            _execute(plan=plan, submit=args.submit, env=dict(os.environ))
        )
    except (RunnerError, StoreError, OSError, ValueError) as error:
        _emit(
            {
                "schema_version": "1.0",
                "status": "ERROR",
                "error_code": _error_code(error),
                "request_sha256": (
                    plan.request_digest() if "plan" in locals() else None
                ),
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
