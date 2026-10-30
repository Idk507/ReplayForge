"""The ingress configuration is limited to one trusted HTTPS hostname."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.render_public_proxy import public_host, render


@pytest.mark.parametrize(
    "origin",
    [
        "http://demo.example.com",
        "https://*.example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://demo.example.com/path",
        "https://demo.example.com:444",
        "https://demo.example.com;return 200",
    ],
)
def test_reject_unsafe_public_origin(origin):
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN_INVALID"):
        public_host(origin)


def test_render_exact_host_and_routes():
    template = Path("deploy/nginx.conf.template").read_text(encoding="utf-8")
    config = render("https://demo.example.com", template)
    assert "__PUBLIC_HOST__" not in config
    assert "server_name demo.example.com;" in config
    assert 'if ($http_x_forwarded_proto != "https")' in config
    assert "location = /api/v1/auth/github/callback" in config
    assert "access_log off;" in config


@pytest.mark.parametrize(
    "origin",
    [
        "http://demo.example.com",
        "https://*.example.com",
        "https://demo.example.com\n;evil",
    ],
)
def test_proxy_start_rejects_unsafe_origin_before_launch(origin):
    shell = shutil.which("bash")
    if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").is_file():
        shell = "C:/Program Files/Git/bin/bash.exe"
    if shell is None:
        pytest.skip("requires bash to exercise proxy startup")
    env = os.environ | {
        "REPLAYFORGE_DEPLOYMENT_MODE": "PUBLIC_DEPLOYMENT",
        "REPLAYFORGE_WEB_AUTH_MODE": "github",
        "REPLAYFORGE_PUBLIC_ORIGIN": origin,
        "REPLAYFORGE_AUTH_API_ORIGIN": origin,
        "REPLAYFORGE_AUTH_WEB_ORIGIN": origin,
    }
    result = subprocess.run(
        [shell, "deploy/start-proxy.sh"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert "PUBLIC_ORIGIN_INVALID" in result.stderr
