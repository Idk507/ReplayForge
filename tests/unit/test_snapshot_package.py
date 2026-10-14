"""Encrypted source round-trips without filesystem extraction or execution."""

import hashlib
import os
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

import pytest
from pydantic import SecretBytes

from packages.contracts.repository import RepositoryGrant
from packages.contracts.source import SourceFile, SourceManifest
from services.repository.packaging import PackageError, SnapshotCipher
from services.repository.snapshot import FileIdentity, Snapshot


def fixture():
    owner, repository = uuid4(), uuid4()
    body = b"# synthetic untrusted source\nx=1\n"
    blob = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    sha = hashlib.sha256(body).hexdigest()
    grant = RepositoryGrant(
        repository_id=repository,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=("a" * 40,),
        approved_paths=("src/",),
        consent=True,
    )
    manifest = SourceManifest(
        repository_id=repository,
        source_commit="a" * 40,
        root_tree="b" * 40,
        evidence_origin="TEST_TRANSPORT",
        observed_at=datetime.now(UTC),
        trace_id=uuid4(),
        files=(
            SourceFile(path="src/a.py", blob_id=blob, sha256=sha, size_bytes=len(body)),
        ),
    )
    snapshot = Snapshot(
        "a" * 40,
        (FileIdentity("src/a.py", blob, sha, len(body)),),
        MappingProxyType({"src/a.py": body}),
        "GITHUB_HTTPS_METADATA",
        "TEST_TRANSPORT",
    )
    return snapshot, manifest, grant, body


def test_encrypted_package_round_trip_and_random_nonce() -> None:
    snapshot, manifest, grant, body = fixture()
    cipher = SnapshotCipher(SecretBytes(os.urandom(32)), "test-key")
    run_id = uuid4()
    sealed = cipher.seal(snapshot, manifest, grant, grant.owner_id, run_id)
    again = cipher.seal(snapshot, manifest, grant, grant.owner_id, run_id)
    assert sealed.nonce != again.nonce
    assert "synthetic untrusted source" not in sealed.model_dump_json()
    assert "synthetic untrusted source" not in repr(sealed)
    restored = cipher.open(sealed, manifest, grant, grant.owner_id, run_id)
    assert restored.read_file("src/a.py", 65536) == body
    assert restored.evidence_origin == "TEST_TRANSPORT"


@pytest.mark.parametrize("mutation", ["run", "manifest", "ciphertext", "key", "caller"])
def test_binding_tamper_wrong_key_and_cross_owner_rejected(mutation) -> None:
    snapshot, manifest, grant, body = fixture()
    cipher = SnapshotCipher(SecretBytes(os.urandom(32)), "test-key")
    run_id = uuid4()
    sealed = cipher.seal(snapshot, manifest, grant, grant.owner_id, run_id)
    caller = grant.owner_id
    if mutation == "run":
        run_id = uuid4()
    elif mutation == "manifest":
        manifest = manifest.model_copy(update={"root_tree": "c" * 40})
    elif mutation == "ciphertext":
        sealed = sealed.model_copy(update={"ciphertext_b64": "AAAA"})
    elif mutation == "key":
        cipher = SnapshotCipher(SecretBytes(os.urandom(32)), "test-key")
    else:
        caller = uuid4()
    with pytest.raises(PackageError):
        cipher.open(sealed, manifest, grant, caller, run_id)


def test_source_hash_mismatch_prevents_packaging() -> None:
    snapshot, manifest, grant, body = fixture()
    forged = Snapshot(
        snapshot.source_commit,
        snapshot.files,
        MappingProxyType({"src/a.py": b"changed"}),
        snapshot.commit_identity,
        snapshot.evidence_origin,
    )
    with pytest.raises(PackageError):
        SnapshotCipher(SecretBytes(os.urandom(32)), "test-key").seal(
            forged, manifest, grant, grant.owner_id, uuid4()
        )


@pytest.mark.parametrize("key", [b"", b"x" * 31])
def test_invalid_key_configuration_is_safe(key: bytes) -> None:
    with pytest.raises(PackageError, match="SNAPSHOT_KEY_CONFIG"):
        SnapshotCipher(SecretBytes(key), "test-key")


def test_decoded_source_budget_precedes_packaging() -> None:
    snapshot, manifest, grant, body = fixture()
    entries = tuple(
        manifest.files[0].model_copy(
            update={"path": f"src/a{i:03d}.py", "size_bytes": 65536}
        )
        for i in range(33)
    )
    large = manifest.model_copy(update={"files": entries})
    with pytest.raises(PackageError, match="SNAPSHOT_LIMIT"):
        SnapshotCipher(SecretBytes(os.urandom(32)), "test-key").seal(
            snapshot, large, grant, grant.owner_id, uuid4()
        )
