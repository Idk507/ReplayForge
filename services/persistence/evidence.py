"""Private manifest/artifact retention; hashes do not grant verification authority."""

import base64
import binascii
import hashlib
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import CommitSHA, RunState, Stage
from packages.policies.repository import admit
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import canonical
from services.verifier.authenticated import (
    AuthenticatedVerdict,
    GateContext,
    GateVerifier,
    SignedGates,
)
from services.verifier.manifest import (
    ArtifactInput,
    EvidenceManifest,
    build_manifest,
    verify_manifest,
)


class EncodedArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,80}$")
    content_b64: str = Field(max_length=87384, repr=False)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    source_commit: CommitSHA
    manifest: EvidenceManifest
    files: tuple[EncodedArtifact, ...] = Field(min_length=1, max_length=32, repr=False)


def _decode(row: dict[str, object]) -> tuple[EvidenceBundle, tuple[ArtifactInput, ...]]:
    payload = row["payload"]
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise StoreError("EVIDENCE_INTEGRITY")
    body = bytes(payload)
    if (
        not 1 <= len(body) <= 3000000
        or hashlib.sha256(body).hexdigest() != row["payload_digest"]
    ):
        raise StoreError("EVIDENCE_INTEGRITY")
    try:
        bundle = EvidenceBundle.model_validate_json(body)
        artifacts = tuple(
            ArtifactInput(
                name=f.name, content=base64.b64decode(f.content_b64, validate=True)
            )
            for f in bundle.files
        )
        if (
            canonical(bundle) != body
            or bundle.manifest.manifest_id != "sha256:" + str(row["manifest_digest"])
            or not verify_manifest(bundle.manifest, artifacts)
        ):
            raise StoreError("EVIDENCE_INTEGRITY")
    except (ValidationError, ValueError, binascii.Error):
        raise StoreError("EVIDENCE_INTEGRITY") from None
    return bundle, artifacts


class EvidenceStore:
    """Worker-only writes and owner-scoped private reads, never a model tool.

    The trusted producer must sanitize before retention. Read expiry is not
    physical erasure. No source paths are opened and no binary artifact executes.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def evaluate(
        self,
        lease: Lease,
        owner: UUID,
        signed: SignedGates,
        verifier: GateVerifier,
        *,
        image_digest: str,
        configuration_ref: str,
        expected_version: int,
    ) -> AuthenticatedVerdict:
        """Authenticate gates against a fenced current checkpoint, without writes.

        Image/configuration identities and verifier keys are trusted operator
        configuration, never fields selected by a model or received envelope.
        This result is an evaluation, not a durable stage transition or approval.
        """
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            job = conn.execute(
                "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
                "AND token=%s AND status='leased' "
                "AND leased_until>clock_timestamp() FOR UPDATE",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None or job is None:
                raise StoreError("LEASE_LOST")
            run = RunState.model_validate(row["checkpoint"])
            if type(expected_version) is not int or run.version != expected_version:
                raise StoreError("STALE_VERSION")
            if (
                run.current_stage != Stage.VERIFYING
                or run.cancellation_requested
                or not run.patch_attempt_refs
                or run.reproduction_test_ref is None
                or run.baseline_result_ref is None
                or run.evidence_manifest_ref is None
            ):
                raise StoreError("EVIDENCE_STAGE_DENIED")
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if (
                consent is None
                or not admit(
                    RepositoryGrant.model_validate(consent["grant_json"]),
                    owner,
                    run.repository_id,
                    run.source_commit,
                ).allowed
            ):
                raise StoreError("EVIDENCE_SCOPE_DENIED")
            stored = conn.execute(
                "SELECT payload,payload_digest,manifest_digest FROM evidence_bundles "
                "WHERE run_id=%s AND manifest_digest=%s "
                "AND expires_at>clock_timestamp()",
                (run.run_id, run.evidence_manifest_ref[7:]),
            ).fetchone()
            if stored is None:
                raise StoreError("NOT_FOUND")
            bundle, artifacts = _decode(stored)
            if bundle.run_id != run.run_id or bundle.source_commit != run.source_commit:
                raise StoreError("EVIDENCE_INTEGRITY")
            expected = GateContext(
                run_id=run.run_id,
                repository_id=run.repository_id,
                run_version=run.version,
                source_commit=run.source_commit,
                patch_ref=run.patch_attempt_refs[-1],
                image_digest=image_digest,
                configuration_ref=configuration_ref,
                test_ref=run.reproduction_test_ref,
                workspace_sha256=bundle.manifest.workspace_sha256,
                manifest_ref=run.evidence_manifest_ref,
            )
            clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
            if clock is None or not isinstance(clock["now"], datetime):
                raise StoreError("EVIDENCE_CLOCK_INVALID")
            return verifier.verify(
                signed, expected, bundle.manifest, artifacts, now=clock["now"]
            )

    def retain(
        self,
        lease: Lease,
        owner: UUID,
        workspace_sha256: str,
        artifacts: tuple[ArtifactInput, ...],
        *,
        expected_version: int,
    ) -> RunState:
        """Commit a content bundle, checkpoint ref and audit event atomically."""
        if not 1 <= len(artifacts) <= 32:
            raise StoreError("EVIDENCE_LIMIT")
        try:
            artifacts = tuple(
                ArtifactInput.model_validate(a.model_dump()) for a in artifacts
            )
            manifest = build_manifest(workspace_sha256, artifacts)
        except (ValidationError, ValueError):
            raise StoreError("EVIDENCE_INVALID") from None
        lease = Lease.model_validate(lease.model_dump())
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (lease.run_id, owner),
            ).fetchone()
            job = conn.execute(
                "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
                "AND token=%s AND status='leased' "
                "AND leased_until>clock_timestamp() FOR UPDATE",
                (lease.run_id, lease.worker_id, lease.token),
            ).fetchone()
            if row is None or job is None:
                raise StoreError("LEASE_LOST")
            run = RunState.model_validate(row["checkpoint"])
            if run.current_stage != Stage.VERIFYING or run.cancellation_requested:
                raise StoreError("EVIDENCE_STAGE_DENIED")
            consent = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if (
                consent is None
                or not admit(
                    RepositoryGrant.model_validate(consent["grant_json"]),
                    owner,
                    run.repository_id,
                    run.source_commit,
                ).allowed
            ):
                raise StoreError("EVIDENCE_SCOPE_DENIED")
            existing = conn.execute(
                "SELECT payload,payload_digest,manifest_digest,"
                "expires_at>clock_timestamp() AS active FROM evidence_bundles "
                "WHERE run_id=%s AND manifest_digest=%s",
                (run.run_id, manifest.manifest_id[7:]),
            ).fetchone()
            if type(expected_version) is not int:
                raise StoreError("STALE_VERSION")
            if existing is not None:
                bundle, _ = _decode(existing)
                if not existing["active"]:
                    raise StoreError("EVIDENCE_EXPIRED")
                if (
                    run.evidence_manifest_ref == manifest.manifest_id
                    and bundle.run_id == run.run_id
                    and bundle.source_commit == run.source_commit
                    and run.version in {expected_version, expected_version + 1}
                ):
                    return run
                raise StoreError("EVIDENCE_CONFLICT")
            if run.version != expected_version:
                raise StoreError("STALE_VERSION")
            bundle = EvidenceBundle(
                run_id=run.run_id,
                source_commit=run.source_commit,
                manifest=manifest,
                files=tuple(
                    EncodedArtifact(
                        name=a.name,
                        content_b64=base64.b64encode(a.content).decode("ascii"),
                    )
                    for a in sorted(artifacts, key=lambda a: a.name)
                ),
            )
            body = canonical(bundle)
            if len(body) > 3000000:
                raise StoreError("EVIDENCE_LIMIT")
            conn.execute(
                "INSERT INTO evidence_bundles"
                "(run_id,manifest_digest,payload_digest,payload) VALUES (%s,%s,%s,%s)",
                (
                    run.run_id,
                    manifest.manifest_id[7:],
                    hashlib.sha256(body).hexdigest(),
                    body,
                ),
            )
            updated = RunState.model_validate(
                {
                    **run.model_dump(),
                    "version": run.version + 1,
                    "evidence_manifest_ref": manifest.manifest_id,
                }
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(updated.model_dump(mode="json")), updated.version, run.run_id),
            )
            self._store._event(conn, updated, "EVIDENCE_RETAINED")
            return updated

    def read(
        self, run_id: UUID, owner: UUID, manifest_ref: str
    ) -> tuple[EvidenceManifest, tuple[ArtifactInput, ...]]:
        """Validate scope, expiry and content before returning private bytes."""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest_ref):
            raise StoreError("EVIDENCE_REFERENCE_INVALID")
        with self._store._connection() as conn:
            row = conn.execute(
                "SELECT b.payload,b.payload_digest,b.manifest_digest,r.checkpoint "
                "FROM evidence_bundles b JOIN runs r ON r.id=b.run_id "
                "WHERE b.run_id=%s AND r.owner_id=%s AND b.manifest_digest=%s "
                "AND b.expires_at>clock_timestamp()",
                (run_id, owner, manifest_ref[7:]),
            ).fetchone()
        if row is None:
            raise StoreError("NOT_FOUND")
        bundle, artifacts = _decode(row)
        run = RunState.model_validate(row["checkpoint"])
        if bundle.run_id != run_id or bundle.source_commit != run.source_commit:
            raise StoreError("EVIDENCE_INTEGRITY")
        return bundle.manifest, artifacts
