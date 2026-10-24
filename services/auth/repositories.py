"""Read-only user/app repository intersection; no provider-selected URLs or writes."""

import re
from collections.abc import Callable, Sequence
from typing import Annotated, Literal, TypeVar

from pydantic import AwareDatetime, Field, RootModel, SecretStr

from packages.contracts.run import CommitSHA
from services.auth.github import AuthDenied, GitHubAuthClient, PrivateModel

Number = Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]


class Identified(PrivateModel):
    id: Number


class Account(Identified):
    login: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


class AppPermissions(PrivateModel):
    contents: Literal["none", "read", "write"] = "none"
    pull_requests: Literal["none", "read", "write"] = "none"


class Installation(Identified):
    account: Account
    permissions: AppPermissions
    suspended_at: AwareDatetime | None = None


class RepoPermissions(PrivateModel):
    pull: bool = Field(default=False, strict=True)
    push: bool = Field(default=False, strict=True)


class GitHubRepository(Identified):
    full_name: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$"
    )
    private: bool = Field(strict=True)
    permissions: RepoPermissions
    archived: bool = Field(default=False, strict=True)
    disabled: bool = Field(default=False, strict=True)


class Page(PrivateModel):
    total_count: int = Field(strict=True, ge=0, le=50)


class InstallationPage(Page):
    installations: list[Installation] = Field(max_length=5)


class RepositoryPage(Page):
    repositories: list[GitHubRepository] = Field(max_length=5)


class LargeRepositoryPage(PrivateModel):
    total_count: int = Field(strict=True, ge=0, le=1000000)
    repositories: list[GitHubRepository] = Field(max_length=50)


class CommitStamp(PrivateModel):
    sha: CommitSHA


class HeadCommits(RootModel[list[CommitStamp]]):
    root: list[CommitStamp] = Field(min_length=1, max_length=1)


P = TypeVar("P", bound=Page)
R = TypeVar("R", bound=Identified)


class RepositoryClient(GitHubAuthClient):
    """At most 50 records/10 pages per enumeration; refuse incomplete visibility."""

    def _collect(
        self,
        model: type[P],
        path: str,
        items: Callable[[P], Sequence[R]],
        token: SecretStr,
    ) -> tuple[R, ...]:
        result: list[R] = []
        expected = None
        for page in range(1, 11):
            value = self._request(
                model,
                "GET",
                f"https://api.github.com/{path}?per_page=5&page={page}",
                token=token,
            )
            if expected is not None and value.total_count != expected:
                raise AuthDenied("AUTH_LIST_CHANGED")
            expected = value.total_count
            batch = items(value)
            if (
                (not batch and len(result) != expected)
                or any(v.id in {r.id for r in result} for v in batch)
                or len({v.id for v in batch}) != len(batch)
            ):
                raise AuthDenied("AUTH_LIST_INVALID")
            result.extend(batch)
            if len(result) == expected:
                return tuple(result)
            if len(result) > expected:
                raise AuthDenied("AUTH_LIST_INVALID")
        raise AuthDenied("AUTH_LIST_LIMIT")

    def installations(self, token: SecretStr) -> tuple[Installation, ...]:
        return self._collect(
            InstallationPage, "user/installations", lambda p: p.installations, token
        )

    def repositories(
        self, token: SecretStr, installation_id: int
    ) -> tuple[GitHubRepository, ...]:
        if (
            type(installation_id) is not int
            or not 1 <= installation_id <= 9223372036854775807
        ):
            raise AuthDenied("AUTH_INPUT_INVALID")
        return self._collect(
            RepositoryPage,
            f"user/installations/{installation_id}/repositories",
            lambda p: p.repositories,
            token,
        )

    def repository_page(
        self, token: SecretStr, installation_id: int, page: int
    ) -> tuple[GitHubRepository, ...]:
        """Fetch one bounded page, never enumerate an entire account implicitly."""
        if (
            type(page) is not int
            or not 1 <= page <= 20000
            or type(installation_id) is not int
            or not 1 <= installation_id <= 9223372036854775807
        ):
            raise AuthDenied("AUTH_INPUT_INVALID")
        value = self._request(
            LargeRepositoryPage,
            "GET",
            f"https://api.github.com/user/installations/{installation_id}/repositories"
            f"?per_page=50&page={page}",
            token=token,
            max_bytes=524288,
        )
        expected = max(0, min(50, value.total_count - (page - 1) * 50))
        if (
            len(value.repositories) != expected
            or len({v.id for v in value.repositories}) != expected
        ):
            raise AuthDenied("AUTH_LIST_CHANGED")
        return tuple(value.repositories)

    def authorize(
        self,
        token: SecretStr,
        installation_id: int,
        repository_id: int,
        *,
        write: bool = False,
        page: int | None = None,
    ) -> GitHubRepository:
        installation = next(
            (v for v in self.installations(token) if v.id == installation_id), None
        )
        if (
            installation is None
            or installation.suspended_at is not None
            or installation.permissions.contents == "none"
            or (
                write
                and (
                    installation.permissions.contents != "write"
                    or installation.permissions.pull_requests != "write"
                )
            )
        ):
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        repo = next(
            (
                v
                for v in (
                    self.repositories(token, installation_id)
                    if page is None
                    else self.repository_page(token, installation_id, page)
                )
                if v.id == repository_id
            ),
            None,
        )
        if (
            repo is None
            or repo.disabled
            or not repo.permissions.pull
            or (write and (not repo.permissions.push or repo.archived))
        ):
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        return repo

    def resolve_head(self, token: SecretStr, repo: GitHubRepository) -> str:
        """Resolve a freshly authorized default HEAD to an immutable source SHA."""
        return (
            self._request(
                HeadCommits,
                "GET",
                f"https://api.github.com/repos/{repo.full_name}/commits?per_page=1",
                token=token,
            )
            .root[0]
            .sha
        )

    def verify_commit(self, token: SecretStr, full_name: str, commit: str) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", full_name
        ) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise AuthDenied("AUTH_INPUT_INVALID")
        record = self._request(
            CommitStamp,
            "GET",
            f"https://api.github.com/repos/{full_name}/git/commits/{commit}",
            token=token,
        )
        if record.sha != commit:
            raise AuthDenied("AUTH_COMMIT_DENIED")
