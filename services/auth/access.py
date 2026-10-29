"""Current user/install/repository checks plus narrowly scoped consent registration."""

import hmac
import re
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import CommitSHA
from services.api.app import LocalAuth, create_app
from services.auth.app import create_auth_app
from services.auth.github import AuthDenied
from services.auth.installation import WriteScope
from services.auth.journal import AuthJournal, SessionUser
from services.auth.repositories import (
    GitHubRepository,
    Installation,
    Number,
    RepositoryClient,
)
from services.auth.settings import GitHubAuthSettings
from services.persistence.store import Store, lock_repository_consent

ProtocolNumber = Annotated[int, Field(ge=1, le=9223372036854775807)]


def approved_repository_paths(include_checkout: bool) -> tuple[str, ...]:
    """Return the fixed consented path set; checkout requires a separate opt-in."""
    if type(include_checkout) is not bool:
        raise ValueError("AUTH_PATH_CONSENT_INVALID")
    base = (
        "src/",
        "app/",
        "tests/",
        "README.md",
        "pyproject.toml",
        "requirements.txt",
    )
    return base + (("checkout/",) if include_checkout else ())


class ConnectRepository(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    installation_id: Number
    repository_id: Number
    source_commit: CommitSHA
    consent: bool = Field(strict=True)
    private_access_approved: bool = Field(default=False, strict=True)
    include_checkout: bool = Field(default=False, strict=True)
    repository_page: int | None = Field(default=None, strict=True, ge=1, le=20000)


class AuthAccess:
    """Auth-service authority injected separately from execution-worker DB access."""

    def __init__(
        self,
        journal: AuthJournal,
        settings: GitHubAuthSettings,
        client_factory: Callable[[], RepositoryClient],
        *,
        allow_test: bool = False,
    ) -> None:
        self.journal, self.settings, self.client_factory = (
            journal,
            settings,
            client_factory,
        )
        self.allow_test = allow_test is True

    def identify(self, request: Request) -> UUID:
        raw = SecretStr(request.cookies.get(self.settings.session_cookie, ""))
        user = self.journal.session(raw, allow_test=self.allow_test)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-csrf-token", "")
            if (
                request.headers.get("origin")
                not in {self.settings.public_origin, self.settings.web_origin}
                or not re.fullmatch(r"[a-f0-9]{64}", token)
                or not hmac.compare_digest(
                    token, self.journal.vault.digest("csrf", raw)
                )
            ):
                self.journal.denial("AUTH_CSRF_DENIED")
                raise AuthDenied("AUTH_CSRF_DENIED")
        return user.id

    def _provider(self, client: RepositoryClient) -> None:
        if client.settings != self.settings or (
            client.origin == "TEST_TRANSPORT" and not self.allow_test
        ):
            raise AuthDenied("AUTH_PROVIDER_CONFIG_INVALID")

    def installations(self, owner: UUID) -> tuple[Installation, ...]:
        token = self.journal.credential(owner)
        with self.client_factory() as client:
            self._provider(client)
            return tuple(
                v for v in client.installations(token) if v.suspended_at is None
            )

    def repositories(
        self, owner: UUID, installation_id: int, page: int | None = None
    ) -> tuple[GitHubRepository, ...]:
        token = self.journal.credential(owner)
        with self.client_factory() as client:
            self._provider(client)
            if not any(
                v.id == installation_id and v.suspended_at is None
                for v in client.installations(token)
            ):
                raise AuthDenied("AUTH_REPOSITORY_DENIED")
            return tuple(
                v
                for v in (
                    client.repositories(token, installation_id)
                    if page is None
                    else client.repository_page(token, installation_id, page)
                )
                if page is not None or (v.permissions.pull and not v.disabled)
            )

    def head(
        self, owner: UUID, installation_id: int, repository_id: int, page: int
    ) -> str:
        token = self.journal.credential(owner)
        with self.client_factory() as client:
            self._provider(client)
            repo = client.authorize(token, installation_id, repository_id, page=page)
            return client.resolve_head(token, repo)

    def connect(self, user: SessionUser, payload: ConnectRepository) -> RepositoryGrant:
        if not payload.consent:
            raise AuthDenied("AUTH_CONSENT_REQUIRED")
        token = self.journal.credential(user.id)
        with self.client_factory() as client:
            self._provider(client)
            repo = client.authorize(
                token,
                payload.installation_id,
                payload.repository_id,
                page=payload.repository_page,
            )
            if repo.private and not payload.private_access_approved:
                raise AuthDenied("AUTH_PRIVATE_CONSENT_REQUIRED")
            client.verify_commit(token, repo.full_name, payload.source_commit)
            origin = client.origin
        try:
            grant = RepositoryGrant(
                repository_id=uuid4(),
                owner_id=user.id,
                remote_url="https://github.com/" + repo.full_name,
                commits=(payload.source_commit,),
                approved_paths=approved_repository_paths(payload.include_checkout),
                consent=True,
                private=repo.private,
                private_access_approved=payload.private_access_approved,
            )
        except ValueError:
            raise AuthDenied("AUTH_REPOSITORY_DENIED") from None
        lock = int.from_bytes(
            sha256(f"{user.id}:{repo.id}:{payload.source_commit}".encode()).digest()[
                :8
            ],
            "big",
            signed=True,
        )
        with self.journal.store._connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            existing = c.execute(
                """SELECT x.repository_id,g.grant_json,x.installation_id,x.origin,
                x.repository_page
                FROM auth_repository_connections x JOIN repository_grants g
                ON g.id=x.repository_id
                WHERE x.user_id=%s AND x.github_repository_id=%s AND x.source_commit=%s
                AND x.revoked_at IS NULL FOR UPDATE""",
                (user.id, repo.id, payload.source_commit),
            ).fetchone()
            if existing is not None:
                cached = RepositoryGrant.model_validate(existing["grant_json"])
                expected = grant.model_copy(
                    update={"repository_id": cached.repository_id}
                )
                if (
                    cached != expected
                    or existing["installation_id"] != payload.installation_id
                    or existing["origin"] != origin
                ):
                    raise AuthDenied("AUTH_CONNECTION_CONFLICT")
                # A fresh membership check may relocate this repository to a new
                # provider page; the immutable source/grant remains identical.
                c.execute(
                    "UPDATE auth_repository_connections SET repository_page=%s "
                    "WHERE repository_id=%s AND user_id=%s",
                    (payload.repository_page, cached.repository_id, user.id),
                )
                return cached
            c.execute(
                "INSERT INTO repository_grants(id,owner_id,grant_json) "
                "VALUES(%s,%s,%s)",
                (grant.repository_id, user.id, Jsonb(grant.model_dump(mode="json"))),
            )
            c.execute(
                """INSERT INTO auth_repository_connections(repository_id,user_id,
                github_repository_id,installation_id,full_name,source_commit,origin,repository_page)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    grant.repository_id,
                    user.id,
                    repo.id,
                    payload.installation_id,
                    repo.full_name,
                    payload.source_commit,
                    origin,
                    payload.repository_page,
                ),
            )
        return grant

    def disconnect(self, owner: UUID, repository_id: UUID) -> None:
        """Revoke this owner's connection and execution consent atomically.

        No provider call is required to withdraw consent. Existing worker checks
        observe the same grant; reconnect creates a new immutable identity.
        """
        with self.journal.store._connection() as c:
            lock_repository_consent(c, repository_id)
            row = c.execute(
                "SELECT repository_id FROM auth_repository_connections "
                "WHERE repository_id=%s AND user_id=%s FOR UPDATE",
                (repository_id, owner),
            ).fetchone()
            if row is None:
                raise AuthDenied("AUTH_REPOSITORY_DENIED")
            c.execute(
                "UPDATE repository_grants SET grant_json="
                "jsonb_set(grant_json,'{consent}','false'::jsonb) "
                "WHERE id=%s AND owner_id=%s",
                (repository_id, owner),
            )
            c.execute(
                "UPDATE auth_repository_connections "
                "SET revoked_at=COALESCE(revoked_at,clock_timestamp()) "
                "WHERE repository_id=%s AND user_id=%s",
                (repository_id, owner),
            )

    def authorize(
        self, owner: UUID, repository_id: UUID, *, write: bool = False
    ) -> None:
        with self.journal.store._connection() as c:
            row = c.execute(
                """SELECT installation_id,github_repository_id,full_name,
                source_commit,origin,repository_page FROM auth_repository_connections
                WHERE repository_id=%s
                AND user_id=%s AND revoked_at IS NULL""",
                (repository_id, owner),
            ).fetchone()
        if row is None or (row["origin"] == "TEST_TRANSPORT" and not self.allow_test):
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        grant = self.journal.store.repository(repository_id, owner)
        if (
            not grant.consent
            or grant.remote_url != "https://github.com/" + str(row["full_name"])
            or grant.commits != (row["source_commit"],)
        ):
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        token = self.journal.credential(owner)
        with self.client_factory() as client:
            self._provider(client)
            repo = client.authorize(
                token,
                int(str(row["installation_id"])),
                int(str(row["github_repository_id"])),
                write=write,
                page=None
                if row["repository_page"] is None
                else int(str(row["repository_page"])),
            )
            if repo.full_name != row["full_name"] or repo.private != grant.private:
                raise AuthDenied("AUTH_REPOSITORY_DENIED")

    def write_scope(self, owner: UUID, repository_id: UUID) -> WriteScope:
        """Resolve mint scope from a current write-authorized saved connection."""
        self.authorize(owner, repository_id, write=True)
        with self.journal.store._connection() as c:
            row = c.execute(
                "SELECT installation_id,github_repository_id,full_name FROM "
                "auth_repository_connections WHERE repository_id=%s AND user_id=%s "
                "AND revoked_at IS NULL",
                (repository_id, owner),
            ).fetchone()
        if row is None:
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        return WriteScope(
            owner_id=owner,
            repository_id=repository_id,
            installation_id=int(str(row["installation_id"])),
            github_repository_id=int(str(row["github_repository_id"])),
            full_name=str(row["full_name"]),
        )


def create_session_app(
    api_store: Store,
    journal: AuthJournal,
    settings: GitHubAuthSettings,
    client_factory: Callable[[], RepositoryClient],
    access: AuthAccess,
    *,
    allow_test: bool = False,
    approval_target_branch: str | None = None,
) -> FastAPI:
    """Compose current identity with existing owner checks; never bearer fallback."""
    if (
        access.journal is not journal
        or access.settings != settings
        or access.allow_test != allow_test
    ):
        raise AuthDenied("AUTH_PROVIDER_CONFIG_INVALID")
    core = create_app(
        api_store,
        LocalAuth(),
        identity_provider=access.identify,
        repository_authorizer=access.authorize,
    )
    app = create_auth_app(
        journal, settings, client_factory, allow_test=allow_test, api_app=core
    )
    if approval_target_branch is not None:
        from services.auth.approval import install_review_routes

        install_review_routes(
            app, api_store, access, target_branch=approval_target_branch
        )

    @app.get("/api/v1/github/installations")
    def installations(request: Request) -> tuple[Installation, ...]:
        return access.installations(access.identify(request))

    @app.get("/api/v1/github/repositories")
    def repositories(
        request: Request,
        installation_id: ProtocolNumber,
        page: Annotated[int | None, Field(ge=1, le=20000)] = None,
    ) -> tuple[GitHubRepository, ...]:
        return access.repositories(access.identify(request), installation_id, page)

    @app.get("/api/v1/github/head")
    def head(
        request: Request,
        installation_id: ProtocolNumber,
        repository_id: ProtocolNumber,
        page: Annotated[int, Field(ge=1, le=20000)] = 1,
    ) -> dict[str, str]:
        return {
            "sha": access.head(
                access.identify(request), installation_id, repository_id, page
            )
        }

    @app.post("/api/v1/repositories/{repo_id}/connect")
    def connect(
        repo_id: ProtocolNumber, payload: ConnectRepository, request: Request
    ) -> RepositoryGrant:
        owner = access.identify(request)
        if repo_id != payload.repository_id:
            raise AuthDenied("AUTH_REPOSITORY_DENIED")
        user = journal.session(
            SecretStr(request.cookies.get(settings.session_cookie, "")),
            allow_test=allow_test,
        )
        if user.id != owner:
            raise AuthDenied("AUTH_REQUIRED")
        return access.connect(user, payload)

    @app.post("/api/v1/repositories/{repository_id}/disconnect", status_code=204)
    def disconnect(repository_id: UUID, request: Request) -> None:
        access.disconnect(access.identify(request), repository_id)

    return app
