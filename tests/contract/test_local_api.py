"""Local API admission contracts without cloud calls."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from services.api.app import LocalAuth, create_app
from services.persistence.store import Store


def client() -> TestClient:
    app = create_app(
        Store("invalid-not-used"),
        LocalAuth(
            token=SecretStr("synthetic-test-token-" * 3),
            owner_id=uuid4(),
            approved_repositories=(uuid4(),),
        ),
    )
    return TestClient(app, client=("127.0.0.1", 1234))


def test_missing_and_wrong_identity_denied_before_database() -> None:
    with client() as http:
        assert http.get(f"/api/v1/runs/{uuid4()}").status_code == 401
        assert (
            http.get(
                f"/api/v1/runs/{uuid4()}", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 403
        )


def test_remote_clients_denied() -> None:
    app = create_app(Store("invalid-not-used"), LocalAuth())
    with TestClient(app, client=("203.0.113.2", 1234)) as http:
        assert http.get("/health/live").status_code == 403


def test_disabled_auth_fails_closed() -> None:
    app = create_app(Store("invalid-not-used"), LocalAuth())
    with TestClient(app, client=("127.0.0.1", 1234)) as http:
        assert http.get(f"/api/v1/runs/{uuid4()}").status_code == 503


def test_validation_does_not_echo_secret_input() -> None:
    with client() as http:
        response = http.post(
            "/api/v1/runs",
            headers={"Authorization": "Bearer " + "synthetic-test-token-" * 3},
            json={"incident_text": "secret-value-do-not-echo"},
        )
        assert response.status_code == 422
        assert "secret-value" not in response.text


def test_excessive_request_rejected() -> None:
    with client() as http:
        response = http.post("/api/v1/runs", content=b"x" * 32769)
        assert response.status_code == 413


def test_short_token_rejected() -> None:
    with pytest.raises(ValidationError):
        LocalAuth(token=SecretStr("weak"))


def test_unapproved_repository_denied_before_database() -> None:
    with client() as http:
        response = http.post(
            "/api/v1/runs",
            headers={"Authorization": "Bearer " + "synthetic-test-token-" * 3},
            json={
                "repository_id": str(uuid4()),
                "commit_sha": "a" * 40,
                "incident_text": "synthetic",
                "idempotency_key": str(uuid4()),
            },
        )
        assert response.status_code == 403


def test_forwarded_peer_header_cannot_bypass_loopback_boundary() -> None:
    with TestClient(
        create_app(Store("unused"), LocalAuth()), client=("203.0.113.5", 1)
    ) as http:
        assert (
            http.get(
                "/health/live", headers={"X-Forwarded-For": "127.0.0.1"}
            ).status_code
            == 403
        )
