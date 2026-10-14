"""Consent-specific read smoke; does not execute, investigate or publish code."""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import CommitSHA
from services.repository.github import (
    CommitResolution,
    GitHubReader,
    RepositoryReadError,
)
from services.repository.snapshot import SnapshotError

REPOSITORY: Literal["https://github.com/Idk507/maf_ds_agent"] = (
    "https://github.com/Idk507/maf_ds_agent"
)


class ReadFileProof(BaseModel):
    """Only file identity; raw source is never printed or persisted by this probe."""

    path: Literal["README.md"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=65536)


class GitHubProbe(BaseModel):
    """Safe fixed-repository read observation, never repair verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    repository: Literal["https://github.com/Idk507/maf_ds_agent"] = REPOSITORY
    source_commit: CommitSHA | None = None
    status: Literal["NOT_RUN", "PASSED", "FAILED"] = "NOT_RUN"
    evidence_origin: Literal["NOT_RUN", "REMOTE_SERVICE", "TEST_TRANSPORT"] = "NOT_RUN"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: UUID = Field(default_factory=uuid4)
    policy_decision: str = "DENY_LIVE_NOT_AUTHORIZED"
    error_class: str | None = None
    resolution: CommitResolution | None = None
    files: tuple[ReadFileProof, ...] = ()
    artifacts: tuple[()] = ()
    retryable: Literal[False] = False
    limitations: tuple[str, ...] = (
        "Read-only README scope; no repository code was executed.",
        "Commit binding is provider HTTPS metadata, not raw commit verification.",
        "Credential least privilege is not verified; CLI credential may allow writes.",
        "File bytes are not retained; hashes are observations, not stored artifacts.",
    )


def probe(
    commit: str | None,
    token: SecretStr | None = None,
    *,
    live: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> GitHubProbe:
    """Run only the human-authorized public repo and explicitly pinned commit."""
    if not live:
        return GitHubProbe()
    if commit is None or not re.fullmatch(r"[a-f0-9]{40}", commit):
        return GitHubProbe(policy_decision="DENY_CONFIG", error_class="COMMIT_INVALID")
    if token is None:
        return GitHubProbe(
            source_commit=commit,
            policy_decision="DENY_CONFIG",
            error_class="CREDENTIAL_MISSING",
        )
    owner = uuid4()
    grant = RepositoryGrant(
        repository_id=uuid4(),
        owner_id=owner,
        remote_url=REPOSITORY,
        commits=(commit,),
        approved_paths=("README.md",),
        consent=True,
    )
    origin: Literal["REMOTE_SERVICE", "TEST_TRANSPORT"] = (
        "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
    )
    try:
        with GitHubReader(token, transport=transport) as reader:
            result = reader.acquire(grant, owner, commit)
        if tuple(item.path for item in result.snapshot.files) != ("README.md",):
            raise RepositoryReadError("EXPECTED_FILE_MISSING")
    except (RepositoryReadError, SnapshotError) as error:
        return GitHubProbe(
            source_commit=commit,
            status="FAILED",
            evidence_origin=origin,
            policy_decision="ALLOW_READ_ONLY_PINNED",
            error_class=str(error),
        )
    identity = result.snapshot.files[0]
    return GitHubProbe(
        source_commit=commit,
        status="PASSED",
        evidence_origin=origin,
        policy_decision="ALLOW_READ_ONLY_PINNED",
        resolution=result.resolution,
        files=(
            ReadFileProof(
                path="README.md", sha256=identity.sha256, size_bytes=identity.size_bytes
            ),
        ),
    )
