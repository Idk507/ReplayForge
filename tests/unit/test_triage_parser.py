import json

import pytest

from services.orchestrator.triage import TriageError, parse_triage_response


def valid() -> str:
    return json.dumps(
        {
            "hypotheses": [
                {
                    "claim": "duplicate",
                    "supporting_refs": ["sha256:" + "a" * 64],
                    "uncertainty": "LOW",
                }
            ],
            "reproduction": {
                "target_path": "tests/test_checkout.py",
                "behavior": "one charge",
                "failure_signature": "2 != 1",
                "test_source": "def test_checkout():\n    assert True\n",
            },
        }
    )


def test_parser_accepts_strict_json_contract() -> None:
    assert (
        parse_triage_response(valid()).reproduction.target_path
        == "tests/test_checkout.py"
    )


def test_parser_accepts_a_complete_json_code_fence() -> None:
    assert parse_triage_response(f"```json\n{valid()}\n```").reproduction.test_source


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("not-json", "TRIAGE_OUTPUT_JSON"),
        ("[]", "TRIAGE_OUTPUT_OBJECT"),
        ("{}", "TRIAGE_OUTPUT_REQUIRED_FIELDS"),
        ("x" * 16385, "TRIAGE_OUTPUT_SIZE"),
        ("prefix ```json\n{}\n```", "TRIAGE_OUTPUT_JSON"),
    ],
)
def test_parser_rejects_invalid_or_oversized_model_output(
    content: str, code: str
) -> None:
    with pytest.raises(TriageError, match=code):
        parse_triage_response(content)
