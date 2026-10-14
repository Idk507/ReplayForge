"""Configured read loader is not a model tool or automatic network dispatcher."""

from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState
from services.orchestrator.collection import SourceInputError
from services.orchestrator.source import github_inputs


def state_and_grant():
    state = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        source_commit="a" * 40,
        policy_version="local-v1",
        trace_id=uuid4(),
    )
    grant = RepositoryGrant(
        repository_id=state.repository_id,
        owner_id=uuid4(),
        remote_url="https://github.com/example/fixture",
        commits=(state.source_commit,),
        approved_paths=("src/",),
        consent=True,
    )
    return state, grant


def test_disallowed_paths_are_terminal_before_http() -> None:
    run, grant = state_and_grant()

    def handle(request):
        raise AssertionError("policy denial reached HTTP")

    loader = github_inputs(
        SecretStr("synthetic-read-token"),
        ("tests/a.py",),
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(SourceInputError, match="SOURCE_REJECTED"):
        loader(run, grant)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_failure_is_safe_terminal_input_outcome(status: int) -> None:
    run, grant = state_and_grant()
    loader = github_inputs(
        SecretStr("synthetic-read-token"),
        ("src/a.py",),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text="sensitive provider body")
        ),
    )
    with pytest.raises(SourceInputError, match="^SOURCE_UNAVAILABLE$"):
        loader(run, grant)


@pytest.mark.parametrize(
    "paths", [(), ("src/a.py",) * 2, tuple(f"src/a{i}.py" for i in range(17))]
)
def test_unbounded_loader_configuration_rejected(paths) -> None:
    with pytest.raises(ValueError, match="PATH_LIMIT"):
        github_inputs(SecretStr("synthetic-read-token"), paths)
