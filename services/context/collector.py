"""Bounded context selection, secret redaction and non-executing AST inspection."""

import ast
import hashlib
import json
import re
import unicodedata

from pydantic import SecretStr

from packages.contracts.context import (
    DEPENDENCY_PATHS,
    ContextBundle,
    ContextFile,
    DependencyFile,
    dependency_digest,
)
from services.context.index import build_index
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
    dependencies = []
    for identity in sorted(snapshot.files, key=lambda f: f.path):
        if identity.path not in DEPENDENCY_PATHS:
            continue
        try:
            raw = snapshot.read_file(identity.path, 65536)
        except SnapshotError:
            raise ContextError("PATH_DENIED") from None
        if (
            len(raw) != identity.size_bytes
            or hashlib.sha256(raw).hexdigest() != identity.sha256
        ):
            raise ContextError("SOURCE_INTEGRITY")
        dependencies.append(DependencyFile(path=identity.path, sha256=identity.sha256))
    dependency_files = tuple(dependencies)
    bundle = ContextBundle(
        source_commit=snapshot.source_commit,
        commit_identity=snapshot.commit_identity,
        evidence_origin=snapshot.evidence_origin,
        sanitized_errors=clean_errors[:1024],
        dependency_files=dependency_files,
        deps_hash=dependency_digest(dependency_files),
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
        # Reserve room for the repository map below the 4096-byte bundle cap.
        if len(candidate.model_dump_json().encode()) > 3300:
            omissions.append("CONTEXT_BUDGET")
        else:
            bundle = candidate
    try:
        index = build_index(snapshot)
    except ValueError:
        raise ContextError("SOURCE_INTEGRITY") from None
    included = {item.path for item in bundle.files}
    entries: list[str] = []
    for indexed in index.files:
        if indexed.role in {"SOURCE", "TEST"} and indexed.path not in included:
            names = ",".join(sym.name[:24] for sym in indexed.symbols[:6])
            entries.append(f"{indexed.role} {indexed.path}: {names}"[:120])
    for test, sources in index.test_map.items():
        entries.append(f"TESTS {test} -> {','.join(sources)}"[:120])
    base = {
        **bundle.model_dump(),
        "index_digest": index.digest,
        "frameworks": index.frameworks[:8],
    }
    mapped: list[str] = []
    for entry in entries:
        cleaned = redact(entry)
        if len(mapped) == 16:
            omissions.append("MAP_LIMIT")
            break
        if not re.fullmatch(r"[ -~]+", cleaned):
            omissions.append("MAP_ENTRY_SKIPPED")
            continue
        trial = ContextBundle.model_validate(
            {
                **base,
                "omissions": tuple(omissions),
                "repository_map": (*mapped, cleaned),
            }
        )
        # Triage embeds the bundle as an escaped JSON string inside a 4096-byte
        # context, so bound the escaped size, not just the raw bundle.
        escaped = json.dumps(
            {"context_0": trial.model_dump_json()},
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(trial.model_dump_json().encode()) > 4096 or len(escaped.encode()) > 3900:
            omissions.append("MAP_BUDGET")
            break
        mapped.append(cleaned)
    result = ContextBundle.model_validate(
        {
            **base,
            "omissions": tuple(dict.fromkeys(omissions)),
            "repository_map": tuple(mapped),
        }
    )
    if len(result.model_dump_json().encode()) > 4096:
        raise ContextError("CONTEXT_BUDGET")
    if any(secret in result.model_dump_json() for secret in secrets):
        raise ContextError("SECRET_METADATA")
    return result
