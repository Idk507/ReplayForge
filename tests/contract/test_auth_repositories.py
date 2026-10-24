"""User/app intersection checks over tiny synthetic repository responses."""

import httpx
import pytest
from pydantic import SecretStr

from services.auth.github import AuthDenied
from services.auth.repositories import RepositoryClient
from tests.contract.test_github_auth import settings


def provider(fault=None):
    calls = []

    def handle(r):
        calls.append(r)
        if r.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "installations": [
                        {
                            "id": 7,
                            "account": {"id": 9, "login": "example"},
                            "suspended_at": None,
                            "permissions": {
                                "contents": "read" if fault == "write" else "write",
                                "pull_requests": "write",
                            },
                        }
                    ],
                },
            )
        if r.url.path == "/user/installations/7/repositories":
            return httpx.Response(
                200,
                json={
                    "total_count": 0 if fault == "removed" else 1,
                    "repositories": []
                    if fault == "removed"
                    else [
                        {
                            "id": 42,
                            "full_name": "example/demo",
                            "private": False,
                            "permissions": {"pull": True, "push": fault != "readonly"},
                            "archived": False,
                            "disabled": False,
                        }
                    ],
                },
            )
        if r.url.path == "/repos/example/demo/commits":
            assert r.url.params["per_page"] == "1"
            return httpx.Response(200, json=[{"sha": "a" * 40}])
        if r.url.path == "/repos/example/demo/git/commits/" + "a" * 40:
            return httpx.Response(200, json={"sha": "a" * 40})
        pytest.fail("unexpected provider path")

    return calls, httpx.MockTransport(handle)


def test_repository_intersection_and_exact_commit():
    calls, transport = provider()
    with RepositoryClient(settings(), transport=transport) as c:
        repo = c.authorize(SecretStr("synthetic"), 7, 42, write=True)
        c.verify_commit(SecretStr("synthetic"), repo.full_name, "a" * 40)
    assert repo.full_name == "example/demo"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "fault", ["removed", "readonly", "write", "installation", "repository"]
)
def test_repository_access_denials_never_fall_back_to_broader_token(fault):
    calls, transport = provider(fault)
    with RepositoryClient(settings(), transport=transport) as c:
        with pytest.raises(AuthDenied):
            c.authorize(
                SecretStr("synthetic"),
                8 if fault == "installation" else 7,
                43 if fault == "repository" else 42,
                write=True,
            )
    assert all(r.method == "GET" for r in calls)


def test_repository_list_cannot_return_truncated_or_repeated_pages():
    calls = []

    def handle(r):
        calls.append(r)
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "installations": [
                    {
                        "id": 7,
                        "account": {"id": 9, "login": "example"},
                        "permissions": {},
                        "suspended_at": None,
                    }
                ],
            },
        )

    with RepositoryClient(settings(), transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(AuthDenied):
            c.installations(SecretStr("synthetic"))
    assert len(calls) <= 10


def test_large_account_repository_pages_are_bounded_and_authorized():
    def handle(r):
        if r.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "installations": [
                        {
                            "id": 7,
                            "account": {"id": 9, "login": "example"},
                            "permissions": {"contents": "read"},
                        }
                    ],
                },
            )
        page = int(r.url.params["page"])
        rows = [
            {
                "id": i + 1,
                "full_name": f"example/repo{i + 1}",
                "private": False,
                "permissions": {"pull": True, "push": False},
            }
            for i in range((page - 1) * 50, min(page * 50, 367))
        ]
        return httpx.Response(200, json={"total_count": 367, "repositories": rows})

    with RepositoryClient(settings(), transport=httpx.MockTransport(handle)) as c:
        assert len(c.repository_page(SecretStr("synthetic"), 7, 1)) == 50
        assert len(c.repository_page(SecretStr("synthetic"), 7, 8)) == 17
        assert c.authorize(SecretStr("synthetic"), 7, 367, page=8).id == 367
        with pytest.raises(AuthDenied):
            c.authorize(SecretStr("synthetic"), 7, 367, page=1)
        with pytest.raises(AuthDenied):
            c.repository_page(SecretStr("synthetic"), 7, 0)


@pytest.mark.parametrize("fault", ["duplicate", "truncated", "wrong_page"])
def test_repository_pages_reject_ambiguous_membership(fault):
    def handle(r):
        repo = {
            "id": 1,
            "full_name": "example/demo",
            "private": False,
            "permissions": {"pull": True},
        }
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "repositories": [repo, repo] if fault == "duplicate" else [repo],
            },
        )

    with RepositoryClient(settings(), transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(AuthDenied):
            c.repository_page(
                SecretStr("synthetic"), 7, 2 if fault == "wrong_page" else 1
            )
