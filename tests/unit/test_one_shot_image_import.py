from uuid import uuid4

import httpx
import pytest
from contree_sdk.utils.oci import OCIReference

from packages.contracts.sandbox import RunnerError
from services.runner.one_shot_image_import import (
    ImageImportCallError,
    ImageImportConfig,
    ImageImportPlan,
    OneShotNebiusImageImporter,
)

PROJECT = "aiproject-example"
API_KEY = "fake-local-key-never-printed"
SOURCE = "docker://docker.io/library/python@sha256:" + ("a" * 64)


def config() -> ImageImportConfig:
    return ImageImportConfig(api_key=API_KEY, project_id=PROJECT)


def plan() -> ImageImportPlan:
    return ImageImportPlan(source=SOURCE, timeout_seconds=30)


def response(request: httpx.Request, payload: dict[str, object], status: int = 200):
    return httpx.Response(status, json=payload, request=request)


class MemoryImportJournal:
    """Small callback implementation matching the durable import lifecycle."""

    def __init__(
        self,
        request_digest: str,
        *,
        claim_callback=lambda _: True,
        operation_callback=lambda *_: None,
        diagnostic_callback=lambda _: None,
        known_operation=None,
    ) -> None:
        self.project_id = PROJECT
        self.request_digest = request_digest
        self.operation_id = known_operation
        self.state = "OPERATION_KNOWN" if known_operation else None
        self.claim_callback = claim_callback
        self.operation_callback = operation_callback
        self.diagnostic_callback = diagnostic_callback

    def claim(self, request_digest: str) -> bool:
        accepted = self.claim_callback(request_digest)
        if accepted:
            self.state = "INTENT"
        return accepted

    def operation(self, operation_id, request_digest: str) -> None:
        self.operation_callback(operation_id, request_digest)
        self.operation_id = operation_id
        self.state = "OPERATION_KNOWN"

    def diagnostic(self, record) -> None:
        self.diagnostic_callback(record)
        if record.outcome in {"SUBMISSION_UNKNOWN", "PROVIDER_REJECTED"}:
            self.state = record.outcome


def memory_journal(
    *,
    request_digest: str | None = None,
    claim=lambda _: True,
    operation=lambda *_: None,
    diagnostic=lambda _: None,
    known_operation=None,
) -> MemoryImportJournal:
    return MemoryImportJournal(
        request_digest or plan().request_digest(),
        claim_callback=claim,
        operation_callback=operation,
        diagnostic_callback=diagnostic,
        known_operation=known_operation,
    )


@pytest.mark.anyio
async def test_live_use_requires_explicit_opt_in() -> None:
    with pytest.raises(RunnerError, match="S_LIVE_NOT_AUTHORIZED"):
        OneShotNebiusImageImporter(config())


@pytest.mark.anyio
async def test_post_is_claimed_once_and_operation_is_retained_before_poll() -> None:
    operation = uuid4()
    image = uuid4()
    calls: list[str] = []
    order: list[str] = []
    retained: list[tuple[str, object]] = []
    states = iter(("PENDING", "SUCCESS"))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tokenfactory.nebius.com"
        assert request.headers["project"] == PROJECT
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        calls.append(request.method + " " + request.url.path)
        if request.method == "POST":
            body = request.read().decode()
            assert body == ('{"registry":{"url":"' + SOURCE + '"},"timeout":30}')
            return response(request, {"uuid": str(operation)}, 201)
        order.append("poll")
        state = next(states)
        return response(
            request,
            {
                "uuid": str(operation),
                "status": state,
                "kind": "image_import",
                "result": {"image": str(image)} if state == "SUCCESS" else None,
            },
        )

    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        accepted = await importer.submit(
            plan(),
            journal=memory_journal(
                claim=lambda digest: order.append("claim") is None,
                operation=lambda op, digest: (
                    order.append("retained-operation"),
                    retained.append(("operation", op)),
                ),
                diagnostic=lambda diagnostic: retained.append(
                    ("diagnostic", diagnostic)
                ),
            ),
        )
        assert accepted.operation_id == operation
        assert retained[0] == ("operation", operation)
        result = await importer.wait(
            operation,
            journal=memory_journal(
                request_digest=accepted.request_sha256,
                known_operation=operation,
                diagnostic=lambda diagnostic: retained.append(
                    ("diagnostic", diagnostic)
                ),
            ),
            poll_interval_seconds=0,
        )
        assert result.image_uuid == image and result.status == "SUCCESS"
        with pytest.raises(RunnerError, match="S_CLOSED_OR_ALREADY_SUBMITTED"):
            await importer.submit(
                plan(),
                journal=memory_journal(),
            )

    assert calls == [
        "POST /sandboxes/v1/images/import",
        f"GET /sandboxes/v1/operations/{operation}",
        f"GET /sandboxes/v1/operations/{operation}",
    ]
    assert order == ["claim", "retained-operation", "poll", "poll"]
    assert len(retained) == 1


@pytest.mark.anyio
async def test_http_rejection_is_redacted_and_never_retried() -> None:
    attempts = 0
    diagnostics = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            404,
            json={"error": "secret response body"},
            headers={"X-Request-ID": "safe-req-4", "Set-Cookie": "private=secret"},
            request=request,
        )

    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        with pytest.raises(ImageImportCallError) as raised:
            await importer.submit(
                plan(),
                journal=memory_journal(
                    operation=lambda *_: pytest.fail("no operation on 404"),
                    diagnostic=diagnostics.append,
                ),
            )
        assert raised.value.diagnostic.outcome == "PROVIDER_REJECTED"
        assert raised.value.diagnostic.http_status == 404
        assert raised.value.diagnostic.request_id == "safe-req-4"
        assert "secret" not in raised.value.diagnostic.model_dump_json()
        with pytest.raises(RunnerError, match="S_CLOSED_OR_ALREADY_SUBMITTED"):
            await importer.submit(
                plan(),
                journal=memory_journal(diagnostic=diagnostics.append),
            )

    assert attempts == 1 and len(diagnostics) == 1


@pytest.mark.anyio
async def test_uncertain_transport_failure_is_sticky_and_not_retried() -> None:
    attempts = 0
    diagnostics = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("credential=secret")

    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        with pytest.raises(ImageImportCallError) as raised:
            await importer.submit(
                plan(),
                journal=memory_journal(diagnostic=diagnostics.append),
            )
        assert raised.value.diagnostic.outcome == "SUBMISSION_UNKNOWN"
        assert raised.value.diagnostic.operation_id is None
        assert "secret" not in raised.value.diagnostic.model_dump_json()
        with pytest.raises(RunnerError):
            await importer.submit(
                plan(),
                journal=memory_journal(diagnostic=diagnostics.append),
            )

    assert attempts == 1


@pytest.mark.anyio
async def test_journal_failure_after_201_does_not_repeat_post() -> None:
    operation = uuid4()
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return response(request, {"uuid": str(operation)}, 201)

    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        with pytest.raises(RunnerError, match="S_OPERATION_RETENTION_FAILED"):
            await importer.submit(
                plan(),
                journal=memory_journal(
                    operation=lambda *_: (_ for _ in ()).throw(OSError("disk"))
                ),
            )
        with pytest.raises(RunnerError, match="S_CLOSED_OR_ALREADY_SUBMITTED"):
            await importer.submit(
                plan(),
                journal=memory_journal(),
            )
        # The known operation is still available in memory for reconciliation.
        assert importer._operation_id == operation

    assert attempts == 1


@pytest.mark.anyio
async def test_invalid_reference_denied_before_durable_claim_or_network() -> None:
    attempts = 0
    claims = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return response(request, {"uuid": str(uuid4())}, 201)

    def count_claim() -> bool:
        nonlocal claims
        claims += 1
        return True

    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        with pytest.raises(RunnerError, match="S_IMAGE_REFERENCE_INVALID"):
            await importer.submit(
                ImageImportPlan(source="docker://docker.io/library/python:latest"),
                journal=memory_journal(claim=lambda _: count_claim()),
            )
    assert attempts == 0
    assert claims == 0


@pytest.mark.anyio
async def test_journal_project_and_request_are_checked_before_post() -> None:
    attempts = 0
    claims = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return response(request, {"uuid": str(uuid4())}, 201)

    def count_claim(_: str) -> bool:
        nonlocal claims
        claims += 1
        return True

    original_plan = plan()
    journal = memory_journal(
        request_digest="0" * 64,
        claim=count_claim,
    )
    journal.project_id = "a-different-project"
    async with OneShotNebiusImageImporter(
        config(), transport=httpx.MockTransport(handler)
    ) as importer:
        with pytest.raises(RunnerError, match="S_IMPORT_JOURNAL_BINDING"):
            await importer.submit(original_plan, journal=journal)
    assert claims == 0 and attempts == 0


@pytest.mark.anyio
async def test_poll_only_accepts_the_durably_known_operation() -> None:
    operation = uuid4()
    diagnostics = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return response(
            request,
            {"uuid": str(operation), "kind": "image_import", "status": "SUCCESS"},
        )

    async with OneShotNebiusImageImporter(
        config(),
        transport=httpx.MockTransport(handler),
        known_operation=operation,
        known_request_hash=plan().request_digest(),
    ) as importer:
        with pytest.raises(RunnerError, match="S_SCHEMA_INVALID"):
            await importer.wait(
                operation,
                journal=memory_journal(
                    request_digest=plan().request_digest(),
                    known_operation=operation,
                    diagnostic=diagnostics.append,
                ),
                poll_interval_seconds=0,
            )
        with pytest.raises(RunnerError, match="S_OPERATION_NOT_OWNED"):
            await importer.wait(
                uuid4(),
                journal=memory_journal(
                    request_digest=plan().request_digest(),
                    known_operation=operation,
                    diagnostic=diagnostics.append,
                ),
            )


@pytest.mark.anyio
async def test_restored_known_operation_polls_without_resubmitting() -> None:
    operation, image = uuid4(), uuid4()
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return response(
            request,
            {
                "uuid": str(operation),
                "kind": "image_import",
                "status": "SUCCESS",
                "result": {"image": str(image)},
            },
        )

    async with OneShotNebiusImageImporter(
        config(),
        transport=httpx.MockTransport(handler),
        known_operation=operation,
        known_request_hash=plan().request_digest(),
    ) as importer:
        result = await importer.wait(
            operation,
            journal=memory_journal(
                request_digest=plan().request_digest(),
                known_operation=operation,
                diagnostic=lambda _: pytest.fail("unexpected poll diagnostic"),
            ),
            poll_interval_seconds=0,
        )
    assert result.image_uuid == image
    assert calls == ["GET"]


@pytest.mark.anyio
async def test_polling_is_bounded_and_terminal_provider_failure_is_retained() -> None:
    operation = uuid4()
    requests = 0

    async def pending_handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return response(
            request,
            {
                "uuid": str(operation),
                "kind": "image_import",
                "status": "EXECUTING",
                "result": None,
            },
        )

    async with OneShotNebiusImageImporter(
        config(),
        transport=httpx.MockTransport(pending_handler),
        known_operation=operation,
        known_request_hash=plan().request_digest(),
    ) as importer:
        with pytest.raises(RunnerError, match="S_MATERIALIZATION_PENDING"):
            await importer.wait(
                operation,
                journal=memory_journal(
                    request_digest=plan().request_digest(),
                    known_operation=operation,
                    diagnostic=lambda _: pytest.fail("unexpected diagnostic"),
                ),
                max_polls=3,
                timeout_seconds=5,
                poll_interval_seconds=0,
            )
    assert requests == 3

    async def failed_handler(request: httpx.Request) -> httpx.Response:
        return response(
            request,
            {
                "uuid": str(operation),
                "kind": "image_import",
                "status": "FAILED",
                "error": "RuntimeError('registry HTTP 404; password=must-not-leak')",
                "result": None,
            },
        )

    async with OneShotNebiusImageImporter(
        config(),
        transport=httpx.MockTransport(failed_handler),
        known_operation=operation,
        known_request_hash=plan().request_digest(),
    ) as importer:
        result = await importer.wait(
            operation,
            journal=memory_journal(
                request_digest=plan().request_digest(),
                known_operation=operation,
                diagnostic=lambda _: pytest.fail(
                    "terminal result is not transport error"
                ),
            ),
            poll_interval_seconds=0,
        )
    assert result.status == "FAILED" and result.image_uuid is None
    assert result.failure_code == "REGISTRY_HTTP_ERROR"
    assert result.provider_http_status == 404
    assert result.error_sha256 is not None
    assert "must-not-leak" not in result.model_dump_json()


def test_import_plan_requires_digest_pinned_noncredentialed_oci_reference() -> None:
    with pytest.raises(RunnerError, match="S_IMAGE_REFERENCE_INVALID"):
        ImageImportPlan(
            source="docker://user:password@docker.io/library/python:latest"
        ).request_body()


def test_sdk_digest_parser_also_derives_a_target_tag_from_digest() -> None:
    reference = OCIReference.from_oci(SOURCE)

    assert reference.url == SOURCE
    assert reference.tag == "python@sha256:" + ("a" * 64)
    assert "tag" not in plan().request_body()
