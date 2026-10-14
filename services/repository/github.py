"""Bounded read-only GitHub commit metadata; not a raw Git object transport."""

import base64
import binascii
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import CommitSHA
from packages.policies.repository import admit
from services.repository.snapshot import Snapshot, load_attested_tree


class RepositoryReadError(Exception):
    """Stable safe error; no token, response body or request object."""


class TreeIdentity(BaseModel):
    """Provider fields outside this projection are discarded."""

    sha: CommitSHA


class CommitResponse(BaseModel):
    """Only the pinned identity and root tree enter trusted metadata."""

    sha: CommitSHA
    tree: TreeIdentity


class CommitResolution(BaseModel):
    """HTTPS provider attestation, not locally verified raw commit bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    commit_sha: CommitSHA
    tree_sha: CommitSHA
    repository_id: UUID
    observed_at: datetime
    trace_id: UUID
    status: Literal["PASSED"] = "PASSED"
    evidence_origin: Literal["REMOTE_SERVICE", "TEST_TRANSPORT"]
    policy_decision: Literal["ALLOW_READ_ONLY_PINNED"] = "ALLOW_READ_ONLY_PINNED"
    artifacts: tuple[()] = ()
    retryable: Literal[False] = False
    raw_commit_verified: Literal[False] = False


class GitHubReader:
    """Operator-injected read credential; only fixed api.github.com GETs.

    Token scope must be provisioned separately as Contents:read on the exact
    repository. This client cannot introspect or reduce a token's permissions.
    No environment credentials/proxies, redirects, retries or write methods.
    """

    def __init__(
        self, token: SecretStr, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        raw = token.get_secret_value()
        if not re.fullmatch(r"[\x21-\x7e]{1,4096}", raw):
            raise RepositoryReadError("TOKEN_INVALID")
        self._origin: Literal["REMOTE_SERVICE", "TEST_TRANSPORT"] = (
            "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
        )
        self._client = httpx.Client(
            base_url="https://api.github.com/",
            headers={
                "Authorization": "Bearer " + raw,
                "Accept": "application/vnd.github+json",
                "Accept-Encoding": "identity",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            timeout=httpx.Timeout(5, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> "GitHubReader":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Release local sockets; no remote mutation is performed."""
        self._client.close()

    def resolve(
        self, grant: RepositoryGrant, caller: UUID, commit: str
    ) -> CommitResolution:
        """Admit exact consent before one bounded, correlated HTTP read."""
        grant = RepositoryGrant.model_validate(grant.model_dump())
        if not admit(grant, caller, grant.repository_id, commit).allowed:
            raise RepositoryReadError("SCOPE_DENIED")
        repository = grant.remote_url.removeprefix("https://github.com/")
        path = f"repos/{repository}/git/commits/{commit}"
        try:
            result = CommitResponse.model_validate_json(self._get(path))
        except ValidationError:
            raise RepositoryReadError("SCHEMA_INVALID") from None
        if result.sha != commit:
            raise RepositoryReadError("COMMIT_MISMATCH")
        return CommitResolution(
            commit_sha=result.sha,
            tree_sha=result.tree.sha,
            repository_id=grant.repository_id,
            observed_at=datetime.now(UTC),
            trace_id=uuid4(),
            evidence_origin=self._origin,
        )

    def acquire(
        self, grant: RepositoryGrant, caller: UUID, commit: str
    ) -> "GitHubSnapshot":
        """Resolve consented commit then verify only permitted reachable objects."""
        deadline = time.monotonic() + 20
        resolution = self.resolve(grant, caller, commit)
        objects = _GitHubObjects(self, grant, resolution.tree_sha, deadline)
        snapshot = load_attested_tree(
            objects, grant, caller, commit, resolution.tree_sha
        )
        return GitHubSnapshot(
            replace(snapshot, evidence_origin=resolution.evidence_origin), resolution
        )

    def _get(
        self, path: str, *, limit: int = 65536, until: float | None = None
    ) -> bytes:
        """Internal fixed path callers only; enforce response and progress limits."""
        deadline = time.monotonic() + 10
        if until is not None:
            deadline = min(deadline, until)
            if time.monotonic() >= deadline:
                raise RepositoryReadError("TIMEOUT")
        try:
            with self._client.stream("GET", path) as response:
                status = response.status_code
                if 300 <= status < 400:
                    raise RepositoryReadError("REDIRECT_DENIED")
                if status in (401, 403):
                    raise RepositoryReadError("AUTH_DENIED")
                if status == 429:
                    raise RepositoryReadError("RATE_LIMITED")
                if status != 200:
                    raise RepositoryReadError("UNAVAILABLE")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise RepositoryReadError("RESPONSE_ENCODING")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    if time.monotonic() >= deadline:
                        raise RepositoryReadError("TIMEOUT")
                    if len(body) + len(chunk) > limit:
                        raise RepositoryReadError("RESPONSE_LIMIT")
                    body.extend(chunk)
                if time.monotonic() >= deadline:
                    raise RepositoryReadError("TIMEOUT")
            return bytes(body)
        except httpx.TimeoutException:
            raise RepositoryReadError("TIMEOUT") from None
        except httpx.HTTPError:
            raise RepositoryReadError("UNAVAILABLE") from None


@dataclass(frozen=True)
class GitHubSnapshot:
    """Source bytes stay private; safe provider provenance is kept separately."""

    snapshot: Snapshot = field(repr=False)
    resolution: CommitResolution


class TreeEntry(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    mode: Literal["040000", "100644", "100755", "120000", "160000"]
    type: Literal["tree", "blob", "commit"]
    sha: CommitSHA


class TreeResponse(BaseModel):
    sha: CommitSHA
    tree: tuple[TreeEntry, ...] = Field(max_length=512)
    truncated: Literal[False]


class BlobResponse(BaseModel):
    sha: CommitSHA
    encoding: Literal["base64"]
    size: int = Field(strict=True, ge=0, le=65536)
    content: str = Field(max_length=100000, repr=False)


class _GitHubObjects:
    """Traversal-only adapter; raw commit reconstruction is deliberately absent."""

    def __init__(
        self, reader: GitHubReader, grant: RepositoryGrant, root: str, deadline: float
    ) -> None:
        self.reader = reader
        self.prefix = "repos/" + grant.remote_url.removeprefix("https://github.com/")
        self.kinds = {root: "tree"}
        self.deadline = deadline

    def read_object(self, oid: str, max_bytes: int) -> tuple[str, bytes]:
        """Read only discovered object IDs; reconstruct trees for raw hash checking."""
        kind = self.kinds.get(oid)
        if kind not in {"tree", "blob"}:
            raise RepositoryReadError("OBJECT_DENIED")
        raw = self.reader._get(
            f"{self.prefix}/git/{kind}s/{oid}",
            limit=131072,
            until=self.deadline,
        )
        try:
            if kind == "blob":
                blob = BlobResponse.model_validate_json(raw)
                if blob.sha != oid or blob.size > max_bytes:
                    raise RepositoryReadError("OBJECT_IDENTITY_OR_LIMIT")
                body = base64.b64decode(blob.content.replace("\n", ""), validate=True)
                if len(body) != blob.size:
                    raise RepositoryReadError("BLOB_SIZE")
            else:
                tree = TreeResponse.model_validate_json(raw)
                if tree.sha != oid:
                    raise RepositoryReadError("TREE_IDENTITY")
                parts = []
                for entry in sorted(
                    tree.tree,
                    key=lambda e: (e.path + ("/" if e.type == "tree" else "")).encode(),
                ):
                    expected = {"040000": "tree", "160000": "commit"}.get(
                        entry.mode, "blob"
                    )
                    if entry.type != expected or any(c in entry.path for c in "/\0"):
                        raise RepositoryReadError("TREE_SCHEMA")
                    mode = "40000" if entry.mode == "040000" else entry.mode
                    parts.append(
                        mode.encode()
                        + b" "
                        + entry.path.encode()
                        + b"\0"
                        + bytes.fromhex(entry.sha)
                    )
                    previous = self.kinds.setdefault(entry.sha, entry.type)
                    if previous != entry.type:
                        raise RepositoryReadError("OBJECT_TYPE_COLLISION")
                body = b"".join(parts)
        except (ValidationError, ValueError, binascii.Error):
            raise RepositoryReadError("OBJECT_SCHEMA") from None
        if len(body) > max_bytes:
            raise RepositoryReadError("OBJECT_LIMIT")
        return kind, body
