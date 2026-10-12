"""Real Nebius REST client for fixed synthetic smoke work only.

No SDK defaults, arbitrary commands, local execution or credentials in the
guest. This adapter is not enabled for untrusted repository workloads.
"""

import os
import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from packages.contracts.sandbox import RunnerError
from services.runner.responses import Created, Operation

SANDBOX_URL = "https://api.tokenfactory.nebius.com/sandboxes/"
MAX_RESPONSE_BYTES = 65536

# Trusted application-owned template. This string is sent only to remote guest
# Python via literal argv with shell=false. No caller text is interpolated.
SMOKE_PROGRAM = """import json, os, pathlib, subprocess, sys
version = subprocess.check_output([sys.executable, '-V'], text=True).strip()
print(json.dumps({
    'python_version': version,
    'uid': os.getuid(), 'gid': os.getgid(),
    'interfaces': sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()),
    'docker_socket_present': any(pathlib.Path(p).exists() for p in
        ('/var/run/docker.sock', '/run/docker.sock')),
    'credential_env_present': any(k in os.environ for k in
        ('NEBIUS_API_KEY', 'NEBIUS_PROJECT_ID', 'GITHUB_TOKEN', 'TAVILY_API_KEY'))
}))
"""


class SandboxConfigError(Exception):
    """Configuration error with a stable safe code."""


class SandboxConfig(BaseModel):
    """Environment-only secrets and a pinned provider image UUID."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr
    project_id: str
    image_uuid: UUID
    base_url: str = SANDBOX_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SandboxConfig":
        """Validate destinations/headers before sending credentials anywhere."""
        values = os.environ if env is None else env
        key = values.get("NEBIUS_API_KEY", "")
        project = values.get("NEBIUS_PROJECT_ID", "")
        image = values.get("SANDBOX_IMAGE_UUID", "")
        base = values.get("SANDBOX_REMOTE_URL", SANDBOX_URL)
        if not key or not project or not image:
            raise SandboxConfigError("S_CONFIG_MISSING")
        try:
            url = urlsplit(base)
            valid_url = (
                url.scheme == "https"
                and url.hostname == "api.tokenfactory.nebius.com"
                and url.port in (None, 443)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and url.path in ("/sandboxes", "/sandboxes/")
            )
            image_uuid = UUID(image)
        except ValueError:
            raise SandboxConfigError("S_CONFIG_INVALID") from None
        if (
            not valid_url
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", project)
            or not re.fullmatch(r"[\x21-\x7e]{1,4096}", key)
            or len(base) > 128
        ):
            raise SandboxConfigError("S_CONFIG_INVALID")
        return cls(
            api_key=SecretStr(key),
            project_id=project,
            image_uuid=image_uuid,
            base_url=SANDBOX_URL,
        )


def probe_request(image_uuid: UUID) -> dict[str, object]:
    """A deny-network, non-root, disposable request with no inherited env."""
    return {
        "image": str(image_uuid),
        "command": "/usr/local/bin/python",
        "args": ["-I", "-S", "-c", SMOKE_PROGRAM],
        "shell": False,
        "env": {},
        "preserve_env": False,
        "networking": {"enabled": False},
        "disposable": True,
        "uid": 1000,
        "gid": 1000,
        "cwd": "/tmp",
        "timeout": 15,
        "truncate_output_at": 4096,
        "resources_limits": {"max_layer_bytes": 64 * 1024 * 1024},
    }


class NebiusSandboxBackend:
    """Explicitly closed HTTP client; at most one POST per owned probe client."""

    def __init__(
        self, config: SandboxConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        # Revalidate direct model construction before attaching credentials.
        self.config = SandboxConfig.from_env(
            {
                "NEBIUS_API_KEY": config.api_key.get_secret_value(),
                "NEBIUS_PROJECT_ID": config.project_id,
                "SANDBOX_IMAGE_UUID": str(config.image_uuid),
                "SANDBOX_REMOTE_URL": config.base_url,
            }
        )
        self._submitted = False
        self._owned: UUID | None = None
        self._client = httpx.Client(
            base_url=self.config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Project": config.project_id,
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(5, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        """Release local HTTP resources; this does not terminate remote work."""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        expected: int,
        payload: dict[str, object] | None = None,
    ) -> bytes:
        """Bound data and sanitize HTTP/transport failures; never retry writes."""
        deadline = time.monotonic() + 5
        try:
            with self._client.stream(method, path, json=payload) as response:
                status = response.status_code
                if status in (401, 403):
                    raise RunnerError("S_AUTH")
                if 300 <= status < 400:
                    raise RunnerError("S_REDIRECT_DENIED")
                if status != expected:
                    raise RunnerError("S_UNAVAILABLE")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise RunnerError("S_SCHEMA_INVALID")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise RunnerError("S_TIMEOUT")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise RunnerError("S_RESPONSE_TOO_LARGE")
                    body.extend(chunk)
                return bytes(body)
        except httpx.TimeoutException:
            raise RunnerError("S_TIMEOUT") from None
        except httpx.HTTPError:
            raise RunnerError("S_UNAVAILABLE") from None

    def start_probe(self) -> UUID:
        """Submit once, preserving an unknown outcome rather than duplicating."""
        if self._submitted:
            raise RunnerError("S_DUPLICATE_SUBMISSION")
        self._submitted = True
        try:
            body = self._request(
                "POST", "v1/instances", 201, probe_request(self.config.image_uuid)
            )
            self._owned = Created.model_validate_json(body).uuid
        except (RunnerError, ValidationError) as error:
            if isinstance(error, RunnerError) and str(error) in {
                "S_AUTH",
                "S_REDIRECT_DENIED",
            }:
                raise
            raise RunnerError("S_SUBMISSION_UNKNOWN") from None
        return self._owned

    def _check_owned(self, operation_id: UUID) -> None:
        """Prevent cancellation or reads for unrelated operations."""
        if self._owned is None or operation_id != self._owned:
            raise RunnerError("S_POLICY_DENIED")

    def inspect(self, operation_id: UUID) -> Operation:
        """Ignore provider URLs; construct only the validated owned UUID path."""
        self._check_owned(operation_id)
        try:
            operation = Operation.model_validate_json(
                self._request("GET", f"v1/operations/{operation_id}", 200)
            )
        except ValidationError:
            raise RunnerError("S_SCHEMA_INVALID") from None
        if (
            operation.uuid != operation_id
            or operation.image_uuid != self.config.image_uuid
        ):
            raise RunnerError("S_CORRELATION_FAILED")
        return operation

    def terminate(self, operation_id: UUID) -> None:
        """Cancellation acceptance is not terminal-state confirmation."""
        self._check_owned(operation_id)
        self._request("DELETE", f"v1/operations/{operation_id}", 202)
