import pytest
from pydantic import ValidationError

from packages.contracts.repair import TriageProposal

VALID_SOURCE = "import unittest\n\nclass RetryTest(unittest.TestCase):\n    pass\n"


def test_triage_proposal_accepts_bounded_structured_output() -> None:
    result = TriageProposal.model_validate(
        {
            "hypotheses": [
                {
                    "claim": "duplicate charge",
                    "supporting_refs": ["sha256:" + "a" * 64],
                    "uncertainty": "LOW",
                }
            ],
            "reproduction": {
                "target_path": "tests/test_checkout.py",
                "behavior": "retry charges once",
                "failure_signature": "2 != 1",
                "test_source": VALID_SOURCE,
            },
        }
    )
    assert result.reproduction.target_path == "tests/test_checkout.py"
    assert len(result.reproduction.test_source_sha256()) == 64


@pytest.mark.parametrize("path", ["../x.py", "app/x.py", "tests/x.txt"])
def test_triage_proposal_rejects_unapproved_test_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="REPRODUCTION_PATH_DENIED"):
        TriageProposal.model_validate(
            {
                "hypotheses": [
                    {"claim": "x", "supporting_refs": ["ref"], "uncertainty": "HIGH"}
                ],
                "reproduction": {
                    "target_path": path,
                    "behavior": "x",
                    "failure_signature": "x",
                    "test_source": "pass\n",
                },
            }
        )


@pytest.mark.parametrize("source", ["def broken(:\n", "pass\x00"])
def test_triage_proposal_rejects_non_python_test_source(source: str) -> None:
    with pytest.raises(ValidationError, match="REPRODUCTION_SOURCE_DENIED"):
        TriageProposal.model_validate(
            {
                "hypotheses": [
                    {"claim": "x", "supporting_refs": ["ref"], "uncertainty": "HIGH"}
                ],
                "reproduction": {
                    "target_path": "tests/test_reproduction.py",
                    "behavior": "x",
                    "failure_signature": "x",
                    "test_source": source,
                },
            }
        )
