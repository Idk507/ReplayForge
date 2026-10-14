"""Bounded in-memory source packaging and authenticated encryption; no extraction."""

import base64
import binascii
import hashlib
import json
import os
import re
from types import MappingProxyType
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, SecretBytes, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.source import SealedSnapshot, SourceManifest
from packages.policies.repository import admit, allowed_entry
from services.repository.snapshot import FileIdentity, Snapshot, SnapshotError

PACKAGE_LIMIT = 3000000


class PackageError(Exception):
    """Safe stable error; never includes raw source, ciphertext or key bytes."""


class PayloadFile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    path: str = Field(max_length=240)
    content_b64: str = Field(max_length=87384, repr=False)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    files: tuple[PayloadFile, ...] = Field(max_length=128, repr=False)


def canonical(model: BaseModel) -> bytes:
    """Deterministic JSON, including plaintext only within the private codec."""
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def manifest_digest(manifest: SourceManifest) -> str:
    manifest = SourceManifest.model_validate(manifest.model_dump())
    return hashlib.sha256(canonical(manifest)).hexdigest()


def _admit(manifest: SourceManifest, grant: RepositoryGrant, caller: UUID) -> None:
    if sum(f.size_bytes for f in manifest.files) > 2097152:
        raise PackageError("SNAPSHOT_LIMIT")
    if (
        manifest.repository_id != grant.repository_id
        or not admit(
            grant, caller, manifest.repository_id, manifest.source_commit
        ).allowed
        or any(not allowed_entry(grant, f.path, "100644") for f in manifest.files)
    ):
        raise PackageError("SNAPSHOT_SCOPE_DENIED")


def _restore(
    payload: bytes, manifest: SourceManifest, grant: RepositoryGrant, caller: UUID
) -> Snapshot:
    _admit(manifest, grant, caller)
    if len(payload) > PACKAGE_LIMIT:
        raise PackageError("SNAPSHOT_LIMIT")
    try:
        parsed = Payload.model_validate_json(payload)
        if canonical(parsed) != payload or tuple(f.path for f in parsed.files) != tuple(
            f.path for f in manifest.files
        ):
            raise PackageError("SNAPSHOT_IDENTITY")
        content: dict[str, bytes] = {}
        identities = []
        total = 0
        for file, identity in zip(parsed.files, manifest.files, strict=True):
            body = base64.b64decode(file.content_b64, validate=True)
            total += len(body)
            if len(body) != identity.size_bytes or total > 2097152:
                raise PackageError("SNAPSHOT_LIMIT")
            if (
                hashlib.sha256(body).hexdigest() != identity.sha256
                or hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
                != identity.blob_id
            ):
                raise PackageError("SNAPSHOT_IDENTITY")
            body.decode("utf-8")
            if b"\0" in body or body.startswith(
                b"version https://git-lfs.github.com/spec/v1"
            ):
                raise PackageError("SNAPSHOT_BINARY_DENIED")
            content[file.path] = body
            identities.append(
                FileIdentity(
                    file.path, identity.blob_id, identity.sha256, identity.size_bytes
                )
            )
    except (ValidationError, ValueError, binascii.Error):
        raise PackageError("SNAPSHOT_SCHEMA") from None
    return Snapshot(
        manifest.source_commit,
        tuple(identities),
        MappingProxyType(content),
        manifest.commit_identity,
        manifest.evidence_origin,
    )


class SnapshotCipher:
    """Explicit runtime key; callers provision/retain/rotate it outside the DB.

    No key discovery/generation/persistence. Nonces are freshly generated here,
    not caller supplied. Source stays in memory and ciphertext only crosses the
    graph/storage boundary. Python cannot guarantee plaintext/key zeroization.
    """

    def __init__(self, key: SecretBytes, key_id: str) -> None:
        if len(key.get_secret_value()) != 32 or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", key_id
        ):
            raise PackageError("SNAPSHOT_KEY_CONFIG")
        self._cipher = AESGCM(key.get_secret_value())
        self.key_id = key_id

    @staticmethod
    def _aad(sealed: SealedSnapshot) -> bytes:
        return canonical(sealed.model_copy(update={"nonce": "", "ciphertext_b64": ""}))

    def seal(
        self,
        snapshot: Snapshot,
        manifest: SourceManifest,
        grant: RepositoryGrant,
        caller: UUID,
        run_id: UUID,
    ) -> SealedSnapshot:
        manifest = SourceManifest.model_validate(manifest.model_dump())
        _admit(manifest, grant, caller)
        if (
            snapshot.source_commit != manifest.source_commit
            or snapshot.commit_identity != manifest.commit_identity
            or snapshot.evidence_origin != manifest.evidence_origin
            or tuple(f.path for f in snapshot.files)
            != tuple(f.path for f in manifest.files)
        ):
            raise PackageError("SNAPSHOT_IDENTITY")
        try:
            payload = canonical(
                Payload(
                    files=tuple(
                        PayloadFile(
                            path=f.path,
                            content_b64=base64.b64encode(
                                snapshot.read_file(f.path, 65536)
                            ).decode(),
                        )
                        for f in manifest.files
                    )
                )
            )
        except SnapshotError:
            raise PackageError("SNAPSHOT_IDENTITY") from None
        _restore(payload, manifest, grant, caller)
        nonce = os.urandom(12)
        sealed = SealedSnapshot(
            run_id=run_id,
            manifest_sha256=manifest_digest(manifest),
            plaintext_sha256=hashlib.sha256(payload).hexdigest(),
            key_id=self.key_id,
            nonce=nonce.hex(),
            ciphertext_b64="A" * 24,
        )
        encrypted = self._cipher.encrypt(nonce, payload, self._aad(sealed))
        return sealed.model_copy(
            update={"ciphertext_b64": base64.b64encode(encrypted).decode()}
        )

    def open(
        self,
        sealed: SealedSnapshot,
        manifest: SourceManifest,
        grant: RepositoryGrant,
        caller: UUID,
        run_id: UUID,
    ) -> Snapshot:
        manifest = SourceManifest.model_validate(manifest.model_dump())
        _admit(manifest, grant, caller)
        try:
            sealed = SealedSnapshot.model_validate(sealed.model_dump())
            if (
                sealed.key_id != self.key_id
                or sealed.run_id != run_id
                or (sealed.manifest_sha256 != manifest_digest(manifest))
            ):
                raise PackageError("SNAPSHOT_BINDING")
            ciphertext = base64.b64decode(sealed.ciphertext_b64, validate=True)
            payload = self._cipher.decrypt(
                bytes.fromhex(sealed.nonce), ciphertext, self._aad(sealed)
            )
        except (InvalidTag, ValidationError, ValueError, binascii.Error):
            raise PackageError("SNAPSHOT_INTEGRITY") from None
        if hashlib.sha256(payload).hexdigest() != sealed.plaintext_sha256:
            raise PackageError("SNAPSHOT_INTEGRITY")
        return _restore(payload, manifest, grant, caller)
