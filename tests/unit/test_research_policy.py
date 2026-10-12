"""Negative research policy and adapter lifecycle tests; all searches are fake."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from packages.contracts.research import DocumentationQuery
from packages.policies.research import compile_query, sanitize_text
from packages.providers.tavily import TavilyConfig, TavilyResearchClient

KEY = "synthetic-private-key"


def config() -> TavilyConfig:
    return TavilyConfig.from_env({"ENABLE_TAVILY": "true", "TAVILY_API_KEY": KEY})


@pytest.mark.parametrize(
    "text",
    [
        KEY[:8] + "\x00" + KEY[8:],
        KEY[:8] + "<b></b>" + KEY[8:],
        KEY[:8] + "\u202e" + KEY[8:],
        KEY.replace("s", "&#115;"),
    ],
)
def test_normalization_cannot_reconstruct_key_after_redaction(text: str) -> None:
    sanitized, _ = sanitize_text(text, KEY, 1000)
    assert KEY not in sanitized
    assert "[REDACTED]" in sanitized


def test_compiled_query_is_public_and_bounded() -> None:
    query, domains = compile_query(
        DocumentationQuery(package="python", version="3.12", topic="transactions")
    )
    assert query == "python 3.12 transaction control official documentation"
    assert domains == ("docs.python.org",)


@pytest.mark.parametrize(
    "content,code",
    [
        (b"not-json", "R_SCHEMA_INVALID"),
        (b" " * 131073, "R_RESPONSE_TOO_LARGE"),
        (b'{"results":[{"title":"missing url"}]}', "R_SCHEMA_INVALID"),
        (b'{"results":[],"usage":{"credits":-1}}', "R_SCHEMA_INVALID"),
    ],
    ids=["invalid-json", "oversized", "missing-field", "invalid-credits"],
)
def test_provider_response_faults_fail_explicitly(content: bytes, code: str) -> None:
    with TavilyResearchClient(
        config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content)
        ),
    ) as client:
        report = client.search_docs(
            DocumentationQuery(package="python", topic="transactions"), allow_live=True
        )
    assert report.status == "FAILED"
    assert report.error_class == code
    assert report.sources == report.artifacts == ()
    assert report.usage_credits is None


def test_timeout_spends_attempt_without_retry_or_secret_leak() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout(KEY, request=request)

    with TavilyResearchClient(
        config(), transport=httpx.MockTransport(respond)
    ) as client:
        report = client.search_docs(
            DocumentationQuery(package="python", topic="transactions"), allow_live=True
        )
    assert report.error_class == "R_TIMEOUT"
    assert report.searches_remaining == len(calls) == 1
    assert KEY not in report.model_dump_json()


def test_concurrent_callers_cannot_overspend_client_budget() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://docs.python.org/3/library/sqlite3.html",
                        "title": "SQLite",
                        "content": "Transactions",
                    }
                ]
            },
        )

    with TavilyResearchClient(
        config(), transport=httpx.MockTransport(respond)
    ) as client:

        def search(_: int) -> str:
            return client.search_docs(
                DocumentationQuery(package="python", topic="transactions"),
                allow_live=True,
            ).status

        with ThreadPoolExecutor(max_workers=3) as pool:
            statuses = list(pool.map(search, range(3)))
    assert statuses.count("SUCCESS") == 2
    assert statuses.count("NOT_RUN") == 1
    assert len(calls) == 2


def test_closed_client_never_starts_work() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("closed client made a request")

    client = TavilyResearchClient(config(), transport=httpx.MockTransport(forbidden))
    client.close()
    report = client.search_docs(
        DocumentationQuery(package="python", topic="transactions"), allow_live=True
    )
    assert report.policy_decision == "DENY_CLOSED"
    assert report.searches_remaining == 2


def test_model_construct_cannot_bypass_query_schema() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("unvalidated query reached provider")

    query = DocumentationQuery.model_construct(
        package="not-registered", topic="transactions"
    )
    with TavilyResearchClient(
        config(), transport=httpx.MockTransport(forbidden)
    ) as client:
        report = client.search_docs(query, allow_live=True)
    assert report.policy_decision == "DENY_QUERY"
    assert report.searches_remaining == 2


def test_cli_uses_fixed_query_and_defaults_to_offline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import check_tavily

    monkeypatch.setattr("sys.argv", ["check_tavily"])
    monkeypatch.setenv("ENABLE_TAVILY", "true")
    monkeypatch.setenv("TAVILY_API_KEY", KEY)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("default CLI constructed an HTTP client")

    monkeypatch.setattr("packages.providers.tavily.httpx.Client", forbidden)
    assert check_tavily.main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["policy_decision"] == "DENY_LIVE_NOT_AUTHORIZED"
    assert KEY not in json.dumps(output)


def test_duplicate_sources_and_long_snippets_record_omissions() -> None:
    source = {
        "url": "https://docs.python.org/3/library/sqlite3.html",
        "title": "SQLite",
        "content": "x" * 1100,
    }
    with TavilyResearchClient(
        config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": [source, source]})
        ),
    ) as client:
        report = client.search_docs(
            DocumentationQuery(package="python", topic="transactions"), allow_live=True
        )
    assert len(report.sources) == report.omitted_sources == 1
    assert report.sources[0].truncated
    assert len(report.sources[0].snippet) == 1000
    assert report.usage_credits is None


def test_provider_cannot_expand_result_limit() -> None:
    source = {
        "url": "https://docs.python.org/3/library/sqlite3.html",
        "title": "SQLite",
        "content": "Transactions",
    }
    with TavilyResearchClient(
        config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": [source, source]})
        ),
    ) as client:
        report = client.search_docs(
            DocumentationQuery(package="python", topic="transactions", max_results=1),
            allow_live=True,
        )
    assert report.error_class == "R_SCHEMA_INVALID"
    assert not report.sources
