# Durable redacted incident inputs

API intake now prepares an IncidentReport after persisted repository admission.
Raw request text remains ephemeral; its existing request digest continues to bind
idempotency. Pattern redaction and the configured local bearer secret are removed
before truncation to 1024 characters. Truncation is explicit. Reports remain
UNTRUSTED_INCIDENT_DATA and cannot authorize execution. No model is called.

Migration 0004 adds incident_reports. Run creation stores the bounded JSON bytes
and SHA256 with checkpoint, initial event and job in one transaction. The run ID
binds the report to immutable owner/repository/source metadata. Matching retries
preserve the original input; conflicting request hashes remain rejected. Existing
metadata-only runs are not silently backfilled. Raw text stays out of events and
checkpoints; no new public report download route is exposed.

Store.incident reads only the owner's retained report and verifies its checksum
and schema. Runtime needs SELECT/INSERT on incident_reports and must lack
UPDATE/DELETE. Seven-day database-clock expiry denies reads, not deletion. These
checks do not protect against a privileged database administrator. Encryption,
purge/backup retention and complete DLP remain deployment prerequisites.

Trusted internal callers must use prepare_incident before Store.create; a typed
report alone does not prove redaction. prepare_incident accepts explicitly known
secrets; the API currently supplies its local bearer token. Pattern matching does
not reliably detect arbitrary bare credentials or personal information. This
local development slice is not suitable for confidential production reports.

Use bind_incident(store, source_loader) with the collection worker to bind the
retained report after graph scope admission. Missing/expired input raises
INCIDENT_UNAVAILABLE before invoking the source loader; there is no caller-text
fallback. Fresh worker instances recover the same retained report. The report
and provider trace are redacted again through the bounded context collector.
Read expiry is checked at load time; lease fencing still guards the eventual
context commit. Truncation remains recorded on the durable report, while the
context collector has its own explicit limits/omissions.

Authenticated repository acquisition remains pending. Persistence and synthetic
loader tests do not investigate a live incident or verify a fix. The worker has
no configured production source loader or polling daemon.
