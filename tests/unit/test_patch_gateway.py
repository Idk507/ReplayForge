"""No model claim can bypass patch admission or modify a reproduction test."""

import json

import httpx
import pytest

from packages.providers.nebius import NebiusConfig, ProbeError
from packages.providers.patch import request_patch
from tests.unit.test_patch_admission import DIFF, context


@pytest.mark.parametrize(
    "fault",
    [None, "test_change", "file_claim", "truncated", "extra", "http", "no_newline"],
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
        if fault == "no_newline":
            proposal["replacements"] = {"src/a.py": "x=2"}
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

    if fault and fault != "no_newline":
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


@pytest.mark.parametrize("source", ["x=1\r\n", "x=1\0\n"])
def test_unrepresentable_source_is_rejected_before_paid_request(source: str) -> None:
    run, grant, owner = context()
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"})
    reservations = []

    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call provider")

    with pytest.raises(ProbeError, match="PATCH_CONTEXT_DENIED"):
        request_patch(
            config,
            {"src/a.py": source},
            "failure",
            run,
            grant,
            owner,
            transport=httpx.MockTransport(forbidden),
            before_submit=reservations.append,
        )
    assert reservations == []


def test_source_without_final_newline_produces_exactly_applicable_diff() -> None:
    from services.runner.candidate_workspace import _apply

    run, grant, owner = context()
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"})
    response = {
        "replacements": {"src/a.py": "x=2\n"},
        "justification": "fix",
        "expected_test_delta": "passes",
        "risk_notes": "not executed",
    }
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(response)},
                    }
                ]
            },
        )
    )
    proposal, _ = request_patch(
        config, {"src/a.py": "x=1"}, "failure", run, grant, owner, transport=transport
    )
    assert "\\ No newline at end of file\n" in proposal.unified_diff
    assert _apply({"src/a.py": b"x=1"}, proposal.unified_diff) == {"src/a.py": b"x=2\n"}


def test_gateway_allows_explicit_checkout_source_and_rejects_checkout_tests() -> None:
    run, grant, owner = context()
    grant = grant.model_copy(update={"approved_paths": ("checkout/",)})
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"})
    requested = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(json.loads(request.content)["messages"][1]["content"])
        body = {
            "replacements": {"checkout/payments.py": "x=2\n"},
            "justification": "fix",
            "expected_test_delta": "pass",
            "risk_notes": "not executed",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(body)}}
                ]
            },
        )

    proposal, admission = request_patch(
        config,
        {"checkout/payments.py": "x=1\n"},
        "failure",
        run,
        grant,
        owner,
        transport=httpx.MockTransport(respond),
    )
    assert admission.files_changed == ("checkout/payments.py",)
    assert "checkout/payments.py" in requested[0]
    with pytest.raises(ProbeError, match="PATCH_CONTEXT_DENIED"):
        request_patch(
            config,
            {"checkout/tests/test_payments.py": "assert False\n"},
            "failure",
            run,
            grant,
            owner,
            transport=httpx.MockTransport(lambda _: pytest.fail("provider called")),
        )


def test_missing_newline_marker_cannot_match_a_terminated_source() -> None:
    from services.runner.candidate_workspace import _apply
    from services.runner.patches import PatchDenied, _paths

    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-x=1\n\\ No newline at end of file\n+x=2\n"
    )
    assert _paths(diff)[0] == ("src/a.py",)
    with pytest.raises(PatchDenied, match="PATCH_SOURCE_CONTEXT_MISMATCH"):
        _apply({"src/a.py": b"x=1\n"}, diff)
    with pytest.raises(PatchDenied, match="PATCH_HUNK"):
        _paths(
            diff.replace(
                "-x=1\n\\ No newline at end of file\n+x=2\n",
                "-x=1\n+x=2\n\\ No newline at end of file\n",
            )
        )
