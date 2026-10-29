"""Operator-only controller for one-shot image import and safe recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict

from packages.contracts.sandbox import RunnerError
from services.persistence.image_imports import ImageImportAttempt, ImageImportJournal
from services.persistence.store import Store, StoreError
from services.runner.import_diagnostics import ImportDiagnostic
from services.runner.one_shot_image_import import (
    ImageImportCallError,
    ImageImportConfig,
    ImageImportPlan,
    ImageImportResult,
    OneShotNebiusImageImporter,
    validate_import_polling,
)

ImportReportStatus = Literal[
    "NOT_RUN",
    "PENDING",
    "SUBMISSION_UNKNOWN",
    "PROVIDER_REJECTED",
    "SUCCESS",
    "FAILED",
    "CANCELLED",
]


class ImageImportReport(BaseModel):
    """Bounded public report; excludes credentials and provider response bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["1.0"] = "1.0"
    status: ImportReportStatus
    observed_at: AwareDatetime
    project_id: str
    source_digest: str
    request_sha256: str
    operation_id: UUID | None = None
    image_uuid: UUID | None = None
    diagnostic: ImportDiagnostic | None = None
    error_code: str | None = None
    provider_http_status: int | None = None
    error_sha256: str | None = None


class ImageImportController:
    """Compose durable journal and one-shot HTTP importer for operators only.

    `allow_live=False` returns without database or network I/O. When enabled,
    the controller submits once only if no attempt exists. Any existing intent
    without a retained operation remains uncertain and is never resubmitted.
    Existing known operations are recovered through GET only.
    """

    def __init__(
        self,
        store: Store,
        config: ImageImportConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._config = ImageImportConfig.model_validate(config.model_dump())
        self._transport = transport

    async def run(
        self,
        plan: ImageImportPlan,
        *,
        allow_live: bool = False,
        max_polls: int = 20,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 1,
    ) -> ImageImportReport:
        """Report dry-run, submit an absent attempt, or recover a known operation."""
        if type(allow_live) is not bool:
            raise RunnerError("S_POLICY_DENIED")
        validate_import_polling(max_polls, timeout_seconds, poll_interval_seconds)
        plan = ImageImportPlan.model_validate(plan.model_dump())
        source_digest = plan.source.rsplit("@sha256:", 1)[-1]
        request_sha256 = plan.request_digest()
        if not allow_live:
            return self._report(
                status="NOT_RUN",
                source_digest=source_digest,
                request_sha256=request_sha256,
            )

        journal = ImageImportJournal(self._store, self._config.project_id, plan)
        existing = self._existing(journal)
        if existing is not None:
            prior_diagnostic = self._last_diagnostic(existing)
            prior_observed_at = (
                prior_diagnostic.observed_at
                if prior_diagnostic is not None
                else existing.events[0].recorded_at
            )
            if existing.state in {"INTENT", "SUBMISSION_UNKNOWN"}:
                return self._report(
                    status="SUBMISSION_UNKNOWN",
                    source_digest=source_digest,
                    request_sha256=request_sha256,
                    diagnostic=prior_diagnostic,
                    error_code="RECONCILIATION_REQUIRED_NO_REPOST",
                    observed_at=prior_observed_at,
                )
            if existing.state == "PROVIDER_REJECTED":
                return self._report(
                    status="PROVIDER_REJECTED",
                    source_digest=source_digest,
                    request_sha256=request_sha256,
                    diagnostic=prior_diagnostic,
                    error_code="RETRY_DENIED_FOR_SOURCE_DIGEST",
                    observed_at=prior_observed_at,
                )
            if existing.state == "TERMINAL":
                return self._terminal_report(journal, source_digest)
            if existing.state != "OPERATION_KNOWN" or existing.operation_id is None:
                raise StoreError("IMAGE_IMPORT_INTEGRITY")
            known_operation = existing.operation_id
        else:
            known_operation = None

        async with OneShotNebiusImageImporter(
            self._config,
            allow_live=allow_live,
            transport=self._transport,
            known_operation=known_operation,
            known_request_hash=request_sha256 if known_operation else None,
        ) as importer:
            if known_operation is None:
                try:
                    accepted = await importer.submit(plan, journal=journal)
                except ImageImportCallError as error:
                    return self._diagnostic_report(
                        error.diagnostic, source_digest, request_sha256
                    )
                operation_id = accepted.operation_id
            else:
                operation_id = known_operation
            try:
                result = await importer.wait(
                    operation_id,
                    journal=journal,
                    max_polls=max_polls,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
            except ImageImportCallError as error:
                return self._diagnostic_report(
                    error.diagnostic, source_digest, request_sha256
                )
            except RunnerError as error:
                if str(error) == "S_MATERIALIZATION_PENDING":
                    return self._report(
                        status="PENDING",
                        source_digest=source_digest,
                        request_sha256=request_sha256,
                        operation_id=operation_id,
                    )
                raise
            journal.complete(result)
        return self._result_report(result, source_digest)

    @staticmethod
    def _existing(journal: ImageImportJournal) -> ImageImportAttempt | None:
        try:
            return journal.history()
        except StoreError as error:
            if str(error) == "IMAGE_IMPORT_NOT_FOUND":
                return None
            raise

    @staticmethod
    def _last_diagnostic(attempt: ImageImportAttempt) -> ImportDiagnostic | None:
        for event in reversed(attempt.events):
            if event.kind == "DIAGNOSTIC":
                return ImportDiagnostic.model_validate(event.payload)
        return None

    def _terminal_report(
        self, journal: ImageImportJournal, source_digest: str
    ) -> ImageImportReport:
        attempt = journal.history()
        for event in reversed(attempt.events):
            if event.kind == "TERMINAL":
                result = ImageImportResult.model_validate(event.payload)
                return self._result_report(result, source_digest)
        raise StoreError("IMAGE_IMPORT_INTEGRITY")

    def _diagnostic_report(
        self,
        diagnostic: ImportDiagnostic,
        source_digest: str,
        request_sha256: str,
    ) -> ImageImportReport:
        if diagnostic.outcome == "PROVIDER_REJECTED":
            status: ImportReportStatus = "PROVIDER_REJECTED"
        elif diagnostic.outcome == "SUBMISSION_UNKNOWN":
            status = "SUBMISSION_UNKNOWN"
        else:
            status = "PENDING"
        return self._report(
            status=status,
            source_digest=source_digest,
            request_sha256=request_sha256,
            operation_id=diagnostic.operation_id,
            diagnostic=diagnostic,
            error_code=diagnostic.outcome,
            observed_at=diagnostic.observed_at,
        )

    def _result_report(
        self, result: ImageImportResult, source_digest: str
    ) -> ImageImportReport:
        return self._report(
            status=result.status,
            source_digest=source_digest,
            request_sha256=result.request_sha256,
            operation_id=result.operation_id,
            image_uuid=result.image_uuid,
            error_code=result.failure_code,
            provider_http_status=result.provider_http_status,
            error_sha256=result.error_sha256,
            observed_at=result.observed_at,
        )

    def _report(
        self,
        *,
        status: ImportReportStatus,
        source_digest: str,
        request_sha256: str,
        operation_id: UUID | None = None,
        image_uuid: UUID | None = None,
        diagnostic: ImportDiagnostic | None = None,
        error_code: str | None = None,
        provider_http_status: int | None = None,
        error_sha256: str | None = None,
        observed_at: datetime | None = None,
    ) -> ImageImportReport:
        return ImageImportReport(
            status=status,
            observed_at=observed_at if observed_at is not None else datetime.now(UTC),
            project_id=self._config.project_id,
            source_digest=source_digest,
            request_sha256=request_sha256,
            operation_id=operation_id,
            image_uuid=image_uuid,
            diagnostic=diagnostic,
            error_code=error_code,
            provider_http_status=provider_http_status,
            error_sha256=error_sha256,
        )
