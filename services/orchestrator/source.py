"""Trusted scoped source loader; no environment discovery or scheduling."""

import httpx
from pydantic import SecretStr

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState
from packages.contracts.source import SourceFile, SourceManifest
from packages.policies.repository import admit, allowed_entry
from services.orchestrator.collection import (
    CollectionInputs,
    InputLoader,
    SourceInputError,
)
from services.repository.github import GitHubReader, RepositoryReadError
from services.repository.keyring import SnapshotKeyring
from services.repository.packaging import PackageError, SnapshotCipher
from services.repository.snapshot import SnapshotError


def github_inputs(
    token: SecretStr,
    paths: tuple[str, ...],
    *,
    known_secrets: tuple[SecretStr, ...] = (),
    transport: httpx.BaseTransport | None = None,
    snapshot_cipher: SnapshotCipher | SnapshotKeyring | None = None,
) -> InputLoader:
    """Create an operator-configured loader; construction performs no HTTP.

    Combine with bind_incident and tick. Each invocation owns/closes its client.
    All failures are terminal for this run; new intake is required after repair
    of source/config/service access. No denied action is automatically retried.
    """
    if not 1 <= len(paths) <= 16 or len(set(paths)) != len(paths):
        raise ValueError("PATH_LIMIT")
    if len(known_secrets) > 31:
        raise ValueError("SECRET_CONFIG")

    def load(run: RunState, grant: RepositoryGrant) -> CollectionInputs:
        if not admit(
            grant, grant.owner_id, run.repository_id, run.source_commit
        ).allowed:
            raise SourceInputError("SOURCE_REJECTED")
        if any(not allowed_entry(grant, path, "100644") for path in paths):
            raise SourceInputError("SOURCE_REJECTED")
        try:
            with GitHubReader(token, transport=transport) as reader:
                acquired = reader.acquire(grant, grant.owner_id, run.source_commit)
        except RepositoryReadError as error:
            unavailable = str(error) in {
                "AUTH_DENIED",
                "RATE_LIMITED",
                "UNAVAILABLE",
                "TIMEOUT",
                "TOKEN_INVALID",
            }
            raise SourceInputError(
                "SOURCE_UNAVAILABLE" if unavailable else "SOURCE_REJECTED"
            ) from None
        except SnapshotError:
            raise SourceInputError("SOURCE_REJECTED") from None
        manifest = SourceManifest(
            repository_id=run.repository_id,
            source_commit=run.source_commit,
            root_tree=acquired.resolution.tree_sha,
            evidence_origin=acquired.resolution.evidence_origin,
            observed_at=acquired.resolution.observed_at,
            trace_id=acquired.resolution.trace_id,
            files=tuple(
                SourceFile(
                    path=item.path,
                    blob_id=item.blob_id,
                    sha256=item.sha256,
                    size_bytes=item.size_bytes,
                )
                for item in acquired.snapshot.files
            ),
        )
        try:
            sealed = (
                snapshot_cipher.seal(
                    acquired.snapshot, manifest, grant, grant.owner_id, run.run_id
                )
                if snapshot_cipher is not None
                else None
            )
        except PackageError:
            raise SourceInputError("SOURCE_REJECTED") from None
        return CollectionInputs(
            acquired.snapshot,
            paths,
            known_secrets=(token, *known_secrets),
            source_manifest=manifest,
            sealed_snapshot=sealed,
        )

    return load
