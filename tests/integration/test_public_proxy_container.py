"""Optional isolated NGINX image acceptance; it never launches the workbench."""

import os
import subprocess
import time
from uuid import uuid4

import pytest


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=30, check=check
    )


def test_proxy_container_enforces_host_and_protocol():
    image = os.environ.get("REPLAYFORGE_TEST_PROXY_IMAGE")
    if image != "replayforge-local-proxy:dev":
        pytest.skip("requires explicitly selected task-owned local proxy image")
    name = "rf-proxy-test-" + uuid4().hex
    origin = "https://demo.example.com"
    env = {
        "REPLAYFORGE_DEPLOYMENT_MODE": "PUBLIC_DEPLOYMENT",
        "REPLAYFORGE_WEB_AUTH_MODE": "github",
        "REPLAYFORGE_PUBLIC_ORIGIN": origin,
        "REPLAYFORGE_AUTH_API_ORIGIN": origin,
        "REPLAYFORGE_AUTH_WEB_ORIGIN": origin,
    }
    args = ["run", "-d", "--network", "none", "--name", name]
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    args.append(image)
    container = docker(*args).stdout.strip()
    try:

        def request(host: str, proto: str) -> str:
            result = docker(
                "exec",
                container,
                "curl",
                "--max-time",
                "3",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                f"Host: {host}",
                "-H",
                f"X-Forwarded-Proto: {proto}",
                "http://127.0.0.1:8080/",
                check=False,
            )
            return result.stdout.strip()

        status = ""
        for _ in range(20):
            status = request("demo.example.com", "https")
            if status != "000":
                break
            time.sleep(0.2)
        # No Next.js container is present; 502 proves the trusted route was selected.
        assert status == "502"
        assert request("evil.example.com", "https") == "421"
        assert request("demo.example.com", "http") == "403"
        assert request("demo.example.com", "https,http") == "403"
        assert (
            docker("inspect", "--format", "{{.Config.User}}", container).stdout.strip()
            == "nginx"
        )
        docker("stop", "--time", "5", container)
        assert (
            docker(
                "inspect",
                "--format",
                "{{.State.Status}} {{.State.ExitCode}}",
                container,
            ).stdout.strip()
            == "exited 0"
        )
        docker("start", container)
        status = "000"
        for _ in range(20):
            status = request("demo.example.com", "https")
            if status != "000":
                break
            time.sleep(0.2)
        assert status == "502"
    finally:
        state = docker(
            "inspect", "--format", "{{.State.Status}}", container, check=False
        )
        if state.returncode == 0 and state.stdout.strip() == "running":
            docker("stop", "--time", "5", container)
        docker("rm", container, check=False)
