"""Fail-closed parsing for a model's bounded triage JSON response."""

import json

from pydantic import ValidationError

from packages.contracts.repair import TriageProposal


class TriageError(Exception):
    """Safe model-output rejection without retaining raw response text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _unwrap_json(content: str) -> str:
    """Accept a complete JSON code fence but never prose surrounding it."""
    if content.startswith("```json\n") and content.endswith("\n```"):
        return content.removeprefix("```json\n").removesuffix("\n```")
    return content


def parse_triage_response(content: str) -> TriageProposal:
    """Accept one bounded JSON object matching the typed proposal contract."""
    if not isinstance(content, str) or not 1 <= len(content.encode("utf-8")) <= 16384:
        raise TriageError("TRIAGE_OUTPUT_SIZE")
    try:
        payload = json.loads(_unwrap_json(content))
    except json.JSONDecodeError:
        raise TriageError("TRIAGE_OUTPUT_JSON") from None
    if not isinstance(payload, dict):
        raise TriageError("TRIAGE_OUTPUT_OBJECT")
    try:
        return TriageProposal.model_validate(payload)
    except ValidationError as exc:
        if any(error["type"] == "missing" for error in exc.errors()):
            raise TriageError("TRIAGE_OUTPUT_REQUIRED_FIELDS") from None
        raise TriageError("TRIAGE_OUTPUT_CONTRACT") from None
