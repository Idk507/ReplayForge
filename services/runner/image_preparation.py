"""One-shot REST preparation of the locked pytest wheelhouse image.

This operator-only adapter uploads bytes, then executes one application-owned
offline installer with guest networking disabled. It never receives source
code and never retries POST requests.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from packages.contracts.sandbox import RunnerError
from services.runner.import_diagnostics import ImportDiagnostic
from services.runner.one_shot_image_import import ImageImportConfig, ImageImportResult
from services.runner.responses import ProcessResult

SANDBOX_URL = "https://api.tokenfactory.nebius.com/sandboxes/"
BASE_IMAGE = UUID("4fa16c8f-62a9-35a4-a4ea-07b7e8cd44ae")
MAX_WHEEL_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 65536
MAX_POLLS = 20
MAX_WAIT_SECONDS = 300
WHEEL_LOCK: dict[str, tuple[str, str, int]] = {
    "iniconfig-2.3.1-py3-none-any.whl": (
        "iniconfig",
        "9121e2c1fdb355232495be3194c8dfe87ccc2d5dee45947b78e68f499790d7a7",
        7552,
    ),
    "packaging-26.3-py3-none-any.whl": (
        "packaging",
        "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
        129956,
    ),
    "pluggy-1.6.0-py3-none-any.whl": (
        "pluggy",
        "e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746",
        20538,
    ),
    "pygments-2.21.0-py3-none-any.whl": (
        "pygments",
        "2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9",
        1250147,
    ),
    "pytest-9.0.3-py3-none-any.whl": (
        "pytest",
        "2c5efc453d45394fdd706ade797c0a81091eccd1d6e4bccfcd476e2b8e0ab5d9",
        375249,
    ),
}

INSTALL_PROGRAM = r"""import importlib.util, subprocess, sys, sysconfig
if importlib.util.find_spec("pip") is None:
    raise SystemExit("PIP_UNAVAILABLE")
wheel_dir = "/tmp/replayforge-wheelhouse"
target = sysconfig.get_paths()["purelib"]
packages = [
    "iniconfig==2.3.1", "packaging==26.3", "pluggy==1.6.0",
    "Pygments==2.21.0", "pytest==9.0.3",
]
command = [
    sys.executable, "-I", "-m", "pip", "install", "--no-index", "--no-deps",
    "--only-binary=:all:", "--no-cache-dir", "--find-links", wheel_dir,
    "--target", target, *packages,
]
result = subprocess.run(
    command, stdin=subprocess.DEVNULL, capture_output=True, timeout=240, check=False
)
if result.returncode:
    raise SystemExit("OFFLINE_INSTALL_FAILED")
print("OFFLINE_INSTALL_OK")
"""


class WheelArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    name: str
    version: str
    filename: str
    sha256: str
    size_bytes: int
    path: Path


def load_verified_wheelhouse(
    manifest_path: Path, files: Mapping[str, Path]
) -> dict[str, bytes]:
    """Verify exact allowlisted local wheel files against lock manifest data."""
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError, TypeError):
        raise RunnerError("S_WHEEL_MANIFEST_INVALID") from None
    records = manifest.get("packages") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "VERIFIED"
        or not isinstance(records, list)
        or len(records) != len(WHEEL_LOCK)
        or set(files) != set(WHEEL_LOCK)
    ):
        raise RunnerError("S_WHEEL_MANIFEST_INVALID")
    record_map = {
        item.get("filename"): item for item in records if isinstance(item, dict)
    }
    if len(record_map) != len(WHEEL_LOCK):
        raise RunnerError("S_WHEEL_MANIFEST_INVALID")
    result: dict[str, bytes] = {}
    for filename, (package, digest, size) in WHEEL_LOCK.items():
        record = record_map.get(filename)
        path = files[filename]
        if (
            record is None
            or record.get("name") != package
            or record.get("version") != filename.split("-")[1]
            or record.get("sha256") != f"sha256:{digest}"
            or record.get("size_bytes") != size
            or record.get("status") != "VERIFIED"
            or path.is_symlink()
        ):
            raise RunnerError("S_WHEEL_MANIFEST_INVALID")
        try:
            data = path.read_bytes()
        except OSError:
            raise RunnerError("S_WHEEL_INTEGRITY") from None
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise RunnerError("S_WHEEL_INTEGRITY")
        result[filename] = data
    return result


class ImagePreparationPlan(BaseModel):
    """Immutable request identity, independent of uploaded provider file IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    base_image: UUID = BASE_IMAGE
    wheels: tuple[WheelArtifact, ...] = Field(min_length=5, max_length=5)
    manifest_path: Path

    @model_validator(mode="after")
    def exact_locked_inputs(self) -> ImagePreparationPlan:
        if self.base_image != BASE_IMAGE:
            raise ValueError("S_PREPARATION_BASE_IMAGE_DENIED")
        expected = {
            filename: (name, filename.split("-")[1], digest, size)
            for filename, (name, digest, size) in WHEEL_LOCK.items()
        }
        filenames = tuple(wheel.filename for wheel in self.wheels)
        if filenames != tuple(sorted(WHEEL_LOCK)):
            raise ValueError("S_WHEEL_MANIFEST_INVALID")
        for wheel in self.wheels:
            if expected.get(wheel.filename) != (
                wheel.name,
                wheel.version,
                wheel.sha256,
                wheel.size_bytes,
            ):
                raise ValueError("S_WHEEL_MANIFEST_INVALID")
        return self

    @classmethod
    def from_wheelhouse(
        cls, *, manifest_path: Path, wheelhouse: Path
    ) -> ImagePreparationPlan:
        paths = {filename: wheelhouse / filename for filename in WHEEL_LOCK}
        load_verified_wheelhouse(manifest_path, paths)
        wheels: list[WheelArtifact] = []
        for filename, (name, digest, size) in sorted(WHEEL_LOCK.items()):
            version = filename.split("-")[1]
            wheels.append(
                WheelArtifact(
                    name=name,
                    version=version,
                    filename=filename,
                    sha256=digest,
                    size_bytes=size,
                    path=wheelhouse / filename,
                )
            )
        plan = cls(
            base_image=BASE_IMAGE, wheels=tuple(wheels), manifest_path=manifest_path
        )
        plan.verify_files()
        return plan

    def verify_files(self) -> dict[str, bytes]:
        """Read only the fixed five wheel names and verify size plus SHA256."""
        result: dict[str, bytes] = {}
        if {wheel.filename for wheel in self.wheels} != set(WHEEL_LOCK):
            raise RunnerError("S_WHEEL_INTEGRITY")
        for wheel in self.wheels:
            path = wheel.path
            try:
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size > MAX_WHEEL_BYTES
                ):
                    raise RunnerError("S_WHEEL_INTEGRITY")
                data = path.read_bytes()
            except OSError:
                raise RunnerError("S_WHEEL_INTEGRITY") from None
            if (
                len(data) != wheel.size_bytes
                or hashlib.sha256(data).hexdigest() != wheel.sha256
            ):
                raise RunnerError("S_WHEEL_INTEGRITY")
            result[wheel.filename] = data
        if len(result) != 5:
            raise RunnerError("S_WHEEL_INTEGRITY")
        return result

    @property
    def journal_source_digest(self) -> str:
        payload = (
            str(self.base_image)
            + "\n"
            + "\n".join(f"{wheel.filename}:{wheel.sha256}" for wheel in self.wheels)
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def journal_source_reference(self) -> str:
        return f"nebius-preparation://{self.base_image}/{self.journal_source_digest}"

    @property
    def journal_intent_metadata(self) -> dict[str, object]:
        """Retain parent, dependency identities and the fixed setup controls."""
        return {
            "base_image_uuid": str(self.base_image),
            "dependencies": [
                {
                    "filename": wheel.filename,
                    "name": wheel.name,
                    "version": wheel.version,
                    "sha256": wheel.sha256,
                    "size_bytes": wheel.size_bytes,
                }
                for wheel in self.wheels
            ],
            "preparation": {
                "networking": {"enabled": False},
                "preserve_env": False,
                "env": {},
                "disposable": False,
                "uid": 0,
                "gid": 0,
                "timeout": 300,
                "max_layer_bytes": 64 * 1024 * 1024,
                "installer_sha256": hashlib.sha256(
                    INSTALL_PROGRAM.encode()
                ).hexdigest(),
            },
        }

    def request_digest(self) -> str:
        body = {
            "image": str(self.base_image),
            "command": "/usr/local/bin/python",
            "args": ["-I", "-c", INSTALL_PROGRAM],
            "shell": False,
            "env": {},
            "preserve_env": False,
            "networking": {"enabled": False},
            "disposable": False,
            "uid": 0,
            "gid": 0,
            "cwd": "/tmp",
            "timeout": 300,
            "truncate_output_at": 4096,
            "resources_limits": {"max_layer_bytes": 64 * 1024 * 1024},
            "wheel_sha256": [wheel.sha256 for wheel in self.wheels],
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def spawn_body(self, uploaded: Mapping[str, UUID]) -> dict[str, object]:
        if set(uploaded) != {wheel.filename for wheel in self.wheels}:
            raise RunnerError("S_WHEEL_UPLOAD_BINDING")
        return {
            "image": str(self.base_image),
            "command": "/usr/local/bin/python",
            "args": ["-I", "-c", INSTALL_PROGRAM],
            "shell": False,
            "env": {},
            "preserve_env": False,
            "networking": {"enabled": False},
            "disposable": False,
            "uid": 0,
            "gid": 0,
            "cwd": "/tmp",
            "timeout": 300,
            "truncate_output_at": 4096,
            "resources_limits": {"max_layer_bytes": 64 * 1024 * 1024},
            "files": {
                f"/tmp/replayforge-wheelhouse/{wheel.filename}": {
                    "uuid": str(uploaded[wheel.filename]),
                    "uid": 0,
                    "gid": 0,
                    "mode": "0644",
                }
                for wheel in self.wheels
            },
        }


class PreparationJournal(Protocol):
    """Existing append-only image operation journal callback boundary."""

    @property
    def project_id(self) -> str: ...

    @property
    def request_digest(self) -> str: ...

    @property
    def operation_id(self) -> UUID | None: ...

    @property
    def state(self) -> str | None: ...

    def claim(self, request_digest: str) -> bool: ...
    def operation(self, operation_id: UUID, request_digest: str) -> None: ...
    def diagnostic(self, record: ImportDiagnostic) -> None: ...
    def complete(self, result: ImageImportResult) -> None: ...
    def upload_intent(self, filename: str) -> None: ...
    def upload_complete(self, filename: str, file_id: UUID, digest: str) -> None: ...
    def spawn_intent(self, uploaded: Mapping[str, UUID]) -> None: ...


class ImagePreparationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    operation_id: UUID
    image_uuid: UUID
    base_image_uuid: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SUCCESS"]
    observed_at: AwareDatetime


class NebiusImagePreparation:
    """Hash-check, upload, and retain a network-disabled offline image setup."""

    def __init__(
        self,
        config: ImageImportConfig,
        plan: ImagePreparationPlan,
        journal: PreparationJournal,
        *,
        allow_live: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        wheelhouse: Mapping[str, bytes] | None = None,
        known_operation: UUID | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        self._plan = ImagePreparationPlan.model_validate(plan.model_dump())
        self._config = ImageImportConfig.model_validate(config.model_dump())
        self._journal = journal
        self._request_hash = self._plan.request_digest()
        if (
            journal.project_id != config.project_id
            or journal.request_digest != self._request_hash
        ):
            raise RunnerError("S_PREPARATION_JOURNAL_BINDING")
        if known_operation is not None and (
            journal.operation_id != known_operation
            or journal.state != "OPERATION_KNOWN"
        ):
            raise RunnerError("S_OPERATION_NOT_OWNED")
        self._operation_id = known_operation
        self._submitted = known_operation is not None
        self._wheel_bytes: dict[str, bytes] = {}
        if known_operation is None:
            self._wheel_bytes = (
                dict(wheelhouse)
                if wheelhouse is not None
                else self._plan.verify_files()
            )
            if set(self._wheel_bytes) != {
                wheel.filename for wheel in self._plan.wheels
            }:
                raise RunnerError("S_WHEEL_INTEGRITY")
            for wheel in self._plan.wheels:
                data = self._wheel_bytes[wheel.filename]
                if (
                    len(data) != wheel.size_bytes
                    or hashlib.sha256(data).hexdigest() != wheel.sha256
                ):
                    raise RunnerError("S_WHEEL_INTEGRITY")
        self._client = httpx.AsyncClient(
            base_url=SANDBOX_URL,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Project": config.project_id,
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._closed = False

    async def __aenter__(self) -> NebiusImagePreparation:
        if self._closed:
            raise RunnerError("S_CLOSED")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._closed:
            await self._client.aclose()
            self._closed = True

    async def _json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        content: bytes | None = None,
        octet_stream: bool = False,
    ) -> tuple[int, dict[str, object]]:
        headers = {"Content-Type": "application/octet-stream"} if octet_stream else None
        try:
            async with self._client.stream(
                method, path, json=body, content=content, headers=headers
            ) as response:
                if response.status_code in {401, 403}:
                    raise RunnerError("S_AUTH")
                if 300 <= response.status_code < 400:
                    raise RunnerError("S_REDIRECT_DENIED")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise RunnerError("S_RESPONSE_TOO_LARGE")
                    raw.extend(chunk)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise RunnerError("S_SCHEMA_INVALID")
                try:
                    decoded = json.loads(raw) if raw else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    decoded = None
                return response.status_code, decoded if isinstance(
                    decoded, dict
                ) else {}
        except httpx.TimeoutException:
            raise RunnerError("S_TIMEOUT") from None
        except httpx.HTTPError:
            raise RunnerError("S_UNAVAILABLE") from None

    def _diagnostic(
        self,
        *,
        phase: Literal["submit", "poll"],
        error: BaseException,
        status: int | None = None,
        operation_id: UUID | None = None,
    ) -> None:
        outcome: Literal["SUBMISSION_UNKNOWN", "PROVIDER_REJECTED", "POLL_ERROR"] = (
            "POLL_ERROR"
            if phase == "poll"
            else (
                "PROVIDER_REJECTED"
                if status is not None
                and 400 <= status < 500
                and status not in {408, 429}
                else "SUBMISSION_UNKNOWN"
            )
        )
        path = (
            "/sandboxes/v1/instances"
            if phase == "submit"
            else f"/sandboxes/v1/operations/{operation_id}"
        )
        record = ImportDiagnostic(
            outcome=outcome,
            observed_at=datetime.now(UTC),
            method="POST" if phase == "submit" else "GET",
            path=path,
            http_status=status,
            operation_id=operation_id,
            request_sha256=self._request_hash,
            sdk_version="contree-sdk-0.3.6",
            error_class="ApiTimeoutError"
            if str(error) == "S_TIMEOUT"
            else "ContreeTransportError",
        )
        self._journal.diagnostic(record)

    async def _upload_wheels(self) -> dict[str, UUID]:
        uploaded: dict[str, UUID] = {}
        for wheel in self._plan.wheels:
            data = self._wheel_bytes[wheel.filename]
            self._journal.upload_intent(wheel.filename)
            status: int | None = None
            try:
                status, result = await self._json(
                    "POST", "v1/files", content=data, octet_stream=True
                )
                if status not in {200, 201}:
                    raise RunnerError("S_UNAVAILABLE")
                file_id = UUID(str(result["uuid"]))
                digest = str(result["sha256"]).removeprefix("sha256:")
            except (RunnerError, KeyError, ValueError, TypeError) as error:
                self._diagnostic(phase="submit", error=error, status=status)
                raise RunnerError("S_SUBMISSION_UNKNOWN") from None
            if digest != wheel.sha256:
                self._diagnostic(
                    phase="submit", error=RunnerError("S_SCHEMA_INVALID"), status=502
                )
                raise RunnerError("S_FILE_INTEGRITY")
            self._journal.upload_complete(wheel.filename, file_id, digest)
            uploaded[wheel.filename] = file_id
        return uploaded

    async def prepare(
        self,
        *,
        max_polls: int = 20,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 1,
    ) -> ImagePreparationResult:
        if self._closed or self._submitted:
            raise RunnerError("S_CLOSED_OR_ALREADY_SUBMITTED")
        if (
            not 1 <= max_polls <= MAX_POLLS
            or not 1 <= timeout_seconds <= MAX_WAIT_SECONDS
            or not 0 <= poll_interval_seconds <= 5
        ):
            raise RunnerError("S_POLICY_DENIED")
        self._plan.verify_files()
        if (
            self._journal.state is not None
            or self._journal.claim(self._request_hash) is not True
        ):
            raise RunnerError("S_PREPARATION_ALREADY_CLAIMED")
        self._submitted = True
        uploaded = await self._upload_wheels()
        self._journal.spawn_intent(uploaded)
        await self._submit(uploaded)
        return await self._poll(
            max_polls=max_polls,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    async def _submit(self, uploaded: Mapping[str, UUID]) -> None:
        if self._submitted is False or self._operation_id is not None:
            raise RunnerError("S_DUPLICATE_SUBMISSION")
        try:
            status, data = await self._json(
                "POST", "v1/instances", body=self._plan.spawn_body(uploaded)
            )
            if status != 201:
                self._diagnostic(
                    phase="submit", error=RunnerError("S_UNAVAILABLE"), status=status
                )
                raise RunnerError("S_SUBMISSION_UNKNOWN")
            operation_id = UUID(str(data["uuid"]))
        except (RunnerError, KeyError, ValueError, TypeError) as error:
            if str(error) != "S_SUBMISSION_UNKNOWN":
                self._diagnostic(phase="submit", error=error)
            raise RunnerError("S_SUBMISSION_UNKNOWN") from None
        self._operation_id = operation_id
        try:
            self._journal.operation(operation_id, self._request_hash)
        except Exception:
            raise RunnerError("S_OPERATION_RETENTION_FAILED") from None

    async def recover(
        self,
        *,
        max_polls: int = 20,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 1,
    ) -> ImagePreparationResult:
        if self._closed or self._operation_id is None:
            raise RunnerError("S_OPERATION_NOT_OWNED")
        return await self._poll(
            max_polls=max_polls,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    async def _poll(
        self, *, max_polls: int, timeout_seconds: int, poll_interval_seconds: float
    ) -> ImagePreparationResult:
        if self._operation_id is None:
            raise RunnerError("S_OPERATION_NOT_OWNED")
        deadline = time.monotonic() + timeout_seconds
        for index in range(max_polls):
            try:
                status, data = await self._json(
                    "GET", f"v1/operations/{self._operation_id}"
                )
            except RunnerError as error:
                self._diagnostic(
                    phase="poll", error=error, operation_id=self._operation_id
                )
                raise
            if (
                status != 200
                or data.get("uuid") != str(self._operation_id)
                or data.get("kind") != "instance"
            ):
                raise RunnerError("S_CORRELATION_FAILED")
            if data.get("image_uuid") != str(self._plan.base_image):
                raise RunnerError("S_CORRELATION_FAILED")
            state = data.get("status")
            if state in {"SUCCESS", "FAILED", "CANCELLED"}:
                image_id = data.get("result_image_uuid")
                metadata = data.get("metadata")
                guest_result = (
                    metadata.get("result") if isinstance(metadata, dict) else None
                )
                try:
                    process = ProcessResult.model_validate(guest_result)
                    stdout = process.stdout
                    output = (
                        base64.b64decode(stdout.value, validate=True)
                        if stdout.encoding == "base64"
                        else stdout.value.encode("utf-8")
                    ).decode("utf-8")
                    process_ok = (
                        process.state.exit_code == 0
                        and process.state.timed_out is False
                        and stdout.truncated is False
                        and process.stderr.truncated is False
                        and output == "OFFLINE_INSTALL_OK\n"
                    )
                except (ValidationError, ValueError, UnicodeDecodeError):
                    process_ok = False
                succeeded = state == "SUCCESS" and process_ok
                if not succeeded or image_id is None:
                    failure = ImageImportResult(
                        operation_id=self._operation_id,
                        request_sha256=self._request_hash,
                        status="FAILED" if state != "CANCELLED" else "CANCELLED",
                        image_uuid=None,
                        observed_at=datetime.now(UTC),
                    )
                    self._journal.complete(failure)
                    if succeeded and image_id is None:
                        raise RunnerError("S_DERIVED_IMAGE_MISSING")
                    raise RunnerError("S_PREPARATION_FAILED")
                try:
                    derived = UUID(str(image_id))
                except (ValueError, TypeError):
                    raise RunnerError("S_SCHEMA_INVALID") from None
                result = ImageImportResult(
                    operation_id=self._operation_id,
                    request_sha256=self._request_hash,
                    status="SUCCESS",
                    image_uuid=derived,
                    observed_at=datetime.now(UTC),
                )
                self._journal.complete(result)
                return ImagePreparationResult(
                    operation_id=self._operation_id,
                    image_uuid=derived,
                    base_image_uuid=self._plan.base_image,
                    request_sha256=self._request_hash,
                    status="SUCCESS",
                    observed_at=result.observed_at,
                )
            if state not in {"PENDING", "ASSIGNED", "EXECUTING"}:
                raise RunnerError("S_SCHEMA_INVALID")
            if index + 1 < max_polls and time.monotonic() < deadline:
                await asyncio.sleep(
                    min(poll_interval_seconds, max(deadline - time.monotonic(), 0))
                )
        raise RunnerError("S_MATERIALIZATION_PENDING")
