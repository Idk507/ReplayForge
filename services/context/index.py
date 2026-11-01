"""Deterministic, bounded repository index over an already verified snapshot.

No model, network, filesystem or code execution. Everything derives from the
snapshot's tree identities and the bytes it already holds, so each entry keeps
repository-commit, path, blob and content-hash provenance. Ranking looks only at
paths, symbol names and import names, never comments, docstrings or string
literals, so repository text cannot steer which files are shown to a model.
"""

import ast
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.repository.snapshot import Snapshot, SnapshotError

Role = Literal["SOURCE", "TEST", "DEPENDENCY", "DOC", "CONFIG", "OTHER"]
MAX_FILES = 512
MAX_SYMBOLS = 64
MAX_READ = 65536
_FRAMEWORKS = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "pytest": "pytest",
    "sqlalchemy": "sqlalchemy",
    "pydantic": "pydantic",
    "requests": "requests",
    "httpx": "httpx",
}
_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".md": "markdown",
    ".toml": "toml",
    ".txt": "text",
}
_DEPENDENCY_FILES = frozenset(
    {"pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile.lock"}
)
_TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = frozenset(
    "the and for with when from that this was were are not but can has have into "
    "after before then than error exception failed failure issue bug problem "
    "customer user users request response should does did doing".split()
)


class Symbol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(max_length=128)
    kind: Literal["function", "class", "method"]
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class IndexedFile(BaseModel):
    """One snapshot file; bytes are never copied into the index."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str = Field(max_length=240)
    blob_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    language: str
    role: Role
    parse_status: Literal["PARSED", "INVALID_SYNTAX", "NOT_PYTHON", "NOT_READ"]
    symbols: tuple[Symbol, ...] = Field(default=(), max_length=MAX_SYMBOLS)
    imports: tuple[str, ...] = Field(default=(), max_length=64)
    entry_point: bool = False


class RepositoryIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    evidence_origin: Literal["NOT_RECORDED", "TEST_TRANSPORT", "REMOTE_SERVICE"]
    files: tuple[IndexedFile, ...] = Field(max_length=MAX_FILES)
    languages: dict[str, int]
    frameworks: tuple[str, ...]
    test_map: dict[str, tuple[str, ...]]
    omissions: tuple[str, ...]
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    trust: Literal["UNTRUSTED_REPOSITORY_DATA"] = "UNTRUSTED_REPOSITORY_DATA"
    can_authorize_execution: Literal[False] = False


class RankedPath(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    score: int
    reasons: tuple[str, ...]


def _role(path: str) -> Role:
    name = path.rsplit("/", 1)[-1]
    parts = path.lower().split("/")
    if name in _DEPENDENCY_FILES:
        return "DEPENDENCY"
    if path.endswith(".py") and (
        any(p in {"tests", "test"} for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
    ):
        return "TEST"
    if path.endswith(".md"):
        return "DOC"
    if path.endswith((".toml", ".ini", ".cfg", ".yml", ".yaml", ".json")):
        return "CONFIG"
    if path.endswith(".py"):
        return "SOURCE"
    return "OTHER"


def _symbols_and_imports(
    tree: ast.AST,
) -> tuple[tuple[Symbol, ...], tuple[str, ...], bool]:
    symbols: list[Symbol] = []
    imports: set[str] = set()
    entry = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(
                Symbol(
                    name=node.name[:128],
                    kind="class",
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                Symbol(
                    name=node.name[:128],
                    kind="function",
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module)
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                entry = True
    symbols.sort(key=lambda s: (s.line, s.name))
    return tuple(symbols[:MAX_SYMBOLS]), tuple(sorted(imports))[:64], entry


def _module_paths(path: str) -> set[str]:
    """Dotted module names that could import this file (src/ layouts included)."""
    if not path.endswith(".py"):
        return set()
    stem = path[:-3]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    parts = stem.split("/")
    names = set()
    for start in range(len(parts)):
        names.add(".".join(parts[start:]))
    return names


def build_index(snapshot: Snapshot) -> RepositoryIndex:
    """Index every identity in the snapshot, reading only bytes it already holds."""
    identities = sorted(snapshot.files, key=lambda f: f.path)
    omissions: list[str] = []
    if len(identities) > MAX_FILES:
        omissions.append("FILE_LIMIT")
        identities = identities[:MAX_FILES]
    files: list[IndexedFile] = []
    frameworks: set[str] = set()
    for identity in identities:
        role = _role(identity.path)
        language = _LANGUAGES.get(
            "." + identity.path.rsplit(".", 1)[-1] if "." in identity.path else "",
            "unknown",
        )
        status: Literal["PARSED", "INVALID_SYNTAX", "NOT_PYTHON", "NOT_READ"] = (
            "NOT_PYTHON"
        )
        symbols: tuple[Symbol, ...] = ()
        imports: tuple[str, ...] = ()
        entry = False
        raw: bytes | None
        try:
            raw = snapshot.read_file(identity.path, MAX_READ)
        except SnapshotError:
            raw = None  # tree identity only: not selected, or over the read cap
        if raw is not None:
            if hashlib.sha256(raw).hexdigest() != identity.sha256:
                raise ValueError("SOURCE_INTEGRITY")
            if role == "DEPENDENCY":
                text = raw.decode("utf-8", errors="ignore").lower()
                frameworks.update(
                    label
                    for key, label in _FRAMEWORKS.items()
                    if re.search(rf"(?<![a-z0-9_-]){key}(?![a-z0-9_-])", text)
                )
            if identity.path.endswith(".py"):
                try:
                    tree = ast.parse(raw.decode("utf-8"))
                    symbols, imports, entry = _symbols_and_imports(tree)
                    status = "PARSED"
                except (SyntaxError, ValueError, RecursionError, UnicodeDecodeError):
                    status = "INVALID_SYNTAX"
        elif identity.path.endswith(".py"):
            status = "NOT_READ"
        if status == "PARSED" and any(
            label in imports or any(i.startswith(label + ".") for i in imports)
            for label in _FRAMEWORKS.values()
        ):
            frameworks.update(
                label
                for label in _FRAMEWORKS.values()
                if label in imports or any(i.startswith(label + ".") for i in imports)
            )
        if (
            identity.path.rsplit("/", 1)[-1] in {"main.py", "app.py", "__main__.py"}
            and role == "SOURCE"
        ):
            entry = True
        files.append(
            IndexedFile(
                path=identity.path,
                blob_id=identity.blob_id,
                sha256=identity.sha256,
                size_bytes=identity.size_bytes,
                language=language,
                role=role,
                parse_status=status,
                symbols=symbols,
                imports=imports,
                entry_point=entry,
            )
        )
    modules: dict[str, str] = {}
    for item in files:
        if item.role == "SOURCE":
            for name in _module_paths(item.path):
                modules.setdefault(name, item.path)
    test_map: dict[str, tuple[str, ...]] = {}
    for item in files:
        if item.role != "TEST":
            continue
        linked: set[str] = set()
        for imp in item.imports:
            candidate = imp if imp in modules else imp.rsplit(".", 1)[0]
            if candidate in modules:
                linked.add(modules[candidate])
        # A test named test_<stem>.py also covers <stem>.py without importing it.
        stem = item.path.rsplit("/", 1)[-1][len("test_") :]
        linked.update(
            f.path
            for f in files
            if f.role == "SOURCE" and f.path.rsplit("/", 1)[-1] == stem
        )
        if linked:
            test_map[item.path] = tuple(sorted(linked))
    languages: dict[str, int] = {}
    for item in files:
        languages[item.language] = languages.get(item.language, 0) + 1
    body = {
        "source_commit": snapshot.source_commit,
        "files": [f.model_dump() for f in files],
        "frameworks": sorted(frameworks),
        "test_map": test_map,
        "omissions": omissions,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RepositoryIndex(
        source_commit=snapshot.source_commit,
        evidence_origin=snapshot.evidence_origin,
        files=tuple(files),
        languages=dict(sorted(languages.items())),
        frameworks=tuple(sorted(frameworks)),
        test_map=test_map,
        omissions=tuple(omissions),
        digest=digest,
    )


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text[:4096]):
        for part in re.split(r"_|(?<=[a-z])(?=[A-Z])", word):
            low = part.lower()
            if _TOKEN.fullmatch(low) and low not in _STOP:
                out.add(low)
                if low.endswith("s") and len(low) > 4:
                    out.add(low[:-1])
    return out


def rank_paths(
    index: RepositoryIndex, incident: str, *, limit: int = 8
) -> tuple[RankedPath, ...]:
    """Score by overlap with path, symbol and import names; ties break by path."""
    if not 1 <= limit <= 16:
        raise ValueError("RANK_LIMIT")
    wanted = _tokens(incident)
    explicit = {m for m in re.findall(r"[A-Za-z0-9_./-]+\.py", incident[:4096])}
    scores: dict[str, tuple[int, list[str]]] = {}
    for item in index.files:
        if item.role not in {"SOURCE", "TEST"}:
            continue
        points = 0
        reasons: list[str] = []
        if item.path in explicit:
            points += 100
            reasons.append("INCIDENT_PATH")
        path_hits = wanted & _tokens(item.path.replace("/", "_").replace(".", "_"))
        if path_hits:
            points += 5 * len(path_hits)
            reasons.append("PATH_TERM")
        symbol_hits = wanted & set().union(
            *(_tokens(s.name) for s in item.symbols), set()
        )
        if symbol_hits:
            points += 8 * len(symbol_hits)
            reasons.append("SYMBOL_TERM")
        import_hits = wanted & set().union(*(_tokens(i) for i in item.imports), set())
        if import_hits:
            points += 2 * len(import_hits)
            reasons.append("IMPORT_TERM")
        if item.role == "TEST":
            points -= 1  # evidence for behavior, but rank the code under test first
        if points > 0:
            scores[item.path] = (points, reasons)
    # Sources exercised by a matching test inherit a smaller boost.
    for test, sources in index.test_map.items():
        if test in scores:
            for source in sources:
                points, reasons = scores.get(source, (0, []))
                scores[source] = (points + 3, [*reasons, "TESTED_BY_MATCH"])
    ranked = sorted(
        (
            RankedPath(path=p, score=s, reasons=tuple(dict.fromkeys(r)))
            for p, (s, r) in scores.items()
        ),
        key=lambda r: (-r.score, r.path),
    )
    return tuple(ranked[:limit])


def search_symbols(index: RepositoryIndex, term: str) -> tuple[tuple[str, Symbol], ...]:
    """Case-insensitive literal substring over indexed symbol names (no regex)."""
    needle = term.strip().lower()
    if not 2 <= len(needle) <= 64:
        raise ValueError("SEARCH_TERM")
    return tuple(
        (f.path, s) for f in index.files for s in f.symbols if needle in s.name.lower()
    )[:50]
