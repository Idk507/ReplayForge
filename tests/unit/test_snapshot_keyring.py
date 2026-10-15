"""Rotation retains old decrypt keys without silently falling back on writes."""

import os
from uuid import uuid4

import pytest
from pydantic import SecretBytes

from services.repository.keyring import SnapshotKeyring
from services.repository.packaging import PackageError
from tests.unit.test_snapshot_package import fixture


def test_rotation_and_restart_recover_old_and_new_snapshots() -> None:
    snapshot, manifest, grant, body = fixture()
    old, new = SecretBytes(os.urandom(32)), SecretBytes(os.urandom(32))
    run_id = uuid4()
    first = SnapshotKeyring({"old": old}, active_key_id="old")
    sealed_old = first.seal(snapshot, manifest, grant, grant.owner_id, run_id)
    rotated = SnapshotKeyring({"old": old, "new": new}, active_key_id="new")
    sealed_new = rotated.seal(snapshot, manifest, grant, grant.owner_id, run_id)
    assert sealed_old.key_id == "old"
    assert sealed_new.key_id == "new"
    restarted = SnapshotKeyring({"old": old, "new": new}, active_key_id="new")
    for sealed in (sealed_old, sealed_new):
        assert (
            restarted.open(sealed, manifest, grant, grant.owner_id, run_id).read_file(
                "src/a.py", 65536
            )
            == body
        )
    retired = SnapshotKeyring({"new": new}, active_key_id="new")
    with pytest.raises(PackageError, match="SNAPSHOT_KEY_UNAVAILABLE"):
        retired.open(sealed_old, manifest, grant, grant.owner_id, run_id)
    forged = sealed_old.model_copy(update={"key_id": "new"})
    with pytest.raises(PackageError, match="SNAPSHOT_INTEGRITY"):
        restarted.open(forged, manifest, grant, grant.owner_id, run_id)


@pytest.mark.parametrize(
    "case", ["empty", "missing", "size", "id", "limit", "duplicate"]
)
def test_invalid_configuration_fails_closed(case: str) -> None:
    key = SecretBytes(os.urandom(32))
    keys = {"active": key}
    active = "active"
    if case == "empty":
        keys = {}
    elif case == "missing":
        active = "missing"
    elif case == "size":
        keys = {"active": SecretBytes(b"short")}
    elif case == "id":
        keys = {"bad id": key}
        active = "bad id"
    elif case == "limit":
        keys = {f"key-{i}": SecretBytes(os.urandom(32)) for i in range(17)}
        active = "key-0"
    else:
        keys["alias"] = key
    with pytest.raises(PackageError, match="SNAPSHOT_KEY_CONFIG"):
        SnapshotKeyring(keys, active_key_id=active)


def test_configuration_is_copied_and_repr_has_no_keys() -> None:
    snapshot, manifest, grant, _ = fixture()
    key = SecretBytes(os.urandom(32))
    keys = {"active": key}
    ring = SnapshotKeyring(keys, active_key_id="active")
    keys.clear()
    assert key.get_secret_value().hex() not in repr(ring)
    assert (
        ring.seal(snapshot, manifest, grant, grant.owner_id, uuid4()).key_id == "active"
    )
