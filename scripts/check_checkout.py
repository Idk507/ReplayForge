"""Offline-only validation of saved M2 fixture evidence; makes no cloud calls."""

import json
from pathlib import Path

from pydantic import ValidationError

from packages.contracts.repair import PatchProposal, TriageProposal
from services.verifier.checkout import inspect_checkout


def main() -> int:
    """Read bounded, fixed local evidence artifacts and print safe check results."""
    root = Path(__file__).resolve().parents[1] / "docs/evidence/m2-checkout-e2e"
    try:
        contents = []
        for name in ("report.json", "triage.json", "patch.json"):
            with (root / name).open("rb") as stream:
                body = stream.read(131073)
            if len(body) > 131072:
                raise ValueError("EVIDENCE_LIMIT")
            contents.append(body)
        report = json.loads(contents[0])
        if not isinstance(report, dict):
            raise ValueError("EVIDENCE_SCHEMA")
        result = inspect_checkout(
            report,
            TriageProposal.model_validate_json(contents[1]),
            PatchProposal.model_validate_json(contents[2]),
        )
    except (OSError, ValueError, ValidationError):
        print(json.dumps({"status": "INCONCLUSIVE", "error_class": "EVIDENCE_INVALID"}))
        return 2
    print(result.model_dump_json())
    return 0 if result.status == "M2_FIXTURE_ACCEPTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
