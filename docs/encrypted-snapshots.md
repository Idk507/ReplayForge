# Opt-in encrypted source snapshots

Migration 0006 adds sealed_snapshots. The collection loader accepts an explicit
SnapshotCipher; without it, source bytes remain ephemeral and no sealed artifact
is stored. Keys are supplied as 32-byte SecretBytes with a public key identifier.
No key is discovered, generated, logged or persisted by the application. Tests
use temporary random keys; a production key has not been provisioned.

SnapshotKeyring adds explicit rotation with 1–16 independent 32-byte keys and
one required active key ID. New writes always use the active key; reads select
only the envelope's key ID and authenticate that ID through AES-GCM. Missing or
retired keys fail with SNAPSHOT_KEY_UNAVAILABLE, without trying another key.
Empty rings, absent active IDs, malformed IDs/lengths and duplicate key material
are rejected. Constructor input is copied. Rotation constructs a new keyring;
there is no mutable global key registry or key fallback.

The GitHub loader accepts either SnapshotCipher or SnapshotKeyring, remaining
disabled by default. A trusted operator secret provider must supply keys at
startup. Retain historical keys until every dependent artifact and backup is
expired or intentionally abandoned. Reusing an ID for different material makes
old artifacts unreadable; key identifiers must remain stable across restarts.
Do not put key bytes in configuration documents, command arguments, database
rows, events or repository files. Production provisioning, backup/recovery and
rotation scheduling remain operational work; these tests do not prove them.

The codec uses cryptography 50.0.2 AESGCM (AES-256-GCM), with a fresh random
12-byte nonce per seal. Authenticated metadata binds schema, algorithm, run,
source manifest digest, plaintext digest and key identifier. Decryption rejects
wrong keys, changed ciphertext, another run/manifest and unauthorized callers.
See the [official AESGCM API](https://cryptography.io/en/latest/hazmat/primitives/aead/).

Only approved UTF-8 files are packaged in canonical JSON in memory: at most 128
files, 64 KiB per file and 2 MiB decoded source in total. Plaintext JSON is capped
at 3,000,000 bytes; stored canonical ciphertext envelopes at 4,100,000 bytes.
Both sealing and restoration check file paths, sizes, SHA256 and Git blob IDs
against the manifest. Restoration does not extract files or execute code.

Only ciphertext crosses the graph/storage boundary. The reducer atomically
inserts context, manifest and sealed artifact with checkpoint references, event
and job completion. A failed event insert rolls all artifacts back. Internal
Store.sealed_snapshot reads enforce owner, seven-day expiry, checksum, canonical
schema and run binding. Runtime permissions are SELECT/INSERT only. No raw-source
download API or database decryption key is introduced.

Threat limits: original approved code can contain embedded secrets, so decrypted
source remains sensitive. Python memory cannot guarantee key/plaintext erasure.
Manifest metadata remains readable. Checksums do not defeat a privileged database
administrator; AEAD needs an independently protected key. Key provisioning,
rotation/recovery, deletion of expired rows and backup retention remain pending.
Seven-day read expiry is not deletion. Production retention stays disabled until
operators provide suitable key management.

This is an approved subset of source files, not a complete checkout: executable
modes and full tree contents are not packaged. Provider commit identity remains
GITHUB_HTTPS_METADATA, not raw commit-object verification. Remote transfer,
isolated replay and runner controls are unverified. No real repository was
executed, paid provider called or remote write performed for this slice.

Acceptance: `uv run python -m scripts.test_postgres --evidence-name
m1-encrypted-snapshot-tests.xml` passed 360 tests, including 52 PostgreSQL cases,
on 2026-10-15. This includes opt-in/off API-to-worker paths, restart reads,
cross-owner denial, tamper/expiry denial, rollback and restricted runtime writes.
See evidence/m1-encrypted-snapshot-checks.json for commands and artifact hashes.
