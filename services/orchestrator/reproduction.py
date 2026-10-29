"""Immutable reproduction handoff; no filesystem writes or execution authority."""

import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from packages.contracts.repair import ReproductionProposal
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, CommitSHA, RunState, Stage
from packages.policies.repository import admit, allowed_entry


class ReproductionHandoffError(Exception):
    """Sanitized denial without generated source or provider payloads."""


class ReproductionArtifact(BaseModel):
    """Trusted binding around untrusted code; construction cannot authorize it."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    repository_id: UUID
    source_commit: CommitSHA
    source_manifest_ref: ArtifactID
    context_refs: tuple[ArtifactID, ...]
    proposal: ReproductionProposal

    def canonical_bytes(self) -> bytes:
        """Serialize exactly for content-addressed retention and transport."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def artifact_ref(self) -> str:
        """Include source/run identity as well as code in the artifact identity."""
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


def bind_reproduction(
    proposal: ReproductionProposal,
    run: RunState,
    grant: RepositoryGrant,
    caller: UUID,
) -> ReproductionArtifact:
    """Validate a candidate before retention; never mutate the checkpoint."""
    proposal = ReproductionProposal.model_validate(proposal.model_dump())
    run = RunState.model_validate(run.model_dump())
    grant = RepositoryGrant.model_validate(grant.model_dump())
    if not admit(grant, caller, run.repository_id, run.source_commit).allowed:
        raise ReproductionHandoffError("REPRODUCTION_SCOPE_DENIED")
    if (
        run.current_stage
        not in {
            Stage.TRIAGING,
            Stage.REPRODUCING,
            Stage.PATCHING,
            Stage.VERIFYING,
            Stage.VERIFIED,
            Stage.NOT_VERIFIED,
            Stage.INCONCLUSIVE,
        }
        or run.cancellation_requested
        or not run.context_refs
        or run.source_manifest_ref is None
    ):
        raise ReproductionHandoffError("REPRODUCTION_STATE_DENIED")
    if not allowed_entry(grant, proposal.target_path, "100644"):
        raise ReproductionHandoffError("REPRODUCTION_PATH_DENIED")
    return ReproductionArtifact(
        run_id=run.run_id,
        repository_id=run.repository_id,
        source_commit=run.source_commit,
        source_manifest_ref=run.source_manifest_ref,
        context_refs=run.context_refs,
        proposal=proposal,
    )


def load_reproduction(
    payload: bytes,
    expected_ref: str,
    run: RunState,
    grant: RepositoryGrant,
    caller: UUID,
) -> ReproductionArtifact:
    """Revalidate retained bytes, consent and checkpoint binding before handoff."""
    if (
        len(payload) > 131072
        or "sha256:" + hashlib.sha256(payload).hexdigest() != expected_ref
    ):
        raise ReproductionHandoffError("REPRODUCTION_INTEGRITY")
    try:
        artifact = ReproductionArtifact.model_validate_json(payload)
    except ValidationError:
        raise ReproductionHandoffError("REPRODUCTION_INTEGRITY") from None
    rebound = bind_reproduction(artifact.proposal, run, grant, caller)
    if artifact.canonical_bytes() != payload or rebound != artifact:
        raise ReproductionHandoffError("REPRODUCTION_BINDING_DENIED")
    if run.reproduction_test_ref != expected_ref:
        raise ReproductionHandoffError("REPRODUCTION_BINDING_DENIED")
    return artifact
