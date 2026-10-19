"""The contingency runner has a fixed source/argv and never executes locally."""

import ast
from uuid import uuid4

import httpx
import pytest

from packages.contracts.repair import ReproductionProposal
from packages.contracts.sandbox import RunnerError
from services.runner.checkout_benchmark import CheckoutBenchmarkBackend, fixture_source
from services.runner.fixture_guest import GUEST_PROGRAM
from services.runner.remote import SandboxConfig
from tests.unit.test_patch_admission import DIFF


def proposal() -> ReproductionProposal:
    return ReproductionProposal(
        target_path="tests/test_generated.py",
        behavior="retry once",
        failure_signature="RF_DUPLICATE_TRANSACTION",
        test_source="def test_retry():\n    pass\n",
    )


def backend(handler) -> CheckoutBenchmarkBackend:
    return CheckoutBenchmarkBackend(
        SandboxConfig.from_env(
            {
                "NEBIUS_API_KEY": "synthetic-key",
                "NEBIUS_PROJECT_ID": "synthetic-project",
                "SANDBOX_IMAGE_UUID": str(uuid4()),
            }
        ),
        transport=httpx.MockTransport(handler),
    )


def test_fixed_fixture_request_is_network_disabled_and_one_shot() -> None:
    import json

    ast.parse(GUEST_PROGRAM)  # Syntax inspection only: do not execute guest locally.
    assert "CheckoutService" in fixture_source()
    operation = uuid4()

    def respond(request):
        payload = json.loads(request.content)
        assert payload["networking"] == {"enabled": False}
        assert payload["env"] == {} and payload["preserve_env"] is False
        assert payload["shell"] is False and payload["disposable"] is True
        assert payload["args"][:4] == ["-I", "-S", "-c", GUEST_PROGRAM]
        return httpx.Response(201, json={"uuid": str(operation)})

    client = backend(respond)
    try:
        assert client.start_checkout(proposal()) == operation
        with pytest.raises(RunnerError, match="S_DUPLICATE_SUBMISSION"):
            client.start_checkout(proposal())
    finally:
        client.close()


def test_fixture_rejects_non_fixture_patch_before_network(caplog) -> None:
    client = backend(lambda request: pytest.fail("forbidden network"))
    try:
        with pytest.raises(RunnerError, match="FIXTURE_PATCH_PATH_DENIED"):
            client.start_checkout(proposal(), DIFF)
        assert '"policy_decision": "FIXTURE_PATCH_PATH_DENIED"' in caplog.text
    finally:
        client.close()


def test_fixture_rejects_arbitrary_command_before_network(caplog) -> None:
    client = backend(lambda request: pytest.fail("forbidden network"))
    try:
        with pytest.raises(RunnerError, match="FIXTURE_COMMAND_DENIED"):
            client.start_checkout(proposal(), command_id="sh -c arbitrary")
        assert '"policy_decision": "FIXTURE_COMMAND_DENIED"' in caplog.text
        assert "sh -c arbitrary" not in caplog.text
    finally:
        client.close()
