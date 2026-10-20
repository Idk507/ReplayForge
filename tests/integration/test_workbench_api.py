"""Small real database discovery and private patch routes for the local UI."""

from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from packages.contracts.run import RunState
from services.api.app import LocalAuth, create_app
from tests.integration import test_postgres

store = test_postgres.store


def test_recent_runs_bounded_owner_scoped_authenticated_and_patch_private(store):
    owner = uuid4()
    for _ in range(32):
        state = RunState(
            run_id=uuid4(),
            incident_id=uuid4(),
            repository_id=uuid4(),
            source_commit="a" * 40,
            policy_version="local-v1",
            trace_id=uuid4(),
        )
        store.create(state, owner, uuid4(), "a" * 64)
    assert len(store.recent(owner)) == 30
    assert store.recent(owner)[0] == state
    assert store.recent(uuid4()) == ()
    token = "synthetic-workbench-token-" * 3
    app = create_app(store, LocalAuth(owner_id=owner, token=SecretStr(token)))
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        assert client.get("/api/v1/runs").status_code == 401
        headers = {"Authorization": "Bearer " + token}
        result = client.get("/api/v1/runs", headers=headers)
        assert result.status_code == 200 and len(result.json()) == 30
        assert result.json()[0]["run_id"] == str(state.run_id)
        assert (
            client.get(
                f"/api/v1/runs/{state.run_id}/patch", headers=headers
            ).status_code
            == 404
        )
