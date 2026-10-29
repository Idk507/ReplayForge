"""Mocked REST acceptance for offline Nebius pytest image preparation."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from packages.contracts.sandbox import RunnerError
from services.runner.image_preparation import (
    BASE_IMAGE,
    ImagePreparationPlan,
    NebiusImagePreparation,
    load_verified_wheelhouse,
)

PROJECT = "aiproject-preparation-test"
KEY = "test-secret-do-not-print"
OPERATION = UUID("12345678-1234-5678-1234-567812345678")
DERIVED = UUID("aaaaaaaa-1234-5678-1234-567812345678")


class MemoryJournal:
    def __init__(self, plan: ImagePreparationPlan):
        self.project_id = PROJECT
        self.request_digest = plan.request_digest()
        self.operation_id = None
        self.state = None
        self.events: list[str] = []
        self.uploads: dict[str, UUID] = {}
        self.pending_upload: str | None = None
        self.spawn_intent_recorded = False

    def claim(self, request_digest):
        self.events.append("INTENT")
        self.state = "INTENT"
        return request_digest == self.request_digest

    def operation(self, operation_id, request_digest):
        assert request_digest == self.request_digest
        assert self.spawn_intent_recorded
        self.operation_id = operation_id
        self.state = "OPERATION_KNOWN"
        self.events.append("OPERATION_KNOWN")

    def diagnostic(self, record):
        self.state = record.outcome
        self.events.append(record.outcome)

    def complete(self, result):
        self.state = "TERMINAL"
        self.result = result
        self.events.append("TERMINAL")

    def upload_intent(self, filename):
        assert self.pending_upload is None
        self.pending_upload = filename
        self.events.append(f"UPLOAD_INTENT:{filename}")

    def upload_complete(self, filename, file_id, digest):
        assert self.pending_upload == filename
        self.pending_upload = None
        self.uploads[filename] = file_id
        self.events.append(f"UPLOAD_KNOWN:{filename}:{digest}")

    def spawn_intent(self, uploaded):
        assert uploaded == self.uploads
        self.spawn_intent_recorded = True
        self.events.append("SPAWN_INTENT")


def fixture_plan(tmp_path: Path) -> tuple[ImagePreparationPlan, dict[str, bytes]]:
    # Use the repository's verified artifact manifest, not newly downloaded data.
    root = Path("artifacts/nebius-pytest-wheelhouse")
    plan = ImagePreparationPlan.from_wheelhouse(
        manifest_path=Path("docs/evidence/nebius-pytest-wheelhouse.json"),
        wheelhouse=root,
    )
    payloads = {wheel.filename: wheel.path.read_bytes() for wheel in plan.wheels}
    return plan, payloads


def configuration():
    from services.runner.one_shot_image_import import ImageImportConfig

    return ImageImportConfig(api_key=KEY, project_id=PROJECT)


def response(request, body, status=200):
    return httpx.Response(status, json=body, request=request)


def process_result(exit_code):
    return {
        "state": {"exit_code": exit_code, "signal": None, "timed_out": False},
        "stdout": {
            "value": base64.b64encode(b"OFFLINE_INSTALL_OK\n").decode(),
            "encoding": "base64",
            "truncated": False,
        },
        "stderr": {"value": "", "encoding": "ascii", "truncated": False},
    }


def test_wheelhouse_rejects_modified_bytes(tmp_path):
    plan, _ = fixture_plan(tmp_path)
    wheel = plan.wheels[0]
    corrupted = tmp_path / wheel.name
    corrupted.write_bytes(wheel.path.read_bytes() + b"tampered")
    with pytest.raises(RunnerError, match="S_WHEEL_INTEGRITY"):
        load_verified_wheelhouse(
            plan.manifest_path,
            {
                wheel.filename: corrupted,
                **{
                    item.filename: item.path
                    for item in plan.wheels
                    if item.filename != wheel.filename
                },
            },
        )


def test_plan_rejects_base_image_rebinding(tmp_path):
    plan, _ = fixture_plan(tmp_path)
    with pytest.raises(ValidationError):
        plan.model_copy(update={"base_image": uuid4()}).model_validate(
            plan.model_copy(update={"base_image": uuid4()}).model_dump()
        )


def test_success_uploads_exact_wheels_disables_network_and_retains_image(tmp_path):
    plan, payloads = fixture_plan(tmp_path)
    journal = MemoryJournal(plan)
    seen_files = []

    def handler(request):
        if request.url.path.endswith("/v1/files"):
            assert journal.events[-1].startswith("UPLOAD_INTENT:")
            body = request.content
            digest = hashlib.sha256(body).hexdigest()
            seen_files.append(digest)
            return response(request, {"uuid": str(uuid4()), "sha256": digest}, 201)
        if request.url.path.endswith("/v1/instances"):
            data = json.loads(request.content)
            assert journal.events[-1] == "SPAWN_INTENT"
            assert data["image"] == str(BASE_IMAGE)
            assert data["networking"] == {"enabled": False}
            assert data["env"] == {} and data["preserve_env"] is False
            assert data["disposable"] is False and data["shell"] is False
            assert KEY not in request.content.decode()
            assert set(data["files"]) == {
                f"/tmp/replayforge-wheelhouse/{name}" for name in payloads
            }
            return response(request, {"uuid": str(OPERATION)}, 201)
        if request.url.path.endswith(f"/v1/operations/{OPERATION}"):
            return response(
                request,
                {
                    "uuid": str(OPERATION),
                    "kind": "instance",
                    "status": "SUCCESS",
                    "image_uuid": str(BASE_IMAGE),
                    "result_image_uuid": str(DERIVED),
                    "metadata": {"result": process_result(0)},
                },
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

    client = NebiusImagePreparation(
        configuration(),
        plan,
        journal,
        allow_live=True,
        transport=httpx.MockTransport(handler),
        wheelhouse=payloads,
    )
    result = asyncio.run(client.prepare(max_polls=1, poll_interval_seconds=0))
    assert result.operation_id == OPERATION
    assert result.image_uuid == DERIVED
    assert journal.operation_id == OPERATION
    expected_upload_events = [
        event
        for wheel in plan.wheels
        for event in (
            f"UPLOAD_INTENT:{wheel.filename}",
            f"UPLOAD_KNOWN:{wheel.filename}:{wheel.sha256}",
        )
    ]
    assert journal.events == [
        "INTENT",
        *expected_upload_events,
        "SPAWN_INTENT",
        "OPERATION_KNOWN",
        "TERMINAL",
    ]
    assert sorted(seen_files) == sorted(wheel.sha256 for wheel in plan.wheels)
    asyncio.run(client.close())


@pytest.mark.parametrize(
    "fault",
    [
        "bad_upload_hash",
        "invalid_upload_uuid",
        "missing_image",
        "invalid_image_uuid",
        "wrong_base",
        "failed_install",
    ],
)
def test_preparation_rejects_invalid_provider_results(tmp_path, fault):
    plan, payloads = fixture_plan(tmp_path)
    journal = MemoryJournal(plan)

    def handler(request):
        if request.url.path.endswith("/v1/files"):
            digest = hashlib.sha256(request.content).hexdigest()
            if fault == "bad_upload_hash":
                digest = "0" * 64
            if fault == "invalid_upload_uuid":
                return response(request, {"uuid": "not-a-uuid", "sha256": digest}, 201)
            return response(request, {"uuid": str(uuid4()), "sha256": digest}, 201)
        if request.url.path.endswith("/v1/instances"):
            return response(request, {"uuid": str(OPERATION)}, 201)
        return response(
            request,
            {
                "uuid": str(OPERATION),
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": str(uuid4())
                if fault == "wrong_base"
                else str(BASE_IMAGE),
                "result_image_uuid": (
                    None
                    if fault == "missing_image"
                    else "not-a-uuid"
                    if fault == "invalid_image_uuid"
                    else str(DERIVED)
                ),
                "metadata": {
                    "result": process_result(4 if fault == "failed_install" else 0)
                },
            },
        )

    client = NebiusImagePreparation(
        configuration(),
        plan,
        journal,
        allow_live=True,
        transport=httpx.MockTransport(handler),
        wheelhouse=payloads,
    )
    with pytest.raises(RunnerError):
        asyncio.run(client.prepare(max_polls=1, poll_interval_seconds=0))
    if fault in {"missing_image", "failed_install"}:
        assert journal.state == "TERMINAL"
        assert journal.events[-1] == "TERMINAL"
    asyncio.run(client.close())


def test_uncertain_post_is_sticky_and_not_retried(tmp_path):
    plan, payloads = fixture_plan(tmp_path)
    journal = MemoryJournal(plan)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/v1/files"):
            digest = hashlib.sha256(request.content).hexdigest()
            return response(request, {"uuid": str(uuid4()), "sha256": digest}, 201)
        raise httpx.ReadTimeout("uncertain", request=request)

    client = NebiusImagePreparation(
        configuration(),
        plan,
        journal,
        allow_live=True,
        transport=httpx.MockTransport(handler),
        wheelhouse=payloads,
    )
    with pytest.raises(RunnerError, match="S_SUBMISSION_UNKNOWN"):
        asyncio.run(client.prepare(max_polls=1, poll_interval_seconds=0))
    post_count = calls
    with pytest.raises(RunnerError):
        asyncio.run(client.prepare(max_polls=1, poll_interval_seconds=0))
    assert calls == post_count
    assert journal.state == "SUBMISSION_UNKNOWN"
    asyncio.run(client.close())


def test_recovery_polls_known_operation_without_post(tmp_path):
    plan, _ = fixture_plan(tmp_path)
    journal = MemoryJournal(plan)
    journal.state = "OPERATION_KNOWN"
    journal.operation_id = OPERATION
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "GET"
        return response(
            request,
            {
                "uuid": str(OPERATION),
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": str(BASE_IMAGE),
                "result_image_uuid": str(DERIVED),
                "metadata": {"result": process_result(0)},
            },
        )

    client = NebiusImagePreparation(
        configuration(),
        plan,
        journal,
        allow_live=True,
        transport=httpx.MockTransport(handler),
        known_operation=OPERATION,
    )
    result = asyncio.run(client.recover(max_polls=1, poll_interval_seconds=0))
    assert result.image_uuid == DERIVED
    assert calls == ["GET"]
    asyncio.run(client.close())
