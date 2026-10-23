"""Bounded, approval-rechecked GitHub tree/commit/ref/draft-PR publication.

Only trusted application code may inject authorize(). Nothing here authenticates
humans, discovers keys, executes source, updates target refs or merges a PR.
"""

import base64
import binascii
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from typing import Literal, Self, TypeVar
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, CommitSHA
from packages.policies.repository import admit
from services.approval.journal import PublicationIntent
from services.publication.objects import TreeEntry, commit_bytes, git_sha, tree_sha
from services.publication.patch import (
    PublicationDenied,
    apply_approved_patch,
    blob_sha,
    patch_paths,
)

T = TypeVar("T", bound=BaseModel)


class PublicationWork(BaseModel):
    """Private trusted journal inputs; serializing this never grants permission."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    intent: PublicationIntent
    issued_at: AwareDatetime
    diff: str = Field(max_length=500000, repr=False)
    grant: RepositoryGrant


class PullRequestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    external_id: UUID
    source_commit: CommitSHA
    patch_ref: ArtifactID
    head_commit: CommitSHA
    head_branch: str = Field(pattern=r"^replayforge/[a-f0-9]{32}-[a-f0-9]{12}$")
    number: int = Field(strict=True, ge=1)
    html_url: str = Field(max_length=400)
    state: Literal["open", "closed"]
    origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"]
    observed_at: AwareDatetime
    can_authorize_merge: Literal[False] = False


class Identity(BaseModel):
    sha: CommitSHA


class Commit(Identity):
    tree: Identity


class RefObject(Identity):
    type: Literal["commit"]


class Ref(BaseModel):
    ref: str = Field(max_length=200)
    object: RefObject


class Tree(Identity):
    tree: list[TreeEntry] = Field(max_length=4096)
    truncated: bool = Field(strict=True)


class Blob(Identity):
    encoding: Literal["base64"]
    size: int = Field(strict=True, ge=0, le=65536)
    content: str = Field(max_length=90000, repr=False)


class Repo(BaseModel):
    full_name: str = Field(max_length=150)


class PullSide(Identity):
    ref: str = Field(max_length=200)
    repo: Repo


class Pull(BaseModel):
    number: int = Field(strict=True, ge=1)
    html_url: str = Field(max_length=400)
    state: Literal["open", "closed"]
    body: str | None = Field(max_length=20000, repr=False)
    head: PullSide
    base: PullSide


def _model(model: type[T], value: object) -> T:
    try:
        return model.model_validate(value)
    except ValidationError:
        raise PublicationDenied("PUBLICATION_RESPONSE_INVALID") from None


class GitHubPublisher:
    """One bounded publication per instance at a time. No automatic write retry.

    Explicitly opt into real writes. Custom transports are always test evidence.
    Before every POST, authorize must reload current human permission and consume
    or reconcile the SAME durable approval/intent, not return browser/model data.
    """

    def __init__(
        self,
        token: SecretStr,
        *,
        allow_live_writes: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.origin: Literal["TEST_TRANSPORT", "REMOTE_SERVICE"] = (
            "TEST_TRANSPORT" if transport is not None else "REMOTE_SERVICE"
        )
        self._live = allow_live_writes is True
        self._client = httpx.Client(
            base_url="https://api.github.com/",
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=3),
            headers={
                "Authorization": "Bearer " + token.get_secret_value(),
                "Accept": "application/vnd.github+json",
                "Accept-Encoding": "identity",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        self._lock = Lock()
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        self._closed = True

    def _call(
        self,
        method: str,
        suffix: str,
        payload: dict[str, object] | None = None,
        *,
        missing: bool = False,
        conflict: bool = False,
    ) -> object:
        if time.monotonic() >= self._deadline or self._requests >= 128:
            raise PublicationDenied("PUBLICATION_BUDGET_EXHAUSTED")
        if method == "POST":
            self._guard()
            if time.monotonic() >= self._deadline:
                raise PublicationDenied("PUBLICATION_BUDGET_EXHAUSTED")
        self._requests += 1
        try:
            with self._client.stream(
                method, f"repos/{self._repo}/{suffix}", json=payload
            ) as response:
                if response.status_code in {401, 403}:
                    raise PublicationDenied("PUBLICATION_AUTH_DENIED")
                if 300 <= response.status_code < 400:
                    raise PublicationDenied("PUBLICATION_REDIRECT_DENIED")
                if (missing and response.status_code == 404) or (
                    conflict and response.status_code == 422
                ):
                    return None
                if response.status_code != (201 if method == "POST" else 200):
                    raise PublicationDenied("PUBLICATION_HTTP_FAILURE")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise PublicationDenied("PUBLICATION_ENCODING_DENIED")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if (
                        len(body) + len(chunk) > 262144
                        or time.monotonic() >= self._deadline
                    ):
                        raise PublicationDenied("PUBLICATION_RESPONSE_LIMIT")
                    body.extend(chunk)
                return json.loads(body)
        except httpx.HTTPError:
            raise PublicationDenied(
                "PUBLICATION_WRITE_UNKNOWN"
                if method == "POST"
                else "PUBLICATION_READ_FAILED"
            ) from None
        except (ValueError, UnicodeError):
            raise PublicationDenied("PUBLICATION_RESPONSE_INVALID") from None

    def _guard(self) -> None:
        value = self._authorize()
        if value != self._work.intent:
            raise PublicationDenied("PUBLICATION_APPROVAL_CHANGED")

    def _ref(self, branch: str, *, missing: bool = False) -> str | None:
        data = self._call(
            "GET", "git/ref/heads/" + quote(branch, safe="/"), missing=missing
        )
        if data is None and missing:
            return None
        ref = _model(Ref, data)
        if ref.ref != "refs/heads/" + branch:
            raise PublicationDenied("PUBLICATION_REF_MISMATCH")
        return ref.object.sha

    def _tree(self, sha: str) -> list[TreeEntry]:
        if sha not in self._trees:
            tree = _model(Tree, self._call("GET", f"git/trees/{sha}"))
            if tree.truncated or tree.sha != sha or tree_sha(tree.tree) != sha:
                raise PublicationDenied("PUBLICATION_TREE_IDENTITY")
            self._trees[sha] = tree.tree
        return self._trees[sha]

    def _source(self, root: str, path: str) -> bytes:
        sha = root
        parts = path.split("/")
        for index, part in enumerate(parts):
            entry = next((e for e in self._tree(sha) if e.path == part), None)
            if entry is None:
                raise PublicationDenied("PUBLICATION_SOURCE_MISSING")
            if index != len(parts) - 1:
                if entry.mode != "040000":
                    raise PublicationDenied("PUBLICATION_SOURCE_MODE")
                sha = entry.sha
            else:
                if entry.mode != "100644":
                    raise PublicationDenied("PUBLICATION_SOURCE_MODE")
                blob = _model(Blob, self._call("GET", f"git/blobs/{entry.sha}"))
                try:
                    content = base64.b64decode(
                        "".join(blob.content.splitlines()), validate=True
                    )
                except (ValueError, binascii.Error):
                    raise PublicationDenied("PUBLICATION_BLOB_INVALID") from None
                if (
                    blob.sha != entry.sha
                    or len(content) != blob.size
                    or blob_sha(content) != entry.sha
                ):
                    raise PublicationDenied("PUBLICATION_BLOB_IDENTITY")
                return content
        raise PublicationDenied("PUBLICATION_SOURCE_MISSING")

    def _new_tree(
        self, sha: str, replacements: dict[str, str], prefix: str = ""
    ) -> str:
        entries = []
        for entry in self._tree(sha):
            path = prefix + entry.path
            if path in replacements:
                entry = entry.model_copy(update={"sha": replacements[path]})
            elif entry.mode == "040000" and any(
                p.startswith(path + "/") for p in replacements
            ):
                entry = entry.model_copy(
                    update={"sha": self._new_tree(entry.sha, replacements, path + "/")}
                )
            entries.append(entry)
        return tree_sha(entries)

    def _find_pull(self, commit: str) -> PullRequestResult | None:
        query = urlencode(
            {
                "state": "all",
                "head": self._repo.split("/")[0] + ":" + self._work.intent.head_branch,
                "base": self._work.intent.binding.target_branch,
                "per_page": 2,
            }
        )
        data = self._call("GET", "pulls?" + query)
        if not isinstance(data, list) or len(data) > 1:
            raise PublicationDenied("PUBLICATION_PR_AMBIGUOUS")
        return None if not data else self._result(_model(Pull, data[0]), commit)

    def _result(self, pull: Pull, commit: str) -> PullRequestResult:
        intent = self._work.intent
        url = f"https://github.com/{self._repo}/pull/{pull.number}"
        if (
            pull.head.ref != intent.head_branch
            or pull.head.sha != commit
            or pull.base.ref != intent.binding.target_branch
            or pull.base.sha != intent.binding.source_commit
            or pull.head.repo.full_name.casefold() != self._repo.casefold()
            or pull.base.repo.full_name.casefold() != self._repo.casefold()
            or pull.html_url.casefold() != url.casefold()
            or self._marker not in (pull.body or "")
        ):
            raise PublicationDenied("PUBLICATION_PR_BINDING")
        return PullRequestResult(
            run_id=intent.binding.run_id,
            external_id=intent.external_id,
            source_commit=intent.binding.source_commit,
            patch_ref=intent.binding.patch_ref,
            head_commit=commit,
            head_branch=intent.head_branch,
            number=pull.number,
            html_url=url,
            state=pull.state,
            origin=self.origin,
            observed_at=datetime.now(UTC),
        )

    def publish(
        self, work: PublicationWork, authorize: Callable[[], PublicationIntent]
    ) -> PullRequestResult:
        if self._closed or (self.origin == "REMOTE_SERVICE" and not self._live):
            raise PublicationDenied("PUBLICATION_LIVE_NOT_AUTHORIZED")
        if not self._lock.acquire(blocking=False):
            raise PublicationDenied("PUBLICATION_BUSY")
        try:
            work = PublicationWork.model_validate(work.model_dump())
            binding, grant = work.intent.binding, work.grant
            if (
                not admit(
                    grant,
                    binding.owner_id,
                    binding.repository_id,
                    binding.source_commit,
                ).allowed
                or work.intent.head_branch
                != f"replayforge/{binding.run_id.hex}-{binding.patch_ref[-12:]}"
                or binding.patch_ref
                != "sha256:" + sha256(work.diff.encode()).hexdigest()
            ):
                raise PublicationDenied("PUBLICATION_INPUT_DENIED")
            paths = patch_paths(work.diff, grant)
            self._work, self._authorize = work, authorize
            self._repo = grant.remote_url.removeprefix("https://github.com/")
            self._marker = f"<!-- replayforge:{work.intent.external_id} -->"
            self._requests, self._deadline = 0, time.monotonic() + 60
            self._trees: dict[str, list[TreeEntry]] = {}
            self._guard()
            if self._ref(binding.target_branch) != binding.source_commit:
                raise PublicationDenied("PUBLICATION_BASE_MOVED")
            source = _model(
                Commit, self._call("GET", f"git/commits/{binding.source_commit}")
            )
            if source.sha != binding.source_commit:
                raise PublicationDenied("PUBLICATION_SOURCE_IDENTITY")
            edits = apply_approved_patch(
                work.diff, {p: self._source(source.tree.sha, p) for p in paths}, grant
            )
            expected_tree = self._new_tree(
                source.tree.sha, {e.path: e.after_sha for e in edits}
            )
            if expected_tree == source.tree.sha:
                raise PublicationDenied("PUBLICATION_EMPTY_CHANGE")
            message = f"ReplayForge repair {work.intent.external_id}\n"
            expected_commit = git_sha(
                "commit",
                commit_bytes(
                    expected_tree, binding.source_commit, message, work.issued_at
                ),
            )
            known = self._ref(work.intent.head_branch, missing=True)
            if known is not None and known != expected_commit:
                raise PublicationDenied("PUBLICATION_BRANCH_CONFLICT")
            if known is None:
                created = _model(
                    Identity,
                    self._call(
                        "POST",
                        "git/trees",
                        {
                            "base_tree": source.tree.sha,
                            "tree": [
                                {
                                    "path": e.path,
                                    "mode": "100644",
                                    "type": "blob",
                                    "content": e.content,
                                }
                                for e in edits
                            ],
                        },
                    ),
                )
                if created.sha != expected_tree:
                    raise PublicationDenied("PUBLICATION_CREATED_TREE_MISMATCH")
                actor = {
                    "name": "ReplayForge",
                    "email": "replayforge@users.noreply.github.com",
                    "date": work.issued_at.astimezone(UTC)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
                created = _model(
                    Identity,
                    self._call(
                        "POST",
                        "git/commits",
                        {
                            "tree": expected_tree,
                            "parents": [binding.source_commit],
                            "message": message,
                            "author": actor,
                            "committer": actor,
                        },
                    ),
                )
                if created.sha != expected_commit:
                    raise PublicationDenied("PUBLICATION_CREATED_COMMIT_MISMATCH")
                self._call(
                    "POST",
                    "git/refs",
                    {
                        "ref": "refs/heads/" + work.intent.head_branch,
                        "sha": expected_commit,
                    },
                    conflict=True,
                )
                if self._ref(work.intent.head_branch) != expected_commit:
                    raise PublicationDenied("PUBLICATION_BRANCH_CONFLICT")
            existing = self._find_pull(expected_commit)
            if existing is not None:
                return existing
            if self._ref(binding.target_branch) != binding.source_commit:
                raise PublicationDenied("PUBLICATION_BASE_MOVED")
            data = self._call(
                "POST",
                "pulls",
                {
                    "title": "ReplayForge verified repair",
                    "head": work.intent.head_branch,
                    "base": binding.target_branch,
                    "draft": True,
                    "maintainer_can_modify": False,
                    "body": (
                        f"{self._marker}\nSource: {binding.source_commit}\n"
                        f"Patch: {binding.patch_ref}\n"
                        f"Evidence: {binding.verification_ref}\n\n"
                        "Review the retained evidence report for test results "
                        "and limitations."
                    ),
                },
                conflict=True,
            )
            result = (
                self._find_pull(expected_commit)
                if data is None
                else self._result(_model(Pull, data), expected_commit)
            )
            if result is None:
                raise PublicationDenied("PUBLICATION_PR_CONFLICT")
            return result
        except (ValidationError, UnicodeError):
            raise PublicationDenied("PUBLICATION_INPUT_INVALID") from None
        finally:
            self._lock.release()
