"""Explicit in-memory key rotation; no key discovery, persistence or fallback."""

from collections.abc import Mapping
from types import MappingProxyType
from uuid import UUID

from pydantic import SecretBytes

from packages.contracts.repository import RepositoryGrant
from packages.contracts.source import SealedSnapshot, SourceManifest
from services.repository.packaging import PackageError, SnapshotCipher
from services.repository.snapshot import Snapshot


class SnapshotKeyring:
    """Seal with one active key; open only with the authenticated envelope's ID.

    Operators retain old keys until associated artifacts/backups expire. Create
    a new keyring to rotate; existing instances keep their immutable selection.
    Keys must be independent, supplied by a trusted runtime secret provider.
    """

    def __init__(self, keys: Mapping[str, SecretBytes], *, active_key_id: str) -> None:
        if not 1 <= len(keys) <= 16 or active_key_id not in keys:
            raise PackageError("SNAPSHOT_KEY_CONFIG")
        if any(not isinstance(key, SecretBytes) for key in keys.values()):
            raise PackageError("SNAPSHOT_KEY_CONFIG")
        if len({key.get_secret_value() for key in keys.values()}) != len(keys):
            raise PackageError("SNAPSHOT_KEY_CONFIG")
        self._ciphers = MappingProxyType(
            {name: SnapshotCipher(key, name) for name, key in keys.items()}
        )
        self._active_key_id = active_key_id

    def seal(
        self,
        snapshot: Snapshot,
        manifest: SourceManifest,
        grant: RepositoryGrant,
        caller: UUID,
        run_id: UUID,
    ) -> SealedSnapshot:
        """Never retry encryption with a historical key on failure."""
        return self._ciphers[self._active_key_id].seal(
            snapshot, manifest, grant, caller, run_id
        )

    def open(
        self,
        sealed: SealedSnapshot,
        manifest: SourceManifest,
        grant: RepositoryGrant,
        caller: UUID,
        run_id: UUID,
    ) -> Snapshot:
        """Unknown or retired key IDs fail closed without trying other keys."""
        cipher = self._ciphers.get(sealed.key_id)
        if cipher is None:
            raise PackageError("SNAPSHOT_KEY_UNAVAILABLE")
        return cipher.open(sealed, manifest, grant, caller, run_id)
