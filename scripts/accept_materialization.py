"""One live inert upload probe, only within an explicitly disposable test DB.

This is operator acceptance scaffolding, not a production worker. Its synthetic
source identity and reproduction are never promoted to live repair evidence.
"""

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from packages.contracts.sandbox import RunnerError
from services.orchestrator.materialization import materialize_once
from services.persistence.store import StoreError
from services.runner.remote import SandboxConfig
from tests.integration.test_materialization import setup
from tests.integration.test_postgres import prepare_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("MATERIALIZATION_NOT_RUN")
        return 2
    dsn = os.environ.get("REPLAYFORGE_TEST_DATABASE_URL", "")
    try:
        info = conninfo_to_dict(dsn)
        if not re.fullmatch(r"rf_test_[a-f0-9]{32}", str(info.get("dbname", ""))):
            raise ValueError
    except (ValueError, TypeError):
        print("DISPOSABLE_DATABASE_REQUIRED")
        return 2
    attempt = Path("docs/evidence/materialization-live-attempt.jsonl")
    if attempt.exists():
        print("LIVE_MATERIALIZATION_ALREADY_ATTEMPTED_NO_RESUBMISSION")
        return 2
    config = SandboxConfig.from_env()
    store = prepare_store()
    owner, run, lease, files, _ = setup(store)
    if files != {"src/a.py": b"x=1\n"}:
        raise ValueError("INERT_FIXTURE_CHANGED")

    def record(phase: str, **metadata: object) -> None:
        with attempt.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "phase": phase,
                        "observed_at": datetime.now(UTC).isoformat(),
                        **metadata,
                    }
                )
                + "\n"
            )
            output.flush()
            os.fsync(output.fileno())

    # Exclusive claim precedes the only possible POST, including across a new DB.
    with attempt.open("x", encoding="utf-8"):
        pass
    record(
        "CLAIMED",
        run_id=str(run.run_id),
        input_kind="SYNTHETIC_INERT_FIXTURE",
        image_uuid=str(config.image_uuid),
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            lease = store.heartbeat(lease)
            state = store.get(run.run_id, owner)
            result = materialize_once(
                store,
                lease,
                owner,
                config,
                files,
                lambda: None,
                expected_version=state.version,
                allow_live=True,
            )
            if result is not None:
                path = Path("docs/evidence/materialization-live-result.json")
                payload = {
                    "status": "WORKSPACE_UPLOADED_AND_MEASURED",
                    "input_kind": "SYNTHETIC_INERT_FIXTURE",
                    "result": result.model_dump(mode="json"),
                    "tests_executed": 0,
                    "inference_calls": 0,
                    "can_authorize_execution": False,
                    "limitations": [
                        "Synthetic source/reproduction identity, not a GitHub repair.",
                        "Fixed extractor only; source/test code was not executed.",
                        "Unsigned measurement is not OCI/capability attestation.",
                        "Saved image remains subject to provider retention.",
                    ],
                }
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                record(
                    "MEASURED",
                    operation_id=str(result.operation_id),
                    result_image_uuid=str(result.result_image_uuid),
                )
                print("WORKSPACE_UPLOADED_AND_MEASURED; NO_TESTS_OR_VERIFICATION")
                return 0
            time.sleep(1)
        record("COLLECTION_DEADLINE")
        print("MATERIALIZATION_COLLECTION_DEADLINE; NO_RESUBMISSION")
        return 1
    except (RunnerError, StoreError, OSError) as error:
        code = (
            str(error)
            if isinstance(error, (RunnerError, StoreError))
            else type(error).__name__
        )
        record("ERROR", error_class=code)
        print("MATERIALIZATION_FAILED; " + code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
