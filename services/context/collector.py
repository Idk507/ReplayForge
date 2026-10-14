"""Bounded context selection, secret redaction and non-executing AST inspection."""

import ast
import hashlib
import re
import unicodedata

from pydantic import SecretStr

from packages.contracts.context import ContextBundle, ContextFile
from services.repository.snapshot import Snapshot, SnapshotError

PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
    r"(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
CREDENTIAL = re.compile(
    r"(?i)(?:\bbearer\s+[^\s\"']+|"
    r"\b(?:api[_-]?key|password|secret|token)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]+[\"']?|"
    r"(?:ghp_|github_pat_|tvly-)[A-Za-z0-9_-]+)"
)


class ContextError(Exception):
    """Safe stable failure without source content or secret values."""


def _normalize(text: str) -> str:
    return "".join(
        c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C")
    )


def collect(
    snapshot: Snapshot,
    paths: tuple[str, ...],
    errors: str,
    *,
    known_secrets: tuple[SecretStr, ...] = (),
) -> ContextBundle:
    """Build an at-most-4096-byte serialized payload without reading host files.

    Byte cap is conservative model-independent context admission, not a measured
    tokenizer count. Redaction is bounded pattern matching, not complete DLP.
    """
    if not 1 <= len(paths) <= 16 or len(paths) != len(set(paths)):
        raise ContextError("PATH_LIMIT")
    try:
        if len(errors.encode("utf-8")) > 65536:
            raise ContextError("INPUT_LIMIT")
    except UnicodeEncodeError:
        raise ContextError("INPUT_ENCODING") from None
    if len(known_secrets) > 32:
        raise ContextError("SECRET_CONFIG")
    secrets = tuple(_normalize(s.get_secret_value()) for s in known_secrets)
    if any(not 1 <= len(s) <= 4096 for s in secrets):
        raise ContextError("SECRET_CONFIG")

    def redact(text: str) -> str:
        text = _normalize(text)
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return CREDENTIAL.sub("[REDACTED]", PRIVATE_KEY.sub("[REDACTED]", text))

    clean_errors = redact(errors)
    omissions = ["ERRORS_TRUNCATED"] if len(clean_errors) > 1024 else []
    bundle = ContextBundle(
        source_commit=snapshot.source_commit,
        commit_identity=snapshot.commit_identity,
        evidence_origin=snapshot.evidence_origin,
        sanitized_errors=clean_errors[:1024],
        omissions=tuple(omissions),
    )
    identities = {item.path: item for item in snapshot.files}
    for path in paths:
        if path not in identities:
            raise ContextError("PATH_DENIED")
        try:
            raw = snapshot.read_file(path, 65536)
        except SnapshotError:
            raise ContextError("PATH_DENIED") from None
        if hashlib.sha256(raw).hexdigest() != identities[path].sha256:
            raise ContextError("SOURCE_INTEGRITY")
        try:
            text = redact(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise ContextError("INPUT_ENCODING") from None
        if len(text) > 4096:
            omissions.append("CONTEXT_BUDGET")
            continue
        symbols: tuple[str, ...] = ()
        status = "NOT_PYTHON"
        if path.endswith(".py"):
            try:
                tree = ast.parse(text)
                symbols = tuple(
                    node.name[:128]
                    for node in ast.walk(tree)
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                )[:32]
                status = "PARSED"
            except (SyntaxError, ValueError, RecursionError):
                status = "INVALID_SYNTAX"
        item = ContextFile.model_validate(
            dict(
                path=path,
                source_sha256=identities[path].sha256,
                sanitized_sha256=hashlib.sha256(text.encode()).hexdigest(),
                content=text,
                symbols=symbols,
                parse_status=status,
            )
        )
        candidate = bundle.model_copy(
            update={"files": bundle.files + (item,), "omissions": tuple(omissions)}
        )
        if len(candidate.model_dump_json().encode()) > 3800:
            omissions.append("CONTEXT_BUDGET")
        else:
            bundle = candidate
    result = ContextBundle.model_validate(
        {**bundle.model_dump(), "omissions": tuple(omissions)}
    )
    if len(result.model_dump_json().encode()) > 4096:
        raise ContextError("CONTEXT_BUDGET")
    if any(secret in result.model_dump_json() for secret in secrets):
        raise ContextError("SECRET_METADATA")
    return result
