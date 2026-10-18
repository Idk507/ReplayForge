"""Fixed-fixture contingency runner, separate from repository dispatch authority."""

import hashlib
import json
import time
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.repair import ReproductionProposal
from packages.contracts.sandbox import RunnerError
from services.runner.fixture_guest import GUEST_PROGRAM
from services.runner.patches import _paths
from services.runner.remote import NebiusSandboxBackend, SandboxConfig, probe_request
from services.runner.responses import Created, ProcessResult
from services.runner.smoke import _decode, _wait

SOURCE_HASH = "74d09f7d6afe6a413d3548b28fa35ad3f7596fdf4f383c04a3d614a7d686bc26"


def fixture_source() -> str:
    """Only the repository-owned original synthetic fixture can be uploaded."""
    path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks/fixtures/checkout_retry/app/checkout.py"
    )
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != SOURCE_HASH:
        raise RunnerError("FIXTURE_SOURCE_CHANGED")
    return body.decode("utf-8")


class FixtureObservation(BaseModel):
    """Guest claims for benchmark inspection; never a signed VERIFIED verdict."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    status: str
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    test_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exit_code: int = Field(strict=True)
    behavioral_failure: bool = Field(strict=True)
    setup_error: bool = Field(strict=True)
    tests_ran: bool = Field(strict=True)
    uid: int = Field(strict=True)
    interfaces: list[str]
    credential_env_present: bool = Field(strict=True)


class CheckoutBenchmarkBackend(NebiusSandboxBackend):
    """No repository URLs, host paths, arbitrary argv or source parameters."""

    def start_checkout(self, proposal: ReproductionProposal, patch: str = "") -> UUID:
        proposal = ReproductionProposal.model_validate(proposal.model_dump())
        if patch and _paths(patch)[0] != ("app/checkout.py",):
            raise RunnerError("FIXTURE_PATCH_PATH_DENIED")
        if self._submitted:
            raise RunnerError("S_DUPLICATE_SUBMISSION")
        data = {
            "source": fixture_source(),
            "test": proposal.test_source,
            "patch": patch,
        }
        if len(json.dumps(data).encode()) > 24000:
            raise RunnerError("FIXTURE_LIMIT")
        payload = probe_request(self.config.image_uuid)
        payload.update(
            args=["-I", "-S", "-c", GUEST_PROGRAM, json.dumps(data)], timeout=60
        )
        # Fixed guest installs per-process CPU/address-space/process/file caps.
        # This is fixture-only evidence, not an attestation of provider hard caps.
        self._submitted = True
        try:
            self._owned = Created.model_validate_json(
                self._request("POST", "v1/instances", 201, payload)
            ).uuid
        except (RunnerError, ValidationError):
            raise RunnerError("S_SUBMISSION_UNKNOWN") from None
        return self._owned


def run_checkout(
    config: SandboxConfig, proposal: ReproductionProposal, patch: str = ""
) -> dict[str, object]:
    """One disposable remote operation; only approved synthetic fixture inputs."""
    backend = CheckoutBenchmarkBackend(config)
    operation_id = None
    terminal = False
    try:
        operation_id = backend.start_checkout(proposal, patch)
        operation = _wait(
            backend,
            operation_id,
            polls=55,
            deadline=time.monotonic() + 75,
            sleep=time.sleep,
        )
        terminal = True
        if (
            operation.status != "SUCCESS"
            or operation.result_image_uuid is not None
            or operation.metadata.get("networking") != {"enabled": False}
        ):
            raise RunnerError("FIXTURE_REMOTE_FAILED")
        result = ProcessResult.model_validate(operation.metadata.get("result"))
        if (
            result.state.exit_code != 0
            or result.state.timed_out
            or result.state.signal not in (None, -1)
        ):
            raise RunnerError("FIXTURE_GUEST_FAILED")
        observation = FixtureObservation.model_validate_json(_decode(result.stdout))
        if (
            observation.status != "EXECUTED"
            or observation.uid != 1000
            or observation.interfaces != ["lo"]
            or observation.credential_env_present
            or observation.test_sha256 != proposal.test_source_sha256()
        ):
            raise RunnerError("FIXTURE_BINDING_DENIED")
        return {
            "operation_id": str(operation_id),
            "image_uuid": str(config.image_uuid),
            "compute_terminal_confirmed": terminal,
            **observation.model_dump(),
        }
    finally:
        if operation_id is not None and not terminal:
            try:
                backend.terminate(operation_id)
                _wait(
                    backend,
                    operation_id,
                    polls=5,
                    deadline=time.monotonic() + 10,
                    sleep=time.sleep,
                )
            finally:
                backend.close()
        else:
            backend.close()
