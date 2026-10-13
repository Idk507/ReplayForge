"""Pure repository admission; no network, shell, checkout or filesystem reads."""

import re
from uuid import UUID

from packages.contracts.repository import RepositoryGrant, ScopeDecision


def admit(
    grant: RepositoryGrant, caller: UUID, repository: UUID, commit: str
) -> ScopeDecision:
    """Require exact identity/commit consent from a trusted configured grant.

    Authentication and persisted grant ownership belong to the future registry.
    Private permission cannot be inferred from a model request or public URL.
    """
    grant = RepositoryGrant.model_validate(grant.model_dump())
    allowed = (
        caller == grant.owner_id
        and repository == grant.repository_id
        and commit in grant.commits
        and grant.consent
        and (not grant.private or grant.private_access_approved)
    )
    return ScopeDecision(
        allowed=allowed,
        reason="ALLOW" if allowed else "SCOPE_DENIED",
        approved_paths=grant.approved_paths if allowed else (),
        tool_policy="READ_ONLY_PINNED" if allowed else "NONE",
    )


def allowed_entry(grant: RepositoryGrant, path: str, mode: str) -> bool:
    """Check raw Git tree paths before parsing/normalizing or materializing.

    This is lexical admission only. A future reader must verify pinned tree/blob
    identity, byte limits and every ancestor mode, and never follow host links.
    """
    grant = RepositoryGrant.model_validate(grant.model_dump())
    if mode not in {"100644", "100755"} or not safe_tree_path(path):
        return False
    return any(
        path.startswith(rule) if rule.endswith("/") else path == rule
        for rule in grant.approved_paths
    )


def safe_tree_path(path: str) -> bool:
    """Lexical portability check shared by file and ancestor admission."""
    if not 1 <= len(path) <= 240:
        return False
    if not path.isascii() or not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        return False
    for component in path.split("/"):
        if component in {"", ".", ".."} or component.endswith("."):
            return False
        folded = component.lower()
        stem = folded.split(".")[0]
        if (
            folded in {".git", ".github", ".env", ".ssh"}
            or folded.startswith(".env.")
            or folded.endswith((".pem", ".key", ".p12", ".pfx"))
            or stem in {"con", "prn", "aux", "nul"}
            or re.fullmatch(r"(?:com|lpt)[0-9]", stem)
        ):
            return False
    return True
