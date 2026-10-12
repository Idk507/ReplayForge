"""Optional Tavily research adapter; no incident text or privileged tools.

Uses the documented REST search API. Explicit live admission is separate from
feature enablement. Two attempted searches per client, no automatic retries.
"""

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal, TypedDict
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from packages.contracts.research import (
    DocumentationQuery,
    ResearchReport,
    ResearchSource,
)
from packages.policies.research import allowed_source_url, compile_query, sanitize_text

MAX_RESPONSE_BYTES = 131072


class ObservationContext(TypedDict):
    """Typed correlation fields shared by all report outcomes."""

    observed_at: datetime
    trace_id: UUID


class TavilyConfigError(Exception):
    """Public safe configuration code, with no credential values."""


class ResearchError(Exception):
    """Internal stable error code; raw HTTP content never crosses this boundary."""


class TavilyConfig(BaseModel):
    """Environment-only API key; the destination is fixed in application code."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    enabled: bool
    api_key: SecretStr | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TavilyConfig":
        """Disabled by default. Validate header safety before enabling calls."""
        values = os.environ if env is None else env
        enabled = values.get("ENABLE_TAVILY", "false")
        key = values.get("TAVILY_API_KEY", "")
        if enabled not in ("true", "false"):
            raise TavilyConfigError("R_CONFIG_INVALID")
        if enabled == "true" and not key:
            raise TavilyConfigError("R_CONFIG_MISSING_KEY")
        if key and not re.fullmatch(r"[\x21-\x7e]{1,4096}", key):
            raise TavilyConfigError("R_CONFIG_INVALID")
        return cls(enabled=enabled == "true", api_key=SecretStr(key) if key else None)


class ProviderSource(BaseModel):
    """Raw provider subset, private and bounded before sanitization."""

    model_config = ConfigDict(hide_input_in_errors=True)
    url: str = Field(max_length=4096, repr=False)
    title: str = Field(max_length=4096, repr=False)
    content: str = Field(max_length=16000, repr=False)


class ProviderUsage(BaseModel):
    """Missing credits remain unknown; never infer dollar cost."""

    credits: int = Field(strict=True, ge=0)


class ProviderResponse(BaseModel):
    """Do not retain provider answer, echoed query or optional raw content."""

    model_config = ConfigDict(hide_input_in_errors=True)
    results: list[ProviderSource] = Field(max_length=20)
    usage: ProviderUsage | None = None


class TavilyResearchClient:
    """Serialized, bounded optional search client with explicit lifecycle.

    The per-client budget is not a substitute for durable per-run authorization
    and spend accounting, which must be added by the orchestrator broker.
    """

    def __init__(
        self, config: TavilyConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._config = TavilyConfig.from_env(
            {
                "ENABLE_TAVILY": "true" if config.enabled else "false",
                "TAVILY_API_KEY": config.api_key.get_secret_value()
                if config.api_key
                else "",
            }
        )
        self._transport = transport
        self._remaining = 2
        self._lock = threading.Lock()
        self._http: httpx.Client | None = None
        self._closed = False

    def __enter__(self) -> "TavilyResearchClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Wait for admitted work then close resources; never allow reuse."""
        with self._lock:
            self._closed = True
            if self._http is not None:
                self._http.close()

    def _post(self, payload: dict[str, object]) -> ProviderResponse:
        """Fixed HTTPS endpoint, identity encoding, size/progress/time limits."""
        key = self._config.api_key
        if key is None:
            raise ResearchError("R_CONFIG_MISSING_KEY")
        if self._http is None:
            self._http = httpx.Client(
                base_url="https://api.tavily.com/",
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "Accept-Encoding": "identity",
                },
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(10, connect=5),
                transport=self._transport,
            )
        deadline = time.monotonic() + 15
        try:
            with self._http.stream("POST", "search", json=payload) as response:
                status = response.status_code
                if status in (401, 403):
                    raise ResearchError("R_AUTH")
                if status == 429:
                    raise ResearchError("R_RATE_LIMIT")
                if status in (432, 433):
                    raise ResearchError("R_USAGE_LIMIT")
                if 300 <= status < 400:
                    raise ResearchError("R_REDIRECT_DENIED")
                if status != 200:
                    raise ResearchError("R_UNAVAILABLE")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ResearchError("R_SCHEMA_INVALID")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise ResearchError("R_TIMEOUT")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ResearchError("R_RESPONSE_TOO_LARGE")
                    body.extend(chunk)
                return ProviderResponse.model_validate_json(body)
        except httpx.TimeoutException:
            raise ResearchError("R_TIMEOUT") from None
        except httpx.HTTPError:
            raise ResearchError("R_UNAVAILABLE") from None
        except ValidationError:
            raise ResearchError("R_SCHEMA_INVALID") from None

    def search_docs(
        self, request: DocumentationQuery, *, allow_live: bool = False
    ) -> ResearchReport:
        """Search only compiled public queries; returned strings stay advisory."""
        with self._lock:
            return self._search(request, allow_live=allow_live)

    def _search(
        self, request: DocumentationQuery, *, allow_live: bool
    ) -> ResearchReport:
        """All admission and request accounting happen under the client lock."""
        common: ObservationContext = {
            "observed_at": datetime.now(UTC),
            "trace_id": uuid4(),
        }
        if self._closed:
            return ResearchReport(
                **common,
                status="NOT_RUN",
                evidence_origin="NOT_RUN",
                policy_decision="DENY_CLOSED",
                searches_remaining=self._remaining,
            )
        if not self._config.enabled or not allow_live:
            return ResearchReport(
                **common,
                status="NOT_RUN",
                evidence_origin="NOT_RUN",
                policy_decision="DENY_DISABLED"
                if not self._config.enabled
                else "DENY_LIVE_NOT_AUTHORIZED",
                searches_remaining=self._remaining,
            )
        if self._remaining == 0:
            return ResearchReport(
                **common,
                status="NOT_RUN",
                evidence_origin="NOT_RUN",
                policy_decision="DENY_BUDGET",
                error_class="B_RESEARCH_BUDGET_EXHAUSTED",
                searches_remaining=0,
            )
        try:
            request = DocumentationQuery.model_validate(request.model_dump())
        except ValidationError:
            return ResearchReport(
                **common,
                status="FAILED",
                evidence_origin="NOT_RUN",
                policy_decision="DENY_QUERY",
                error_class="R_QUERY_INVALID",
                searches_remaining=self._remaining,
            )
        query, domains = compile_query(request)
        self._remaining -= 1
        started = time.monotonic()
        origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"] = (
            "TEST_TRANSPORT" if self._transport is not None else "REMOTE_SERVICE"
        )
        try:
            response = self._post(
                {
                    "query": query,
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": request.max_results,
                    "include_domains": list(domains),
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                    "auto_parameters": False,
                    "include_usage": True,
                }
            )
            if len(response.results) > request.max_results:
                raise ResearchError("R_SCHEMA_INVALID")
        except ResearchError as error:
            return ResearchReport(
                **common,
                status="FAILED",
                evidence_origin=origin,
                policy_decision="ALLOW_REGISTERED_DOC_SEARCH",
                error_class=str(error),
                searches_remaining=self._remaining,
                latency_ms=round((time.monotonic() - started) * 1000, 3),
            )
        sources = []
        seen: set[str] = set()
        key = self._config.api_key.get_secret_value() if self._config.api_key else ""
        retrieved_at = datetime.now(UTC)
        for item in response.results:
            if not allowed_source_url(item.url, domains, key) or item.url in seen:
                continue
            title, title_truncated = sanitize_text(item.title, key, 240)
            snippet, snippet_truncated = sanitize_text(item.content, key, 1000)
            if not title or not snippet:
                continue
            seen.add(item.url)
            canonical = json.dumps(
                {"url": item.url, "title": title, "snippet": snippet},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            sources.append(
                ResearchSource(
                    url=item.url,
                    title=title,
                    snippet=snippet,
                    retrieved_at=retrieved_at,
                    content_sha256=hashlib.sha256(canonical).hexdigest(),
                    truncated=title_truncated or snippet_truncated,
                )
            )
        return ResearchReport(
            **common,
            status="SUCCESS" if sources else "INCONCLUSIVE",
            evidence_origin=origin,
            policy_decision="ALLOW_REGISTERED_DOC_SEARCH",
            error_class=None if sources else "R_NO_ALLOWED_SOURCES",
            sources=tuple(sources),
            artifacts=tuple(f"sha256:{source.content_sha256}" for source in sources),
            omitted_sources=len(response.results) - len(sources),
            searches_remaining=self._remaining,
            usage_credits=response.usage.credits if response.usage else None,
            latency_ms=round((time.monotonic() - started) * 1000, 3),
        )
