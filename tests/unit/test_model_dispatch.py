"""Request identity must be reserved before any outbound provider traffic."""

import hashlib

import httpx
import pytest

from packages.contracts.inference import ModelCostPolicy
from packages.providers.nebius import NebiusConfig
from packages.providers.triage import request_triage
from tests.unit.test_triage_gateway import response_for_any_context


def policy(config: NebiusConfig) -> ModelCostPolicy:
    return ModelCostPolicy(
        model=config.model,
        base_url=config.base_url,
        policy_version="synthetic-estimate-v1",
        estimated_spend_microusd=100_000,
    )


def test_reservation_precedes_request_and_binds_exact_wire_bytes() -> None:
    observed = []

    def reserve(identity):
        observed.append(identity)

    def respond(request):
        assert len(observed) == 1
        assert (
            observed[0].request_ref
            == "sha256:" + hashlib.sha256(request.content).hexdigest()
        )
        assert observed[0].timeout_seconds == 30
        return response_for_any_context()

    request_triage(
        NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"}),
        {"incident": "synthetic"},
        before_submit=reserve,
        transport=httpx.MockTransport(respond),
    )


def test_failed_reservation_never_contacts_provider() -> None:
    def denied(identity):
        raise RuntimeError("reservation denied")

    with pytest.raises(RuntimeError, match="reservation denied"):
        request_triage(
            NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"}),
            {"incident": "synthetic"},
            before_submit=denied,
            transport=httpx.MockTransport(
                lambda request: pytest.fail("forbidden network")
            ),
        )


@pytest.mark.parametrize("fault", ["missing", "model", "endpoint", "negative_cost"])
def test_missing_or_mismatched_policy_is_denied(fault: str) -> None:
    from packages.contracts.inference import RequestIdentity
    from packages.providers.nebius import ProbeError
    from services.orchestrator.repair_flow import _dispatch

    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    identity = RequestIdentity(
        request_ref="sha256:" + "a" * 64, model=config.model, base_url=config.base_url
    )
    cost = policy(config)
    if fault == "model":
        cost = cost.model_copy(update={"model": "nvidia/other-nemotron"})
    elif fault == "endpoint":
        cost = cost.model_copy(update={"base_url": "https://other.invalid/v1/"})
    elif fault == "negative_cost":
        cost = cost.model_copy(update={"estimated_spend_microusd": -1})
    with pytest.raises(ProbeError, match="MODEL_COST_POLICY_"):
        _dispatch(identity, None if fault == "missing" else cost)


def test_triage_stops_reading_when_response_limit_is_exceeded() -> None:
    from packages.providers.nebius import MAX_RESPONSE_BYTES, ProbeError

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * MAX_RESPONSE_BYTES
            yield b"x"
            pytest.fail("must stop before consuming the rest of the response")

    with pytest.raises(ProbeError, match="M_RESPONSE_TOO_LARGE"):
        request_triage(
            NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"}),
            {"incident": "synthetic"},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream())
            ),
        )
