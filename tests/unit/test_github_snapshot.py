"""Synthetic REST objects with actual Git-format hashes; nothing is executed."""

import base64
import hashlib
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from packages.contracts.repository import RepositoryGrant
from services.repository.github import GitHubReader, RepositoryReadError
from services.repository.snapshot import SnapshotError


def oid(kind: str, body: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()


def fixture(mode="100644", body=b"# synthetic untrusted code\nx=1\n"):
    blob = oid("blob", body)
    tree_bytes = mode.encode() + b" a.py\0" + bytes.fromhex(blob)
    tree = oid("tree", tree_bytes)
    root = oid("tree", b"40000 src\0" + bytes.fromhex(tree))
    commit = "a" * 40  # Commit identity is provider-attested, not fabricated raw bytes.
    grant = RepositoryGrant(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=(commit,),
        approved_paths=("src/",),
        consent=True,
    )
    responses = {
        "commits/" + commit: {"sha": commit, "tree": {"sha": root}},
        "trees/" + root: {
            "sha": root,
            "truncated": False,
            "tree": [{"path": "src", "mode": "040000", "type": "tree", "sha": tree}],
        },
        "trees/" + tree: {
            "sha": tree,
            "truncated": False,
            "tree": [{"path": "a.py", "mode": mode, "type": "blob", "sha": blob}],
        },
        "blobs/" + blob: {
            "sha": blob,
            "encoding": "base64",
            "size": len(body),
            "content": base64.b64encode(body).decode() + "\n",
        },
    }
    calls = []

    def handle(request):
        path = request.url.path.split("/git/")[1]
        calls.append(path)
        assert request.method == "GET" and not request.url.query
        return httpx.Response(200, json=responses[path])

    return grant, responses, calls, httpx.MockTransport(handle)


def test_http_snapshot_hashes_allowed_content_and_keeps_commit_provenance() -> None:
    grant, responses, calls, transport = fixture()
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        result = reader.acquire(grant, grant.owner_id, grant.commits[0])
    assert result.snapshot.read_file("src/a.py", 100).startswith(b"# synthetic")
    assert result.snapshot.commit_identity == "GITHUB_HTTPS_METADATA"
    assert result.resolution.evidence_origin == "TEST_TRANSPORT"
    assert not result.resolution.raw_commit_verified
    assert len(calls) == 4
    from services.context.collector import collect

    context = collect(result.snapshot, ("src/a.py",), "synthetic")
    assert context.commit_identity == "GITHUB_HTTPS_METADATA"


@pytest.mark.parametrize("mutation", ["truncated", "tree-hash", "blob-hash", "base64"])
def test_rest_tampering_fails_closed(mutation: str) -> None:
    grant, responses, calls, transport = fixture()
    root_key = next(k for k in responses if k.startswith("trees/"))
    blob_key = next(k for k in responses if k.startswith("blobs/"))
    if mutation == "truncated":
        responses[root_key]["truncated"] = True
    elif mutation == "tree-hash":
        responses[root_key]["tree"][0]["path"] = "other"
    elif mutation == "blob-hash":
        responses[blob_key]["content"] = base64.b64encode(b"wrong").decode()
        responses[blob_key]["size"] = 5
    else:
        responses[blob_key]["content"] = "%%%"
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        with pytest.raises((RepositoryReadError, SnapshotError)):
            reader.acquire(grant, grant.owner_id, grant.commits[0])


def test_symlink_rejected_without_reading_target_blob() -> None:
    grant, responses, calls, transport = fixture(mode="120000")
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        with pytest.raises(SnapshotError, match="ENTRY_MODE_DENIED"):
            reader.acquire(grant, grant.owner_id, grant.commits[0])
    assert not any(c.startswith("blobs/") for c in calls)


def test_unapproved_directory_is_not_fetched() -> None:
    grant, responses, calls, transport = fixture()
    grant = grant.model_copy(update={"approved_paths": ("tests/",)})
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        result = reader.acquire(grant, grant.owner_id, grant.commits[0])
    assert not result.snapshot.files
    assert len(calls) == 2


@pytest.mark.parametrize(
    "body", [b"\0", b"\xff", b"version https://git-lfs.github.com/spec/v1\n"]
)
def test_binary_and_lfs_remain_denied(body: bytes) -> None:
    grant, responses, calls, transport = fixture(body=body)
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        with pytest.raises(SnapshotError, match="BINARY"):
            reader.acquire(grant, grant.owner_id, grant.commits[0])


def test_wrong_caller_performs_no_http() -> None:
    grant, responses, calls, transport = fixture()
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        with pytest.raises(RepositoryReadError, match="SCOPE_DENIED"):
            reader.acquire(grant, uuid4(), grant.commits[0])
    assert not calls


def test_full_size_blob_fits_encoded_response_cap() -> None:
    grant, responses, calls, transport = fixture(body=b"x" * 65536)
    with GitHubReader(SecretStr("synthetic-read-token"), transport=transport) as reader:
        result = reader.acquire(grant, grant.owner_id, grant.commits[0])
    assert len(result.snapshot.read_file("src/a.py", 65536)) == 65536
