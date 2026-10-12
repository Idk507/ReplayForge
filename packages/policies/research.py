"""Bounded public documentation queries and source sanitization policy."""

import html
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from packages.contracts.research import DocumentationQuery

PACKAGE_DOMAINS: dict[str, tuple[str, ...]] = {
    "python": ("docs.python.org",),
    "pytest": ("docs.pytest.org",),
    "fastapi": ("fastapi.tiangolo.com",),
    "pandas": ("pandas.pydata.org",),
    "sqlalchemy": ("docs.sqlalchemy.org",),
    "postgresql": ("www.postgresql.org",),
}
TOPICS: dict[str, str] = {
    "transactions": "transaction control",
    "idempotency": "idempotency guarantees",
    "concurrency": "concurrency synchronization",
    "migration": "migration guide",
    "testing": "testing reference",
}
SENSITIVE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9_.~+/-]+=*|"
    r"(?:api[_-]?key|token|password|secret)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]+|"
    r"(?:ghp_|github_pat_|tvly-)[A-Za-z0-9_-]+|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


def compile_query(request: DocumentationQuery) -> tuple[str, tuple[str, ...]]:
    """Construct public text from a closed schema; never accept incident text."""
    version = f" {request.version}" if request.version is not None else ""
    query = f"{request.package}{version} {TOPICS[request.topic]} official documentation"
    return query, PACKAGE_DOMAINS[request.package]


def allowed_source_url(url: str, domains: tuple[str, ...], credential: str) -> bool:
    """Only exact public documentation hosts; never fetch a returned URL."""
    if (
        len(url) > 2048
        or not url.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in url)
    ):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in domains
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and "\\" not in url
            and credential not in unquote(url)
            and SENSITIVE.search(unquote(url)) is None
        )
    except ValueError:
        return False


def sanitize_text(text: str, credential: str, limit: int) -> tuple[str, bool]:
    """Remove known credential/common sensitive patterns, markup and controls.

    This is conservative public-source sanitation, not a general-purpose DLP
    guarantee. Source text remains untrusted even after redaction.
    """
    text = html.unescape(text)
    text = re.sub(r"<[^>]*>", "", text)
    text = "".join(
        c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C")
    )
    # Normalize first: tags/control characters must not reconstruct a key
    # after redaction has already run.
    text = text.replace(credential, "[REDACTED]")
    text = SENSITIVE.sub("[REDACTED]", text)
    return text[:limit].strip(), len(text) > limit
