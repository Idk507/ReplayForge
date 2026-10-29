"""Configured read loader is not a model tool or automatic network dispatcher."""

from datetime import UTC, datetime
from types import SimpleNamespace
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


def test_checkout_loader_requests_only_the_consented_application_and_test_files(
    monkeypatch,
) -> None:
    from services.orchestrator import source as source_module

    run, original = state_and_grant()
    grant = original.model_copy(update={"approved_paths": ("checkout/", "tests/")})
    selected = ("checkout/payments.py", "tests/test_payments.py")
    observed = []
    now = datetime.now(UTC)

    class Reader:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def acquire(self, _grant, _owner, _commit, *, selected_paths):
            observed.append(selected_paths)
            files = tuple(
                SimpleNamespace(
                    path=path,
                    blob_id="a" * 40,
                    sha256="b" * 64,
                    size_bytes=1,
                )
                for path in selected_paths
            )
            snapshot = SimpleNamespace(files=files)
            resolution = SimpleNamespace(
                tree_sha="c" * 40,
                evidence_origin="TEST_TRANSPORT",
                observed_at=now,
                trace_id=uuid4(),
            )
            return SimpleNamespace(snapshot=snapshot, resolution=resolution)

    monkeypatch.setattr(source_module, "GitHubReader", Reader)
    result = github_inputs(SecretStr("synthetic-read-token"), selected)(run, grant)
    assert observed == [selected]
    assert tuple(file.path for file in result.source_manifest.files) == selected


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
