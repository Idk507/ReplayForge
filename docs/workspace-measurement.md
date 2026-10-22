# Saved-image workspace measurement

`services.runner.workspace.measure_workspace` makes one bounded, read-only GET
of `/workspace/repo` using the provider's documented image archive endpoint.
It defaults to no live access. Controllers must resolve ownership and consent
before invoking this low-level adapter; it grants neither.

The parser never extracts or executes guest files. It accepts an uncompressed
archive of at most 64 KiB, 128 entries and 64 KiB total regular file contents.
Paths, entry kinds, collisions, ancestor aliases, special permissions and sparse
files are checked. Links and device entries are rejected. The versioned identity
hashes sorted paths, permission bits and actual file content hashes. Timestamps
and ownership are excluded. Explicit directory entries are significant, so this
is a conservative workspace identity, not a Git tree or OCI digest.

These small-fixture limits intentionally reject larger repositories. Measurement
is not an attestation: trusted source/result-image correlation, pre-execution
measurement retention, a separately provisioned receipt signer and durable
controller integration remain required before VERIFIED. No signing key is loaded
or created by this module.

Reference checked 2026-10-22: official provider OpenAPI,
https://eu-north.nebius.computer/static/api.yaml,
GET `/inspect/{image_uuid}/archive`, path query; documented POSIX PAX response.
No credentials or paid calls were used to read the specification.

Validation: `uv run pytest tests/unit tests/contract -q
--junitxml=docs/evidence/m3-workspace-regression.xml`: 811 passed, one existing
Starlette/AnyIO deprecation warning, 5.96 seconds, exit 0. Includes 20 tiny inert
archive/HTTP cases for this module. Ruff check, Ruff format check and strict mypy
(100 source files) pass. Database integration and live provider acceptance were
not rerun; this change has no DB, UI, dependency or dispatch mutation.
