"""Completed fixture runs cannot be resumed into duplicate cloud charges."""

import json
import sys

import pytest

from packages.contracts.repair import TriageProposal
from scripts import e2e_checkout
from scripts.e2e_checkout import main


@pytest.mark.parametrize(
    "status,candidate", [("FIXTURE_REPAIR_PASSED", False), ("INCONCLUSIVE", True)]
)
def test_resume_rejects_completed_or_partially_executed_candidate(
    tmp_path, monkeypatch, status, candidate
) -> None:
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "docs/evidence/m2-checkout-e2e"
    output.mkdir(parents=True)
    report = {"status": status}
    if candidate:
        report["candidate"] = {}
    body = json.dumps(report)
    (output / "report.json").write_text(body)
    monkeypatch.setattr(
        sys,
        "argv",
        ["e2e_checkout", "--live", "--env-file", "missing.env", "--resume-baseline"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert (output / "report.json").read_text() == body
    assert len(list(output.iterdir())) == 1


def test_invalid_generated_syntax_is_inconclusive_before_sandbox(
    tmp_path, monkeypatch
) -> None:
    """Mocked inference only; AST validation never executes generated code."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs/evidence").mkdir(parents=True)
    (tmp_path / "test.env").write_text(
        "NEBIUS_API_KEY=synthetic\nNEBIUS_PROJECT_ID=synthetic\n"
    )
    monkeypatch.setattr(
        sys, "argv", ["e2e_checkout", "--live", "--env-file", "test.env"]
    )
    monkeypatch.setattr(e2e_checkout, "fixture_source", lambda: "synthetic")

    def malformed(*args, **kwargs):
        return TriageProposal.model_validate(
            {
                "hypotheses": [
                    {
                        "claim": "synthetic",
                        "supporting_refs": ["source"],
                        "uncertainty": "LOW",
                    }
                ],
                "reproduction": {
                    "target_path": "tests/test_generated.py",
                    "behavior": "synthetic",
                    "failure_signature": "RF_DUPLICATE_TRANSACTION",
                    "test_source": "def broken(",
                },
            }
        )

    monkeypatch.setattr(e2e_checkout, "request_triage", malformed)
    monkeypatch.setattr(
        e2e_checkout,
        "run_checkout",
        lambda *args: pytest.fail("no sandbox for invalid syntax"),
    )
    assert main() == 2
    report = json.loads(
        (tmp_path / "docs/evidence/m2-checkout-e2e/report.json").read_text()
    )
    assert report["status"] == "INCONCLUSIVE"
    assert "baseline" not in report
    assert "broken(" not in json.dumps(report)
