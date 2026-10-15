"""Offline boundary tests; these do not prove live provider availability."""

import json

import httpx
import pytest

from packages.providers.nebius import NebiusConfig, ProbeError, probe


def config(**overrides: str) -> NebiusConfig:
    return NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret", **overrides})


@pytest.mark.parametrize(
    "url",
    [
        "http://api.tokenfactory.us-central1.nebius.com/v1/",
        "https://evil.example/v1/",
        "https://localhost/v1/",
        "https://user:secret@api.tokenfactory.nebius.com/v1/",
        "https://api.tokenfactory.nebius.com/v1/?token=secret",
        "https://api.tokenfactory.nebius.com:444/v1/",
    ],
)
def test_rejects_credential_exfiltration_endpoints(url: str) -> None:
    with pytest.raises(ProbeError, match="CONFIG_INVALID"):
        config(NEBIUS_BASE_URL=url)


def test_missing_credentials_are_safe() -> None:
    with pytest.raises(ProbeError, match="CONFIG_MISSING_KEY"):
        NebiusConfig.from_env({})


def test_probe_records_metadata_without_content_or_credentials() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": config().model}]})
        assert json.loads(request.content)["model"] == config().model
        return httpx.Response(
            200,
            json={
                "model": config().model,
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "test-secret"}}
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            },
        )

    result = probe(config(), transport=httpx.MockTransport(respond))
    assert result.input_tokens == 8
    assert result.evidence_origin == "TEST_TRANSPORT"
    assert result.output_tokens == 2
    assert result.estimated_cost_usd is None
    assert "test-secret" not in result.model_dump_json()
    assert result.trace_id
    assert result.observed_at.utcoffset() is not None


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "M_AUTH"),
        (403, "M_AUTH"),
        (429, "M_RATE_LIMIT"),
        (500, "M_UNAVAILABLE"),
        (302, "M_REDIRECT_DENIED"),
    ],
)
def test_provider_errors_are_sanitized_without_retries(status: int, code: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status, text="test-secret", headers={"location": "https://evil.example"}
        )

    with pytest.raises(ProbeError, match=code) as error:
        probe(config(), transport=httpx.MockTransport(respond))
    assert "test-secret" not in str(error.value)
    assert len(calls) == 1


def test_missing_model_blocks_completion() -> None:
    with pytest.raises(ProbeError, match="M_MODEL_UNAVAILABLE"):
        probe(
            config(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": []})
            ),
        )


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "model": "other",
        },
    ],
)
def test_incomplete_or_mismatched_completion_is_not_success(
    body: dict[str, object],
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": config().model}]})
        return httpx.Response(200, json={"model": config().model, **body})

    with pytest.raises(ProbeError, match="M_SCHEMA_INVALID"):
        probe(config(), transport=httpx.MockTransport(respond))


def test_timeout_is_classified() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test-secret", request=request)

    with pytest.raises(ProbeError, match="M_TIMEOUT"):
        probe(config(), transport=httpx.MockTransport(respond))


def test_non_nvidia_route_is_rejected() -> None:
    with pytest.raises(ProbeError, match="CONFIG_INVALID"):
        config(NEBIUS_MODEL_PRIMARY="other/model")


@pytest.mark.parametrize(
    "content,code",
    [
        (b"not-json", "M_SCHEMA_INVALID"),
        (b"x" * 1_000_001, "M_RESPONSE_TOO_LARGE"),
    ],
    ids=["invalid-json", "oversized-response"],
)
def test_malformed_or_oversized_response_is_rejected(content: bytes, code: str) -> None:
    with pytest.raises(ProbeError, match=code):
        probe(
            config(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=content)
            ),
        )


def test_missing_usage_stays_unknown() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": config().model}]})
        return httpx.Response(
            200,
            json={
                "model": config().model,
                "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
            },
        )

    result = probe(config(), transport=httpx.MockTransport(respond))
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_cli_default_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import check_nebius

    monkeypatch.setenv("NEBIUS_API_KEY", "test-secret")
    monkeypatch.setattr("sys.argv", ["check_nebius"])

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline CLI invoked provider")

    monkeypatch.setattr(check_nebius, "probe", forbidden)
    assert check_nebius.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_RUN"


def test_cli_missing_key_fails_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import check_nebius

    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["check_nebius", "--live"])
    assert check_nebius.main() == 2
    assert json.loads(capsys.readouterr().out)["error_class"] == "CONFIG_MISSING_KEY"
