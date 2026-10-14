"""Synthetic Git object transport; no Git processes or repository execution."""

import hashlib
from uuid import uuid4

import pytest

from packages.contracts.repository import RepositoryGrant
from services.repository.snapshot import SnapshotError, load_snapshot


class Objects:
    """Explicit fake transport holding real-format Git object bytes."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, bytes]] = {}
        self.calls = 0

    def add(self, kind: str, body: bytes) -> str:
        oid = hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()
        self.values[oid] = kind, body
        return oid

    def read_object(self, oid: str, max_bytes: int) -> tuple[str, bytes]:
        self.calls += 1
        return self.values[oid]


def fixture(
    mode: str = "100644", name: bytes = b"a.py", content: bytes = b"print('data')\n"
) -> tuple[Objects, RepositoryGrant, str]:
    objects = Objects()
    blob = objects.add("blob", content)
    subtree = objects.add(
        "tree", mode.encode() + b" " + name + b"\0" + bytes.fromhex(blob)
    )
    root = objects.add("tree", b"40000 src\0" + bytes.fromhex(subtree))
    commit = objects.add(
        "commit", f"tree {root}\nauthor Synthetic\n\nfixture\n".encode()
    )
    grant = RepositoryGrant(
        repository_id=uuid4(),
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=(commit,),
        approved_paths=("src/",),
        consent=True,
    )
    return objects, grant, commit


def test_pinned_snapshot_reads_data_and_hashes() -> None:
    objects, grant, commit = fixture()
    snapshot = load_snapshot(objects, grant, grant.owner_id, commit)
    assert snapshot.source_commit == commit
    assert snapshot.read_file("src/a.py", 100) == b"print('data')\n"
    assert snapshot.files[0].sha256 == hashlib.sha256(b"print('data')\n").hexdigest()
    with pytest.raises(SnapshotError, match="PATH_DENIED"):
        snapshot.read_file("../a.py", 100)
    with pytest.raises(SnapshotError, match="READ_LIMIT"):
        snapshot.read_file("src/a.py", 1)


def test_denied_scope_performs_no_transport_call() -> None:
    objects, grant, commit = fixture()
    with pytest.raises(SnapshotError, match="SCOPE_DENIED"):
        load_snapshot(objects, grant, uuid4(), commit)
    assert objects.calls == 0


def test_tampered_object_rejected() -> None:
    objects, grant, commit = fixture()
    objects.values[commit] = ("commit", b"substituted")
    with pytest.raises(SnapshotError, match="OBJECT_IDENTITY"):
        load_snapshot(objects, grant, grant.owner_id, commit)


@pytest.mark.parametrize(
    "mode,name,body",
    [
        ("120000", b"a.py", b"/host/secret"),
        ("160000", b"a.py", b"submodule"),
        ("100644", b"../a.py", b"x"),
        ("100644", b"CON.py", b"x"),
        ("100644", b"a.py", b"\x00binary"),
        ("100644", b"a.py", b"version https://git-lfs.github.com/spec/v1\n"),
    ],
)
def test_unsafe_objects_fail_closed(mode: str, name: bytes, body: bytes) -> None:
    objects, grant, commit = fixture(mode, name, body)
    with pytest.raises(SnapshotError):
        load_snapshot(objects, grant, grant.owner_id, commit)


def test_case_collision_rejected() -> None:
    objects, grant, commit = fixture()
    blob = objects.add("blob", b"x")
    tree = objects.add(
        "tree",
        b"100644 a.py\0" + bytes.fromhex(blob) + b"100644 A.py\0" + bytes.fromhex(blob),
    )
    root = objects.add("tree", b"40000 src\0" + bytes.fromhex(tree))
    commit = objects.add("commit", f"tree {root}\n\n".encode())
    grant = grant.model_copy(update={"commits": (commit,)})
    with pytest.raises(SnapshotError, match="PATH_COLLISION"):
        load_snapshot(objects, grant, grant.owner_id, commit)


@pytest.mark.parametrize(
    "body", [b"100644 broken", b"100644 a.py\0short", b"bad\0" + b"x" * 20]
)
def test_malformed_tree_rejected(body: bytes) -> None:
    objects, grant, _ = fixture()
    tree = objects.add("tree", body)
    commit = objects.add("commit", f"tree {tree}\n\n".encode())
    grant = grant.model_copy(update={"commits": (commit,)})
    with pytest.raises(SnapshotError):
        load_snapshot(objects, grant, grant.owner_id, commit)


def test_missing_object_is_safe_error() -> None:
    objects, grant, commit = fixture()
    del objects.values[commit]
    with pytest.raises(SnapshotError, match="OBJECT_UNAVAILABLE"):
        load_snapshot(objects, grant, grant.owner_id, commit)


def test_oversized_blob_rejected_even_when_transport_ignores_limit() -> None:
    objects, grant, commit = fixture(content=b"x" * 65537)
    with pytest.raises(SnapshotError, match="OBJECT_SCHEMA"):
        load_snapshot(objects, grant, grant.owner_id, commit)


def test_wrong_type_rejected() -> None:
    objects, grant, commit = fixture()
    objects.values[commit] = ("blob", objects.values[commit][1])
    with pytest.raises(SnapshotError, match="OBJECT_SCHEMA"):
        load_snapshot(objects, grant, grant.owner_id, commit)


def test_out_of_scope_directory_not_requested() -> None:
    objects, grant, commit = fixture()
    original = objects.values[commit][1].split(b"\n")[0][5:].decode()
    root = objects.add(
        "tree", objects.values[original][1] + b"40000 docs\0" + b"x" * 20
    )
    commit = objects.add("commit", f"tree {root}\n\n".encode())
    grant = grant.model_copy(update={"commits": (commit,)})
    assert len(load_snapshot(objects, grant, grant.owner_id, commit).files) == 1


@pytest.mark.parametrize("name", [b".env", b".env.example", b"secrets.key"])
def test_excluded_secret_file_is_omitted_without_fetching_its_blob(name: bytes) -> None:
    objects, grant, commit = fixture()
    original = objects.values[commit][1].split(b"\n")[0][5:].decode()
    forbidden_blob = "d" * 40  # Deliberately absent: reading it would fail.
    root = objects.add(
        "tree",
        objects.values[original][1]
        + b"100644 "
        + name
        + b"\0"
        + bytes.fromhex(forbidden_blob),
    )
    commit = objects.add("commit", f"tree {root}\n\n".encode())
    grant = grant.model_copy(update={"commits": (commit,)})
    snapshot = load_snapshot(objects, grant, grant.owner_id, commit)
    assert tuple(f.path for f in snapshot.files) == ("src/a.py",)
    with pytest.raises(SnapshotError, match="PATH_DENIED"):
        snapshot.read_file(name.decode(), 100)


@pytest.mark.parametrize("deep", [False, True])
def test_traversal_budget_is_enforced(deep: bool) -> None:
    objects, grant, _ = fixture()
    empty = objects.add("tree", b"")
    if deep:
        tree = empty
        for _ in range(18):
            tree = objects.add("tree", b"40000 nested\0" + bytes.fromhex(tree))
    else:
        tree = objects.add(
            "tree",
            b"".join(
                f"40000 folder{i}".encode() + b"\0" + bytes.fromhex(empty)
                for i in range(129)
            ),
        )
    root = objects.add("tree", b"40000 src\0" + bytes.fromhex(tree))
    commit = objects.add("commit", f"tree {root}\n\n".encode())
    grant = grant.model_copy(update={"commits": (commit,)})
    with pytest.raises(SnapshotError, match="TREE_DEPTH|SNAPSHOT_BUDGET"):
        load_snapshot(objects, grant, grant.owner_id, commit)
    assert objects.calls <= 128
