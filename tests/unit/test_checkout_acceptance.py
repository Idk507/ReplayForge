"""Replay retained live fixture metadata without making provider calls."""

import copy
import json
from pathlib import Path

import pytest

from packages.contracts.repair import PatchProposal, TriageProposal
from services.verifier.checkout import inspect_checkout

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m2-checkout-e2e"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "patch",
        "test",
        "source",
        "tree",
        "image",
        "duplicate",
        "setup",
        "no_test",
        "baseline_pass",
        "not_terminal",
        "missing",
    ],
)
def test_checkout_acceptance_binds_retained_artifacts(fault: str | None) -> None:
    report = json.loads((EVIDENCE / "report.json").read_bytes())
    report = copy.deepcopy(report)
    triage = TriageProposal.model_validate_json((EVIDENCE / "triage.json").read_bytes())
    patch = PatchProposal.model_validate_json((EVIDENCE / "patch.json").read_bytes())
    if fault == "patch":
        report["patch_ref"] = "sha256:" + "0" * 64
    elif fault == "test":
        report["candidate"]["test_sha256"] = "0" * 64
    elif fault == "source":
        report["candidate"]["source_commit"] = "0" * 40
    elif fault == "tree":
        report["clean_replay"]["tree"] = "0" * 40
    elif fault == "image":
        report["candidate"]["image_uuid"] = "00000000-0000-0000-0000-000000000001"
    elif fault == "duplicate":
        report["clean_replay"]["operation_id"] = report["candidate"]["operation_id"]
    elif fault == "setup":
        report["baseline"]["setup_error"] = True
    elif fault == "no_test":
        report["candidate"]["tests_ran"] = False
    elif fault == "baseline_pass":
        report["baseline"]["exit_code"] = 0
    elif fault == "not_terminal":
        report["candidate"]["compute_terminal_confirmed"] = False
    elif fault == "missing":
        del report["clean_replay"]
    result = inspect_checkout(report, triage, patch)
    assert (result.status == "M2_FIXTURE_ACCEPTED") == (fault is None)
    assert result.can_authorize_verification is False
    assert result.can_authorize_execution is False
