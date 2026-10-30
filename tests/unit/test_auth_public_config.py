"""Public startup configuration uses separate roles and authenticated TLS."""

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.auth import local
from services.auth.github import AuthDenied
from services.auth.local import AuthLocalConfig


def public_values():
    return {
        "REPLAYFORGE_DEPLOYMENT_MODE": "PUBLIC_DEPLOYMENT",
        "REPLAYFORGE_AUTH_ENABLE_GITHUB": "true",
        "REPLAYFORGE_PRIVATE_DATABASE_HOST": "database.example.internal",
        "REPLAYFORGE_DATABASE_URL": "postgresql://worker:secret@database.example.internal/demo?sslmode=verify-full",
        "REPLAYFORGE_AUTH_DATABASE_URL": "postgresql://auth:secret@database.example.internal/demo?sslmode=verify-full",
        "REPLAYFORGE_AUTH_API_ORIGIN": "https://demo.example.com",
        "REPLAYFORGE_AUTH_WEB_ORIGIN": "https://demo.example.com",
        "GITHUB_CLIENT_ID": "Iv1.synthetic",
        "GITHUB_CLIENT_SECRET": "synthetic-client-secret",
        "REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX": "11" * 32,
        "REPLAYFORGE_AUTH_HMAC_KEY_HEX": "22" * 32,
    }


def test_public_config_requires_explicit_https_and_separate_roles():
    config = AuthLocalConfig.from_env(public_values(), public=True)
    assert not config.settings.development
    assert config.settings.session_cookie == "__Host-rf_session"
    assert config.allow_live
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "fault",
    [
        "mode",
        "tls",
        "host",
        "role",
        "origin",
        "publication",
        "collection",
        "github",
        "hostaddr",
        "ip",
    ],
)
def test_public_config_fails_closed(fault):
    values = public_values()
    if fault == "mode":
        values.pop("REPLAYFORGE_DEPLOYMENT_MODE")
    elif fault == "tls":
        values["REPLAYFORGE_DATABASE_URL"] = values["REPLAYFORGE_DATABASE_URL"].replace(
            "verify-full", "require"
        )
    elif fault == "host":
        values["REPLAYFORGE_PRIVATE_DATABASE_HOST"] = "other.example.internal"
    elif fault == "role":
        values["REPLAYFORGE_AUTH_DATABASE_URL"] = values["REPLAYFORGE_DATABASE_URL"]
    elif fault == "origin":
        values["REPLAYFORGE_AUTH_WEB_ORIGIN"] = "http://demo.example.com"
    elif fault == "publication":
        values["REPLAYFORGE_ENABLE_GITHUB_PUBLICATION"] = "true"
    elif fault == "collection":
        values["REPLAYFORGE_ENABLE_COLLECTION_BROKER"] = "true"
    elif fault == "github":
        values["REPLAYFORGE_AUTH_ENABLE_GITHUB"] = "false"
    elif fault == "hostaddr":
        values["REPLAYFORGE_DATABASE_URL"] += "&hostaddr=127.0.0.1"
    elif fault == "ip":
        values["REPLAYFORGE_PRIVATE_DATABASE_HOST"] = "192.0.2.1"
    with pytest.raises(AuthDenied, match="AUTH_PUBLIC_CONFIG_INVALID"):
        AuthLocalConfig.from_env(values, public=True)


def test_public_readiness_checks_both_database_roles(monkeypatch):
    config = AuthLocalConfig.from_env(public_values(), public=True)
    monkeypatch.setattr(local.AuthLocalConfig, "from_env", lambda **_: config)
    monkeypatch.setattr(local, "_create_with_config", lambda _: FastAPI())
    connections = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query):
            assert query == "SELECT 1"
            return self

        def fetchone(self):
            return (1,)

    def connect(dsn, **_):
        connections.append(dsn)
        return Connection()

    monkeypatch.setattr(local.psycopg, "connect", connect)
    client = TestClient(local.create_public_from_env())
    assert client.get("/health/live").json() == {"status": "live"}
    assert connections == []
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert len(connections) == 2

    def unavailable(*_, **__):
        raise psycopg.OperationalError("private database details")

    monkeypatch.setattr(local.psycopg, "connect", unavailable)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
