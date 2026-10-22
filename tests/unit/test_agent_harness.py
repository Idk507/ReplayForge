"""A bounded harness routes fixed stages; no real I/O or model calls."""

from uuid import uuid4

import httpx
import pytest

from packages.providers.nebius import NebiusConfig
from services.persistence.store import Store, StoreError
from tests.unit.test_model_dispatch import policy
from tests.unit.test_worker_polling import Stop, unused_loader


def test_harness_stops_before_claiming(monkeypatch):
    from services.orchestrator.harness import serve_harness

    monkeypatch.setattr(
        "services.orchestrator.harness.tick", lambda *a, **k: pytest.fail("claimed")
    )
    result = serve_harness(Store("unused"), uuid4(), unused_loader, Stop(stopped=True))
    assert result.steps == 0 and result.stop_requested


def test_live_inference_requires_explicit_authorization_before_claim():
    from services.orchestrator.harness import serve_harness

    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    with pytest.raises(ValueError, match="HARNESS_LIVE_NOT_AUTHORIZED"):
        serve_harness(
            Store("unused"),
            uuid4(),
            unused_loader,
            Stop(),
            model_config=config,
            cost_policy=policy(config),
        )


def test_harness_alternates_stages_without_starvation(monkeypatch):
    from services.orchestrator.harness import serve_harness
    from services.orchestrator.worker import WorkerResult

    calls = []

    def collect(*args, **kwargs):
        assert kwargs["queue_triage"] is True
        calls.append("collect")
        return WorkerResult(status="PROCESSED")

    def triage(*args, **kwargs):
        calls.append("triage")
        return WorkerResult(status="PROCESSED")

    monkeypatch.setattr("services.orchestrator.harness.tick", collect)
    monkeypatch.setattr("services.orchestrator.harness.tick_triage", triage)
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    result = serve_harness(
        Store("unused"),
        uuid4(),
        unused_loader,
        Stop(),
        model_config=config,
        cost_policy=policy(config),
        transport=httpx.MockTransport(lambda r: pytest.fail("I/O")),
        max_steps=4,
    )
    assert calls == ["triage", "collect", "triage", "collect"]
    assert result.triaged == result.collected == 2
    assert result.processed == result.steps == 4


def test_harness_does_not_retry_failed_stage(monkeypatch):
    from services.orchestrator.harness import serve_harness

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise StoreError("SUBMISSION_UNCERTAIN")

    monkeypatch.setattr("services.orchestrator.harness.tick", fail)
    with pytest.raises(StoreError, match="SUBMISSION_UNCERTAIN"):
        serve_harness(Store("unused"), uuid4(), unused_loader, Stop())
    assert calls == [1]


@pytest.mark.parametrize(
    "opts",
    [
        {"max_steps": 0},
        {"max_steps": True},
        {"idle_seconds": float("nan")},
        {"idle_seconds": 61},
    ],
)
def test_harness_rejects_unbounded_configuration(opts):
    from services.orchestrator.harness import serve_harness

    with pytest.raises(ValueError, match="HARNESS_LIMIT"):
        serve_harness(Store("unused"), uuid4(), unused_loader, Stop(), **opts)


def test_harness_waits_only_after_both_queues_are_idle(monkeypatch):
    from services.orchestrator.harness import serve_harness
    from services.orchestrator.worker import WorkerResult

    def idle(*a, **k):
        return WorkerResult(status="IDLE")

    monkeypatch.setattr("services.orchestrator.harness.tick", idle)
    monkeypatch.setattr("services.orchestrator.harness.tick_triage", idle)
    config = NebiusConfig.from_env({"NEBIUS_API_KEY": "synthetic"})
    stop = Stop(stop_on_wait=True)
    result = serve_harness(
        Store("unused"),
        uuid4(),
        unused_loader,
        stop,
        model_config=config,
        cost_policy=policy(config),
        transport=httpx.MockTransport(lambda r: pytest.fail("I/O")),
    )
    assert result.steps == result.idle == 2
    assert stop.waits == [1] and result.stop_requested


def test_harness_inference_off_never_touches_triage(monkeypatch):
    from services.orchestrator.harness import serve_harness
    from services.orchestrator.worker import WorkerResult

    monkeypatch.setattr(
        "services.orchestrator.harness.tick",
        lambda *a, **k: WorkerResult(status="IDLE"),
    )
    monkeypatch.setattr(
        "services.orchestrator.harness.tick_triage",
        lambda *a, **k: pytest.fail("inference enabled"),
    )
    result = serve_harness(Store("unused"), uuid4(), unused_loader, Stop(), max_steps=1)
    assert result.steps == result.idle == 1 and not result.inference_enabled
