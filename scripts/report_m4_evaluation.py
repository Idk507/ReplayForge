"""Validate and summarize M4 case observations without executing fixture code."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pydantic import ValidationError

from services.runner.benchmark_evaluation import (
    BenchmarkAttempt,
    BenchmarkCase,
    summarize_benchmark,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 1024 * 1024
SAFE_FILENAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}\.json$")


def _read_input(
    path: Path,
) -> tuple[tuple[BenchmarkCase, ...], tuple[BenchmarkAttempt, ...]]:
    """Load bounded declarative records; no input string is executed."""
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("EVALUATION_INPUT_TOO_LARGE")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"cases", "attempts"}:
        raise ValueError("EVALUATION_INPUT_INVALID")
    if not isinstance(value["cases"], list) or not isinstance(value["attempts"], list):
        raise ValueError("EVALUATION_INPUT_INVALID")
    cases = tuple(BenchmarkCase.model_validate(row) for row in value["cases"])
    attempts = tuple(BenchmarkAttempt.model_validate(row) for row in value["attempts"])
    return cases, attempts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "benchmarks/m4-evaluation-input.json"
    )
    parser.add_argument(
        "--output",
        default="m4-evaluation-status.json",
        help="Safe JSON filename written under docs/evidence/",
    )
    args = parser.parse_args()
    if SAFE_FILENAME.fullmatch(args.output) is None:
        parser.error("output must be a simple JSON filename")
    try:
        cases, attempts = _read_input(args.input)
        report = summarize_benchmark(cases, attempts)
    except (OSError, ValueError, ValidationError, TypeError):
        print("EVALUATION_INPUT_INVALID")
        return 2
    destination = ROOT / "docs/evidence" / args.output
    destination.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    public_report = ROOT / "apps/web/public/evidence/m4-evaluation-status.json"
    public_report.parent.mkdir(parents=True, exist_ok=True)
    public_report.write_bytes(destination.read_bytes())
    totals = report.totals
    print(
        json.dumps(
            {
                "report": str(destination.relative_to(ROOT)),
                "public_report": str(public_report.relative_to(ROOT)),
                "evidence_authority": report.evidence_authority,
                "available_cases": totals.available_cases,
                "unavailable_cases": totals.unavailable_cases,
                "attempted_cases": totals.attempted_cases,
                "live_attempts": totals.live_attempts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
