"""No model claim can bypass patch admission or modify a reproduction test."""

import json

import httpx
import pytest

from packages.providers.nebius import NebiusConfig, ProbeError
from packages.providers.patch import request_patch
from tests.unit.test_patch_admission import DIFF, context


@pytest.mark.parametrize(
    "fault", [None, "test_change", "file_claim", "truncated", "extra", "http"]
)
def test_patch_generation_admits_only_exact_allowed_diff(fault: str | None) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload["reasoning_effort"] == "none"
        assert payload["store"] is False
        assert payload["max_completion_tokens"] == 1200
        if fault == "http":
            return httpx.Response(503)
        proposal = {
            "replacements": {
                "tests/test_repro.py"
                if fault == "test_change"
                else "app/b.py"
                if fault == "file_claim"
                else "src/a.py": "x=2\n"
            },
            "justification": "synthetic change",
            "expected_test_delta": "assertion should pass",
            "risk_notes": "not executed",
        }
        if fault == "extra":
            proposal["command"] = "forbidden"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if fault == "truncated" else "stop",
                        "message": {"content": json.dumps(proposal)},
                    }
                ]
            },
        )

    run, grant, owner = context()
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"})

    def invoke():
        return request_patch(
            config,
            {"src/a.py": "x=1\n"},
            "synthetic failure",
            run,
            grant,
            owner,
            transport=httpx.MockTransport(respond),
        )

    if fault:
        with pytest.raises(ProbeError):
            invoke()
    else:
        proposal, admission = invoke()
        assert proposal.unified_diff == DIFF
        assert admission.files_changed == ("src/a.py",)
        assert not admission.can_authorize_execution
    assert calls == 1


def test_invalid_stage_or_source_never_contacts_provider() -> None:
    from packages.contracts.run import Stage

    run, grant, owner = context()
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"})

    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call provider")

    with pytest.raises(ProbeError, match="PATCH_GENERATION_DENIED"):
        request_patch(
            config,
            {"src/a.py": "x=1"},
            "failure",
            run.model_copy(update={"current_stage": Stage.TRIAGING}),
            grant,
            owner,
            transport=httpx.MockTransport(forbidden),
        )
    with pytest.raises(ProbeError, match="PATCH_CONTEXT_DENIED"):
        request_patch(
            config,
            {"tests/test_a.py": "pass"},
            "failure",
            run,
            grant,
            owner,
            transport=httpx.MockTransport(forbidden),
        )
