"""Small synthetic GitHub server; no credentials, network or repository writes."""

import base64
import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from services.approval.journal import PublicationIntent
from services.approval.policy import ApprovalBinding
from services.publication.github import GitHubPublisher, PublicationWork
from services.publication.objects import TreeEntry, commit_bytes, git_sha, tree_sha
from services.publication.patch import PublicationDenied, blob_sha
from tests.unit.test_publication_patch import DIFF, grant


def setup(fault=None):
    scope = grant()
    binding = ApprovalBinding(
        run_id=uuid4(),
        repository_id=scope.repository_id,
        owner_id=scope.owner_id,
        source_commit="a" * 40,
        patch_ref="sha256:" + sha256(DIFF.encode()).hexdigest(),
        verification_ref="sha256:" + "b" * 64,
        target_branch="main",
        policy_version="local-v1",
    )
    intent = PublicationIntent(
        external_id=uuid4(),
        approval_nonce=uuid4(),
        binding=binding,
        reviewer_id=uuid4(),
        head_branch=f"replayforge/{binding.run_id.hex}-{binding.patch_ref[-12:]}",
    )
    work = PublicationWork(
        intent=intent,
        issued_at=datetime(2026, 10, 22, tzinfo=UTC),
        diff=DIFF,
        grant=scope,
    )
    original = b"x=1\ny=2\n"
    leaf = [TreeEntry(path="a.py", mode="100644", type="blob", sha=blob_sha(original))]
    root = [TreeEntry(path="src", mode="040000", type="tree", sha=tree_sha(leaf))]
    calls, state = [], {"branch": None, "pr": None, "lost": False}

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.github.com"
        path = request.url.path.removeprefix("/repos/example/demo/")
        payload = json.loads(request.content) if request.content else {}
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://bad.example"})
        if path == "git/ref/heads/main":
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {
                        "type": "commit",
                        "sha": "f" * 40 if fault == "base" else "a" * 40,
                    },
                },
            )
        if path.startswith("git/ref/heads/replayforge/"):
            return (
                httpx.Response(404)
                if state["branch"] is None
                else httpx.Response(
                    200,
                    json={
                        "ref": "refs/heads/" + intent.head_branch,
                        "object": {"type": "commit", "sha": state["branch"]},
                    },
                )
            )
        if path == "git/commits/" + "a" * 40:
            return httpx.Response(
                200, json={"sha": "a" * 40, "tree": {"sha": tree_sha(root)}}
            )
        if path.startswith("git/trees/"):
            entries = root if path.endswith(tree_sha(root)) else leaf
            data = {
                "sha": path.rsplit("/", 1)[-1],
                "truncated": False,
                "tree": [e.model_dump() for e in entries],
            }
            if fault == "tree":
                data["tree"] = []
            return httpx.Response(200, json=data)
        if path.startswith("git/blobs/"):
            return httpx.Response(
                200,
                json={
                    "sha": blob_sha(original),
                    "size": len(original),
                    "encoding": "base64",
                    "content": base64.b64encode(
                        b"different" if fault == "blob" else original
                    ).decode(),
                },
            )
        if path == "git/trees":
            assert payload["tree"] == [
                {
                    "path": "src/a.py",
                    "mode": "100644",
                    "type": "blob",
                    "content": "x=1\ny=3\n",
                }
            ]
            changed = [leaf[0].model_copy(update={"sha": blob_sha(b"x=1\ny=3\n")})]
            digest = tree_sha([root[0].model_copy(update={"sha": tree_sha(changed)})])
            return httpx.Response(
                201, json={"sha": "0" * 40 if fault == "new_tree" else digest}
            )
        if path == "git/commits":
            assert payload["parents"] == ["a" * 40]
            digest = git_sha(
                "commit",
                commit_bytes(
                    payload["tree"], "a" * 40, payload["message"], work.issued_at
                ),
            )
            return httpx.Response(201, json={"sha": digest})
        if path == "git/refs":
            assert payload["ref"] == "refs/heads/" + intent.head_branch
            assert "force" not in payload
            state["branch"] = payload["sha"]
            if fault == "lost_ref" and not state["lost"]:
                state["lost"] = True
                raise httpx.ReadTimeout("private response", request=request)
            if fault == "ref_conflict":
                return httpx.Response(422)
            return httpx.Response(
                201,
                json={
                    "ref": payload["ref"],
                    "object": {"type": "commit", "sha": payload["sha"]},
                },
            )
        if path == "pulls" and request.method == "GET":
            return httpx.Response(
                200, json=[] if state["pr"] is None else [state["pr"]]
            )
        if path == "pulls" and request.method == "POST":
            assert payload["draft"] is True
            state["pr"] = {
                "number": 1,
                "html_url": "https://github.com/example/demo/pull/1",
                "state": "open",
                "body": payload["body"],
                "head": {
                    "ref": intent.head_branch,
                    "sha": state["branch"],
                    "repo": {"full_name": "example/demo"},
                },
                "base": {
                    "ref": "main",
                    "sha": "a" * 40,
                    "repo": {"full_name": "example/demo"},
                },
            }
            if fault == "lost_pr" and not state["lost"]:
                state["lost"] = True
                raise httpx.ReadTimeout("private response", request=request)
            if fault == "pr_conflict":
                return httpx.Response(422)
            return httpx.Response(201, json=state["pr"])
        pytest.fail(f"unexpected route {request.method} {path}")

    return work, calls, state, httpx.MockTransport(handler)


def test_exact_publication_and_restart_find_same_branch_pr_without_more_posts():
    work, calls, state, transport = setup()
    guards = []

    def authorize():
        guards.append(1)
        return work.intent

    with GitHubPublisher(
        SecretStr("synthetic-write-key"), transport=transport
    ) as publisher:
        first = publisher.publish(work, authorize)
    count = sum(r.method == "POST" for r in calls)
    with GitHubPublisher(
        SecretStr("synthetic-write-key"), transport=transport
    ) as restarted:
        second = restarted.publish(work, authorize)
    assert first.number == second.number == 1
    assert first.head_commit == second.head_commit
    assert first.origin == "TEST_TRANSPORT"
    assert sum(r.method == "POST" for r in calls) == count == 4
    assert len(guards) >= count
    assert all("synthetic-write-key" not in r.content.decode() for r in calls)


@pytest.mark.parametrize("fault", ["base", "tree", "blob", "redirect", "new_tree"])
def test_bad_provider_facts_never_create_ref_or_pr(fault):
    work, calls, state, transport = setup(fault)
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        with pytest.raises(PublicationDenied):
            publisher.publish(work, lambda: work.intent)
    assert state["branch"] is None and state["pr"] is None


def test_lost_pr_response_is_recovered_by_discovery_without_second_creation():
    work, calls, state, transport = setup("lost_pr")
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        with pytest.raises(PublicationDenied, match="WRITE_UNKNOWN"):
            publisher.publish(work, lambda: work.intent)
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as restarted:
        assert restarted.publish(work, lambda: work.intent).number == 1
    assert sum(r.method == "POST" and r.url.path.endswith("/pulls") for r in calls) == 1


def test_unapproved_work_and_live_default_do_no_http():
    work, calls, _, transport = setup()
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        with pytest.raises(PublicationDenied):
            publisher.publish(
                work.model_copy(update={"diff": DIFF + "\n"}), lambda: work.intent
            )
        with pytest.raises(PublicationDenied):
            publisher.publish(
                work, lambda: work.intent.model_copy(update={"external_id": uuid4()})
            )
    assert not calls
    with GitHubPublisher(SecretStr("synthetic")) as live:
        with pytest.raises(PublicationDenied):
            live.publish(work, lambda: work.intent)


@pytest.mark.parametrize("fault", ["lost_ref", "ref_conflict", "pr_conflict"])
def test_uncertain_or_conflicting_creation_discovers_exact_owned_objects(fault):
    work, calls, state, transport = setup(fault)
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        if fault == "lost_ref":
            with pytest.raises(PublicationDenied, match="WRITE_UNKNOWN"):
                publisher.publish(work, lambda: work.intent)
        else:
            assert publisher.publish(work, lambda: work.intent).number == 1
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as restarted:
        assert restarted.publish(work, lambda: work.intent).number == 1
    assert (
        sum(r.method == "POST" and r.url.path.endswith("/git/refs") for r in calls) == 1
    )
    assert sum(r.method == "POST" and r.url.path.endswith("/pulls") for r in calls) == 1


def test_revocation_between_tree_and_commit_stops_all_following_writes():
    work, calls, state, transport = setup()

    def authorize():
        if any(r.method == "POST" for r in calls):
            raise PublicationDenied("REVIEW_REVOKED")
        return work.intent

    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        with pytest.raises(PublicationDenied, match="REVIEW_REVOKED"):
            publisher.publish(work, authorize)
    assert [r.url.path.rsplit("/", 1)[-1] for r in calls if r.method == "POST"] == [
        "trees"
    ]
    assert state["branch"] is None and state["pr"] is None


@pytest.mark.parametrize("fault", ["branch", "marker", "url", "head", "base"])
def test_existing_objects_require_exact_ownership_and_bindings(fault):
    work, calls, state, transport = setup()
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as publisher:
        publisher.publish(work, lambda: work.intent)
    count = sum(r.method == "POST" for r in calls)
    if fault == "branch":
        state["branch"] = "f" * 40
    elif fault == "marker":
        state["pr"]["body"] = "unrelated PR"
    elif fault == "url":
        state["pr"]["html_url"] = "https://bad.example/"
    else:
        state["pr"][fault]["sha"] = "f" * 40
    with GitHubPublisher(SecretStr("synthetic"), transport=transport) as restarted:
        with pytest.raises(PublicationDenied):
            restarted.publish(work, lambda: work.intent)
    assert sum(r.method == "POST" for r in calls) == count
