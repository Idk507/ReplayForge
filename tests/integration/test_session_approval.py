"""Real session/approval journals over synthetic accepted evidence and GitHub."""

import os
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.run import Stage
from services.auth.access import AuthAccess, create_session_app
from services.auth.journal import AuthJournal
from services.auth.publication import install_publication_route
from services.auth.repositories import RepositoryClient
from services.persistence.store import Store
from tests.contract.test_auth_repositories import provider
from tests.contract.test_github_auth import settings
from tests.integration import test_postgres
from tests.integration.test_approval_journal import setup as accepted
from tests.integration.test_auth_journal import identity, journal, login

store = test_postgres.store


def setup(store, fault=None):
    owner, run, binding, _, _ = accepted(store)
    authority = Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"])
    base = journal(store)
    auth = AuthJournal(authority, base.vault)
    github_id = uuid4().int % 1000000000000 + 1
    with authority._connection() as c:
        c.execute("INSERT INTO auth_users(id) VALUES(%s)", (owner,))
        c.execute(
            "INSERT INTO auth_identities(github_id,user_id,login,avatar_url) "
            "VALUES(%s,%s,%s,%s)",
            (github_id, owner, "person", "https://avatars.githubusercontent.com/u/123"),
        )
        c.execute(
            "INSERT INTO auth_repository_connections(repository_id,user_id,"
            "github_repository_id,installation_id,full_name,source_commit,origin) "
            "VALUES(%s,%s,42,7,'example/fixture',%s,'TEST_TRANSPORT')",
            (run.repository_id, owner, run.source_commit),
        )
    calls, underlying = provider(fault)

    def handle(request):
        response = underlying.handle_request(request)
        body = response.json()
        if "repositories" in body:
            for repo in body["repositories"]:
                repo["full_name"] = "example/fixture"
        return httpx.Response(response.status_code, json=body)

    config = settings()

    def factory():
        return RepositoryClient(config, transport=httpx.MockTransport(handle))

    access = AuthAccess(auth, config, factory, allow_test=True)
    app = create_session_app(
        store,
        auth,
        config,
        factory,
        access,
        allow_test=True,
        approval_target_branch="main",
    )
    session = login(auth, identity().model_copy(update={"id": github_id}))
    client = TestClient(app, base_url=config.public_origin, client=("127.0.0.1", 50000))
    client.cookies.set(config.session_cookie, session.secret.get_secret_value())
    headers = {
        "origin": config.web_origin,
        "x-csrf-token": auth.vault.digest("csrf", session.secret),
    }
    payload = {
        "binding": binding.model_dump(mode="json"),
        "expected_version": run.version,
        "nonce": str(uuid4()),
        "decision": "approve",
    }
    return client, headers, payload, run, owner, access, calls


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_authenticated_review_is_exact_durable_and_idempotent(store, decision):
    client, headers, payload, run, owner, access, _ = setup(store)
    scope = access.write_scope(owner, run.repository_id)
    assert scope.github_repository_id == 42 and scope.installation_id == 7
    path = f"/api/v1/runs/{run.run_id}"
    view = client.get(path + "/review")
    assert view.status_code == 200 and view.json()["binding"] == payload["binding"]
    payload["decision"] = decision
    first = client.post(path + "/approval", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    second = client.post(path + "/approval", json=payload, headers=headers)
    assert first.json() == second.json()
    recovered = client.get(path + "/review")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["receipt"] == first.json()
    assert first.json()["reviewer_id"] == str(owner)
    assert first.json()["can_authorize_publication"] is False
    assert store.get(run.run_id, owner).current_stage == (
        Stage.AWAITING_APPROVAL if decision == "approve" else Stage.CLOSED
    )


@pytest.mark.parametrize(
    "fault", ["csrf", "binding", "reviewer", "anonymous", "readonly"]
)
def test_browser_cannot_forge_review_authority(store, fault):
    client, headers, payload, run, owner, _, calls = setup(
        store, "readonly" if fault == "readonly" else None
    )
    if fault == "csrf":
        headers = {}
    if fault == "binding":
        payload["binding"]["patch_ref"] = "sha256:" + "f" * 64
    if fault == "reviewer":
        payload["reviewer_id"] = str(uuid4())
    if fault == "anonymous":
        client.cookies.clear()
    response = client.post(
        f"/api/v1/runs/{run.run_id}/approval", json=payload, headers=headers
    )
    assert response.status_code in {401, 403, 409, 422}, response.text
    assert store.get(run.run_id, owner).current_stage == Stage.VERIFIED
    assert all(request.method == "GET" for request in calls)


def test_publication_requires_exact_review_before_minting(store):
    client, headers, payload, run, owner, access, _ = setup(store)
    minted = []

    def broker():
        minted.append(True)
        pytest.fail("unapproved publication must not reach App signing authority")

    install_publication_route(
        client.app,
        store,
        access,
        broker,
        target_branch="main",
        publication_transport=httpx.MockTransport(lambda r: pytest.fail("no writes")),
    )
    publication = {k: v for k, v in payload.items() if k != "decision"}
    response = client.post(
        f"/api/v1/runs/{run.run_id}/publication", json=publication, headers=headers
    )
    assert response.status_code == 409, response.text
    assert not minted and store.get(run.run_id, owner).current_stage == Stage.VERIFIED


def test_disabled_live_publication_cannot_consume_human_approval(store):
    client, headers, payload, run, owner, access, _ = setup(store)
    assert (
        client.post(
            f"/api/v1/runs/{run.run_id}/approval", json=payload, headers=headers
        ).status_code
        == 200
    )
    install_publication_route(
        client.app,
        store,
        access,
        lambda: pytest.fail("no token mint"),
        target_branch="main",
    )
    publication = {k: v for k, v in payload.items() if k != "decision"}
    publication["expected_version"] += 1
    response = client.post(
        f"/api/v1/runs/{run.run_id}/publication", json=publication, headers=headers
    )
    assert response.status_code != 200
    assert store.get(run.run_id, owner).current_stage == Stage.AWAITING_APPROVAL
