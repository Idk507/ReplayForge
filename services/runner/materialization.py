"""Owned remote source upload and measured saved workspace, never code execution.

Only the fixed application-owned extractor runs remotely. Source/test bytes are
data. Durable admission and operation retention callbacks are mandatory; neither
an upload nor its unsigned measurement establishes runner capabilities.
"""

import base64
import io
import tarfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Self
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

from packages.contracts.run import ArtifactID, CommitSHA
from packages.contracts.sandbox import RunnerError
from packages.contracts.source import SourceManifest
from packages.policies.repository import safe_tree_path
from services.orchestrator.reproduction import ReproductionArtifact
from services.runner.receipts import canonical
from services.runner.remote import NebiusSandboxBackend, SandboxConfig
from services.runner.responses import Created, Operation, ProcessState
from services.runner.source_archive import build_source_archive
from services.runner.workspace import WorkspaceMeasurement, archive_identity

# Fixed program, passed as literal argv with Python isolation, never host exec.
# Do not import any repository module or parse package installation instructions.
EXTRACT_PROGRAM = r"""import io, os, pathlib, sys, tarfile
assert os.getuid() == 1000 and os.getgid() == 1000
root = pathlib.Path('/workspace/repo')
assert root.resolve() == root and not root.is_symlink() and not any(root.iterdir())
raw = sys.stdin.buffer.read(65537)
assert 0 < len(raw) <= 65536
os.umask(0o022)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
    entries = archive.getmembers()
    assert 0 < len(entries) <= 128
    for entry in entries:
        p = pathlib.PurePosixPath(entry.name)
        assert not p.is_absolute() and all(v not in ('', '.', '..') for v in p.parts)
        assert entry.isdir() or entry.isfile()
        assert entry.mode == (0o755 if entry.isdir() else 0o644)
        target = root.joinpath(*p.parts)
        if entry.isdir():
            target.mkdir(mode=0o755, exist_ok=False)
        else:
            source = archive.extractfile(entry)
            assert source is not None
            body = source.read(65537)
            assert len(body) == entry.size and len(body) <= 65536
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open(target, flags, 0o644)
            with os.fdopen(fd, 'wb') as output:
                output.write(body)
"""


class MaterializationPlan(BaseModel):
    """Immutable metadata suitable for a privileged durable intent journal."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    repository_id: UUID
    source_commit: CommitSHA
    source_manifest_ref: ArtifactID
    reproduction_test_ref: ArtifactID
    image_uuid: UUID
    archive_ref: ArtifactID
    workspace_ref: ArtifactID
    paths: tuple[str, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_paths(self) -> Self:
        if (
            self.paths != tuple(sorted(self.paths))
            or len({p.casefold() for p in self.paths}) != len(self.paths)
            or any(not safe_tree_path(p) for p in self.paths)
        ):
            raise ValueError("MATERIALIZATION_PATH_DENIED")
        return self


class MaterializationUpload(BaseModel):
    """Private source bytes; never serialize into public events or model memory."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    plan: MaterializationPlan
    archive: bytes = Field(min_length=1, max_length=65536, repr=False)


class MaterializationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    plan: MaterializationPlan
    operation_id: UUID
    result_image_uuid: UUID
    measurement: WorkspaceMeasurement
    observed_at: AwareDatetime
    can_authorize_execution: Literal[False] = False


def prepare_upload(
    image_uuid: UUID,
    manifest: SourceManifest,
    files: Mapping[str, bytes],
    reproduction: ReproductionArtifact,
) -> MaterializationUpload:
    """Pack original manifest bytes plus one separately bound new reproduction.

    Existing source/test files cannot be overwritten by the proposed test. The
    input remains a subset unless completeness was established independently.
    No filesystem access, extraction or repository code execution occurs here.
    """
    manifest = SourceManifest.model_validate(manifest.model_dump())
    reproduction = ReproductionArtifact.model_validate(reproduction.model_dump())
    original = build_source_archive(manifest, files)
    path = reproduction.proposal.target_path
    if (
        reproduction.source_manifest_ref
        != "sha256:" + sha256(canonical(manifest)).hexdigest()
        or reproduction.repository_id != manifest.repository_id
        or reproduction.source_commit != manifest.source_commit
        or not safe_tree_path(path)
        or path.casefold() in {name.casefold() for name in files}
    ):
        raise RunnerError("S_MATERIALIZATION_SOURCE_DENIED")
    body = reproduction.proposal.test_source.encode("utf-8")
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(original), mode="r:") as source:
        existing = {m.name for m in source.getmembers()}
        parts = path.split("/")
        directories = {"/".join(parts[:i]) for i in range(1, len(parts))} - existing
        with tarfile.open(
            fileobj=output, mode="w", format=tarfile.USTAR_FORMAT
        ) as target:
            members = {m.name: m for m in source.getmembers()}
            for name in sorted(existing | directories | {path}):
                if name in members:
                    member = members[name]
                    target.addfile(
                        member, source.extractfile(member) if member.isfile() else None
                    )
                else:
                    member = tarfile.TarInfo(name)
                    try:
                        member.tobuf(format=tarfile.USTAR_FORMAT)
                    except (ValueError, UnicodeError):
                        raise RunnerError("S_MATERIALIZATION_PATH_DENIED") from None
                    member.uid = member.gid = 1000
                    if name == path:
                        member.mode, member.size = 0o644, len(body)
                        target.addfile(member, io.BytesIO(body))
                    else:
                        member.type, member.mode = tarfile.DIRTYPE, 0o755
                        target.addfile(member)
    archive = output.getvalue()
    workspace, _, _ = archive_identity(archive)
    return MaterializationUpload(
        plan=MaterializationPlan(
            run_id=reproduction.run_id,
            repository_id=manifest.repository_id,
            source_commit=manifest.source_commit,
            source_manifest_ref=reproduction.source_manifest_ref,
            reproduction_test_ref=reproduction.artifact_ref(),
            image_uuid=image_uuid,
            archive_ref="sha256:" + sha256(archive).hexdigest(),
            workspace_ref=workspace,
            paths=tuple(sorted((*files, path))),
        ),
        archive=archive,
    )


class NebiusMaterializer:
    """One-shot upload with an owned operation and independent archive readback."""

    def __init__(
        self,
        config: SandboxConfig,
        *,
        allow_live: bool = False,
        transport: httpx.BaseTransport | None = None,
        known_operation: UUID | None = None,
    ) -> None:
        if transport is None and allow_live is not True:
            raise RunnerError("S_LIVE_NOT_AUTHORIZED")
        if known_operation is not None and not isinstance(known_operation, UUID):
            raise RunnerError("S_OPERATION_NOT_OWNED")
        self._http = NebiusSandboxBackend(config, transport=transport)
        self._operation = known_operation
        self._submitted = known_operation is not None
        self._closed = False
        self.origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"] = (
            "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
        )

    def __enter__(self) -> Self:
        if self._closed:
            raise RunnerError("S_CLOSED")
        return self

    def __exit__(self, *_: object) -> None:
        self._http.close()
        self._closed = True

    def submit(
        self,
        upload: MaterializationUpload,
        claim: Callable[[MaterializationPlan], bool],
        retain_operation: Callable[[UUID], None],
    ) -> UUID:
        """Commit a fresh durable claim before POST; never repeat unknown work.

        Callbacks are privileged deployment dependencies. claim must commit its
        intent/budget/current consent+fences and return true only once. Retention
        must bind the returned operation before this method returns it. No caller
        JSON or model tool can provide either callback as execution authority.
        """
        if self._closed or self._submitted:
            raise RunnerError("S_CLOSED_OR_ALREADY_SUBMITTED")
        upload = MaterializationUpload.model_validate(upload.model_dump())
        plan = upload.plan
        if (
            plan.image_uuid != self._http.config.image_uuid
            or plan.archive_ref != "sha256:" + sha256(upload.archive).hexdigest()
            or plan.workspace_ref != archive_identity(upload.archive)[0]
        ):
            raise RunnerError("S_MATERIALIZATION_SOURCE_DENIED")
        if claim(plan) is not True:
            raise RunnerError("S_MATERIALIZATION_ALREADY_CLAIMED")
        self._submitted = True
        payload: dict[str, object] = {
            "image": str(plan.image_uuid),
            "command": "/usr/local/bin/python",
            "args": ["-I", "-S", "-c", EXTRACT_PROGRAM],
            "shell": False,
            "env": {},
            "preserve_env": False,
            "networking": {"enabled": False},
            "disposable": False,
            "uid": 1000,
            "gid": 1000,
            "cwd": "/workspace/repo",
            "timeout": 15,
            "truncate_output_at": 1024,
            "resources_limits": {"max_layer_bytes": 1024 * 1024},
            "stdin": {
                "value": base64.b64encode(upload.archive).decode(),
                "encoding": "base64",
                "close": True,
            },
        }
        try:
            response = self._http._request("POST", "v1/instances", 201, payload)
            self._operation = Created.model_validate_json(response).uuid
        except (RunnerError, ValidationError):
            raise RunnerError("S_MATERIALIZATION_SUBMISSION_UNKNOWN") from None
        retain_operation(self._operation)
        return self._operation

    def collect(
        self,
        plan: MaterializationPlan,
        operation_id: UUID,
        authorize: Callable[[], None],
    ) -> MaterializationResult:
        """Known-operation GETs only; no signatures or capability claims.

        Restoring known_operation is allowed only from a trusted durable journal,
        never caller-selected provider IDs. Closing the client is not deletion.
        """
        if self._closed or operation_id != self._operation:
            raise RunnerError("S_OPERATION_NOT_OWNED")
        plan = MaterializationPlan.model_validate(plan.model_dump())
        if plan.image_uuid != self._http.config.image_uuid:
            raise RunnerError("S_MATERIALIZATION_SOURCE_DENIED")
        authorize()
        try:
            operation = Operation.model_validate_json(
                self._http._request("GET", f"v1/operations/{operation_id}", 200)
            )
            if (
                operation.uuid != operation_id
                or operation.image_uuid != plan.image_uuid
            ):
                raise RunnerError("S_CORRELATION_FAILED")
            if operation.status not in {"SUCCESS", "FAILED", "CANCELLED"}:
                raise RunnerError("S_MATERIALIZATION_PENDING")
            process = operation.metadata.get("result")
            state = ProcessState.model_validate(
                process.get("state") if isinstance(process, dict) else None
            )
        except ValidationError:
            raise RunnerError("S_SCHEMA_INVALID") from None
        image = operation.result_image_uuid
        if (
            operation.status != "SUCCESS"
            or state.exit_code != 0
            or state.timed_out
            or state.signal not in {None, -1}
            or image is None
            or image == plan.image_uuid
        ):
            raise RunnerError("S_MATERIALIZATION_FAILED")
        archive = self._http._request(
            "GET", f"v1/inspect/{image}/archive?path=/workspace/repo", 200
        )
        reference, count, size = archive_identity(archive)
        if reference != plan.workspace_ref:
            raise RunnerError("S_MATERIALIZATION_MEASUREMENT_MISMATCH")
        authorize()
        return MaterializationResult(
            plan=plan,
            operation_id=operation_id,
            result_image_uuid=image,
            measurement=WorkspaceMeasurement(
                image_uuid=image,
                tree_ref=reference,
                files=count,
                bytes_measured=size,
                evidence_origin=self.origin,
            ),
            observed_at=datetime.now(UTC),
        )
