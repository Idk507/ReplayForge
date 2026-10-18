"""Canonical hashes for bounded evidence artifacts before gate evaluation."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.run import ArtifactID


class ArtifactInput(BaseModel):
    """Trusted artifact bytes; manifests retain only their content identities."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,80}$")
    content: bytes = Field(max_length=65536, repr=False)


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    artifact_id: ArtifactID
    size_bytes: int = Field(strict=True, ge=0, le=65536)


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str = "1.0"
    workspace_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[ManifestEntry, ...] = Field(min_length=1, max_length=32)
    manifest_id: ArtifactID


def build_manifest(
    workspace_sha256: str, artifacts: tuple[ArtifactInput, ...]
) -> EvidenceManifest:
    """Build a stable content-addressed manifest and reject ambiguous names."""
    if len({item.name for item in artifacts}) != len(artifacts):
        raise ValueError("ARTIFACT_NAME_COLLISION")
    entries = tuple(
        ManifestEntry(
            name=item.name,
            artifact_id="sha256:" + hashlib.sha256(item.content).hexdigest(),
            size_bytes=len(item.content),
        )
        for item in sorted(artifacts, key=lambda item: item.name)
    )
    body = json.dumps(
        {
            "schema_version": "1.0",
            "workspace_sha256": workspace_sha256,
            "entries": [entry.model_dump() for entry in entries],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return EvidenceManifest(
        workspace_sha256=workspace_sha256,
        entries=entries,
        manifest_id="sha256:" + hashlib.sha256(body).hexdigest(),
    )


def verify_manifest(
    manifest: EvidenceManifest, artifacts: tuple[ArtifactInput, ...]
) -> bool:
    """Rebuild and compare the complete canonical manifest before trusting it."""
    try:
        rebuilt = build_manifest(manifest.workspace_sha256, artifacts)
    except (TypeError, ValueError):
        return False
    return rebuilt == manifest
