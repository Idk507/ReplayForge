"""Session-owned repository consent and API isolation with synthetic GitHub only."""

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.auth.access import AuthAccess, ConnectRepository, create_session_app
from services.auth.github import AuthDenied
from services.auth.journal import AuthJournal
from services.auth.repositories import RepositoryClient
from services.persistence.store import Store
from tests.contract.test_auth_repositories import provider
from tests.contract.test_github_auth import settings
from tests.integration import test_postgres
from tests.integration.test_auth_journal import identity, journal, login

store = test_postgres.store


def setup(store, fault=None, who=None):
    base = journal(store)
    # Explicit synthetic consent authority, not a production role prescription.
    authority = Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"])
    j = AuthJournal(authority, base.vault)
    config = settings()
    calls, transport = provider(fault)

    def factory():
        return RepositoryClient(config, transport=transport)

    access = AuthAccess(j, config, factory, allow_test=True)
    app = create_session_app(store, j, config, factory, access, allow_test=True)
    session = login(j, who)
    c = TestClient(app, base_url=config.public_origin, client=("127.0.0.1", 50000))
    c.cookies.set(config.session_cookie, session.secret.get_secret_value())
    headers = {
        "origin": config.web_origin,
        "x-csrf-token": j.vault.digest("csrf", session.secret),
    }
    return c, j, session, headers, access, calls


def connect(access, session, *, include_checkout=False, source_commit="a" * 40):
    user = access.journal.session(session.secret, allow_test=True)
    return access.connect(
        user,
        ConnectRepository(
            installation_id=7,
            repository_id=42,
            source_commit=source_commit,
            consent=True,
            include_checkout=include_checkout,
        ),
    )


def test_session_connect_run_owner_scope_and_csrf(store):
    c, j, session, headers, access, calls = setup(store)
    repo = connect(access, session)
    assert "checkout/" not in repo.approved_paths
    payload = {
        "repository_id": str(repo.repository_id),
        "commit_sha": "a" * 40,
        "incident_text": "Small synthetic checkout failure",
        "idempotency_key": str(uuid4()),
    }
    assert c.post("/api/v1/runs", json=payload).status_code == 403
    r = c.post("/api/v1/runs", json=payload, headers=headers)
    assert r.status_code == 202
    run = r.json()["run_id"]
    assert c.get("/api/v1/runs/" + run).status_code == 200
    assert c.get("/api/v1/runs/" + run + "/events").status_code == 200
    other = login(j, identity().model_copy(update={"id": 456}))
    c.cookies.set("rf_dev_session", other.secret.get_secret_value())
    assert c.get("/api/v1/runs/" + run).status_code == 404
    assert c.get("/api/v1/runs/" + run + "/events").status_code == 404
    assert c.get("/api/v1/runs/" + run + "/verification").status_code == 404
    assert all(v.method == "GET" for v in calls)


def test_checkout_path_is_added_only_by_explicit_connection_choice(store):
    # Use a distinct synthetic owner so this consent variant doesn't collide
    # with another integration case's immutable connection for the same repo.
    who = identity().model_copy(update={"id": 100000 + uuid4().int % 2_000_000_000})
    _, _, session, _, access, _ = setup(store, who=who)
    repo = connect(access, session, include_checkout=True)
    assert repo.approved_paths.count("checkout/") == 1
    assert repo.consent and repo.commits == ("a" * 40,)


@pytest.mark.parametrize("fault", ["removed", "readonly", "write"])
def test_current_repository_denial_cannot_be_waived_by_old_consent(store, fault):
    _, _, session, _, access, _ = setup(store)
    repo = connect(access, session)
    _, _, _, _, revoked, calls = setup(store, fault)
    with pytest.raises(AuthDenied):
        revoked.authorize(session.user_id, repo.repository_id, write=True)
    assert all(v.method == "GET" for v in calls)


def test_repository_connection_idempotent_and_cannot_change_commit_or_owner(store):
    _, _, session, _, access, _ = setup(store)
    a = connect(access, session)
    b = connect(access, session)
    assert a == b
    with pytest.raises(AuthDenied):
        access.authorize(uuid4(), a.repository_id)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE auth_repository_connections SET revoked_at=clock_timestamp() "
            "WHERE repository_id=%s",
            (a.repository_id,),
        )
    with pytest.raises(AuthDenied):
        access.authorize(session.user_id, a.repository_id)


def test_session_http_connection_validates_protocol_parameters_and_isolation(store):
    c, _, _, headers, _, _ = setup(store)
    assert c.get("/api/v1/github/installations").status_code == 200
    assert (
        c.get(
            "/api/v1/github/repositories", params={"installation_id": "7"}
        ).status_code
        == 200
    )
    payload = {
        "installation_id": 7,
        "repository_id": 42,
        "source_commit": "a" * 40,
        "consent": True,
    }
    connected = c.post("/api/v1/repositories/42/connect", json=payload, headers=headers)
    assert connected.status_code == 200
    assert (
        c.post(
            "/api/v1/repositories/43/connect", json=payload, headers=headers
        ).status_code
        == 403
    )
    c.cookies.clear()
    assert (
        c.get("/api/v1/runs", headers={"authorization": "Bearer ignored"}).status_code
        == 401
    )
    assert c.post("/api/v1/runs", json={}).status_code in {401, 422}


def test_disconnect_revokes_worker_consent_and_is_owner_scoped(store):
    c, j, session, headers, access, _ = setup(store)
    repo = connect(access, session)
    path = "/api/v1/repositories/" + str(repo.repository_id) + "/disconnect"
    assert c.post(path).status_code == 403
    other = login(j, identity().model_copy(update={"id": 789}))
    c.cookies.set("rf_dev_session", other.secret.get_secret_value())
    other_headers = {
        "origin": settings().web_origin,
        "x-csrf-token": j.vault.digest("csrf", other.secret),
    }
    assert c.post(path, headers=other_headers).status_code == 403
    assert store.repository(repo.repository_id, session.user_id).consent
    c.cookies.set("rf_dev_session", session.secret.get_secret_value())
    assert c.post(path, headers=headers).status_code == 204
    assert c.post(path, headers=headers).status_code == 204
    assert not store.repository(repo.repository_id, session.user_id).consent
    with pytest.raises(AuthDenied):
        access.authorize(session.user_id, repo.repository_id)
    fresh = connect(access, session)
    assert fresh.repository_id != repo.repository_id
    assert fresh.consent


def test_repository_page_http_and_discovery_errors_do_not_expire_session(store):
    c, _, _, _, access, _ = setup(store)
    page = c.get(
        "/api/v1/github/repositories", params={"installation_id": 7, "page": 1}
    )
    assert page.status_code == 200 and len(page.json()) == 1
    assert (
        c.get(
            "/api/v1/github/repositories", params={"installation_id": 7, "page": 0}
        ).status_code
        == 422
    )

    def broken(owner, installation_id, page=None):
        raise AuthDenied("AUTH_LIST_LIMIT")

    access.repositories = broken
    assert (
        c.get(
            "/api/v1/github/repositories", params={"installation_id": 7, "page": 1}
        ).status_code
        == 502
    )
    assert c.get("/api/v1/auth/me").status_code == 200


def test_paged_connection_pins_head_and_retains_membership_page(store):
    c, j, session, headers, access, _ = setup(store)
    assert c.get(
        "/api/v1/github/head",
        params={"installation_id": 7, "repository_id": 42, "page": 1},
    ).json() == {"sha": "a" * 40}
    assert (
        c.get(
            "/api/v1/github/head",
            params={"installation_id": 8, "repository_id": 42, "page": 1},
        ).status_code
        == 403
    )
    r = c.post(
        "/api/v1/repositories/42/connect",
        headers=headers,
        json={
            "installation_id": 7,
            "repository_id": 42,
            "repository_page": 1,
            "source_commit": "a" * 40,
            "consent": True,
        },
    )
    assert r.status_code == 200
    from uuid import UUID

    repo = UUID(r.json()["repository_id"])
    access.authorize(session.user_id, repo)
    with j.store._connection() as conn:
        assert (
            conn.execute(
                "SELECT repository_page FROM auth_repository_connections "
                "WHERE repository_id=%s",
                (repo,),
            ).fetchone()["repository_page"]
            == 1
        )
