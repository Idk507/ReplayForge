# Local context collection

services.context.collector.collect consumes an already admitted in-memory
Snapshot, 1–16 unique exact paths and bounded error text. It never fetches files,
executes source or calls a model. Identity checks compare each source body's
SHA256 with the snapshot metadata before collection.

Source and error text are normalized for control characters, then explicitly
supplied secrets and common credential patterns are redacted. Private-key blocks
are removed even if the input ends before the closing marker. Known secrets in
remaining serialized metadata fail closed. This is conservative pattern matching,
not a guarantee of comprehensive DLP. Callers must supply applicable secrets and
review policy before forwarding private incident data to any provider.

The frozen ContextBundle distinguishes original and sanitized SHA256 values.
Only sanitized content is retained in that bundle. Paths/reasons and bounded
Python symbol names support later context selection. AST parsing is performed
only on bounded sanitized source; invalid syntax becomes an explicit parse
status. Parsing never imports or executes repository modules. Redaction can
change source syntax; this representation must not be used as executable code.

The final serialized JSON is capped at 4096 UTF-8 bytes, with a conservative
3800-byte file-admission threshold reserving omission metadata. This is a byte
budget, not a measured model tokenizer count. Oversized files are omitted with
CONTEXT_BUDGET rather than silently truncated. Error text is capped at 1024
characters with an omission marker; raw input errors are limited to 64 KiB.
Excessively expanded JSON fails closed. All content remains explicitly
UNTRUSTED_REPOSITORY_DATA and cannot authorize execution.

Acceptance tests use synthetic snapshots and a labeled model sink, proving a
supplied sentinel does not appear in the serialized payload. No production model
gateway or stack-trace-driven file discovery is implemented in this slice.
The original snapshot remains private trusted-worker memory and is not sanitized
in place. It must not be logged or exposed to model tools. M1 remains partial.

Sanitized bundles can now persist through the trusted context-artifact adapter;
see context-artifacts.md. Collector-to-graph wiring now exists; see collection-graph.md.

## Dependency fingerprints

Collection now includes dependency_files (path and original SHA256) and deps_hash.
It considers only six exact root names already available in the admitted snapshot:
pyproject.toml, uv.lock, poetry.lock, Pipfile.lock, requirements.txt and
requirements-dev.txt. It does not search nested directories, fetch additional
files, parse dependency URLs, install packages or execute build hooks. Each body
is read under the existing 64 KiB cap and checked against its declared size and
SHA256, even if it is not among selected source-context paths.

The aggregate is SHA256 of compact sorted-key JSON containing schema_version 1.0
and a path-sorted list of path/SHA256 pairs. Schema validation rejects duplicate
or unordered paths, unrecognized names and mismatched aggregate hashes. Only
identities enter this metadata; a manifest explicitly selected for ordinary
context still follows existing redaction and byte-budget rules. Dependency
metadata counts against the same 4096-byte serialized bundle limit.

This fingerprints the available approved subset, not an installed environment or
complete dependency closure. Absence is represented by no entries and deps_hash
null, not proof that the repository has no dependencies. No dependency validity,
pinning, reproducibility or vulnerability verdict is inferred from these hashes.
Store.store_context checks every dependency identity against SourceManifest before
any database write. Context readback supports older canonical artifacts missing
these fields without rewriting their bytes or digest; their dependency identity
remains unknown. No database migration is needed.
