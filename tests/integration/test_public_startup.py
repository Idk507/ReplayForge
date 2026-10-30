"""Real public startup against a disposable TLS PostgreSQL database."""

import os

import pytest
from fastapi.testclient import TestClient

from services.auth.local import create_public_from_env


def test_public_startup_roles_readiness_and_anonymous_denial(monkeypatch):
    api = os.environ.get("REPLAYFORGE_TEST_PUBLIC_API_DSN")
    auth = os.environ.get("REPLAYFORGE_TEST_PUBLIC_AUTH_DSN")
    if not api or not auth:
        pytest.skip("requires separately provisioned disposable TLS database roles")
    config = {
        "REPLAYFORGE_DEPLOYMENT_MODE": "PUBLIC_DEPLOYMENT",
        "REPLAYFORGE_AUTH_ENABLE_GITHUB": "true",
        "REPLAYFORGE_PRIVATE_DATABASE_HOST": "127.0.0.1.nip.io",
        "REPLAYFORGE_DATABASE_URL": api,
        "REPLAYFORGE_AUTH_DATABASE_URL": auth,
        "REPLAYFORGE_AUTH_API_ORIGIN": "https://demo.example.com",
        "REPLAYFORGE_AUTH_WEB_ORIGIN": "https://demo.example.com",
        "GITHUB_CLIENT_ID": "Iv1.synthetic",
        "GITHUB_CLIENT_SECRET": "synthetic-client-secret",
        "REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX": "11" * 32,
        "REPLAYFORGE_AUTH_HMAC_KEY_HEX": "22" * 32,
    }
    for name, value in config.items():
        monkeypatch.setenv(name, value)
    app = create_public_from_env()
    browser = TestClient(
        app, base_url="https://demo.example.com", client=("127.0.0.1", 50000)
    )
    assert browser.get("/health/ready").json() == {"status": "ready"}
    assert browser.get("/api/v1/auth/me").status_code == 401
    assert browser.get("/api/v1/runs").status_code == 401
    assert (
        browser.get("/health/ready", headers={"host": "evil.example"}).status_code
        == 403
    )
