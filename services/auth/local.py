"""Explicit loopback GitHub-session startup; no provider I/O at construction."""

import os
from collections.abc import Mapping

import psycopg
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from services.auth.access import AuthAccess, create_session_app
from services.auth.github import AuthDenied
from services.auth.journal import AuthJournal, AuthVault
from services.auth.repositories import RepositoryClient
from services.auth.settings import GitHubAuthSettings
from services.persistence.store import Store


class AuthLocalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    api_dsn: SecretStr
    auth_dsn: SecretStr
    settings: GitHubAuthSettings
    encryption_key: SecretBytes
    hash_key: SecretBytes
    allow_live: bool
    collection_key: SecretStr | None = None

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "AuthLocalConfig":
        values = os.environ if values is None else values
        try:

            def dsn(name: str) -> tuple[SecretStr, dict[str, str | int | None]]:
                raw = values.get(name, "")
                if not 1 <= len(raw) <= 4096:
                    raise ValueError
                info = conninfo_to_dict(raw)
                if (
                    info.get("host") not in {"127.0.0.1", "localhost", "::1"}
                    or not all(info.get(v) for v in ("user", "password", "dbname"))
                    or set(info)
                    - {
                        "host",
                        "port",
                        "dbname",
                        "user",
                        "password",
                        "sslmode",
                        "connect_timeout",
                    }
                ):
                    raise ValueError
                info["hostaddr"] = "::1" if info["host"] == "::1" else "127.0.0.1"
                info["port"] = info.get("port", "5432")
                info["connect_timeout"] = "5"
                info["options"] = "-c statement_timeout=5000 -c lock_timeout=3000"
                return SecretStr(make_conninfo("", **info)), info

            api, api_info = dsn("REPLAYFORGE_DATABASE_URL")
            auth, auth_info = dsn("REPLAYFORGE_AUTH_DATABASE_URL")
            if (
                any(api_info[k] != auth_info[k] for k in ("hostaddr", "port", "dbname"))
                or api_info["user"] == auth_info["user"]
            ):
                raise ValueError
            enabled = values.get("REPLAYFORGE_AUTH_ENABLE_GITHUB", "false")
            if enabled not in {"true", "false"}:
                raise ValueError
            encryption = bytes.fromhex(
                values.get("REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX", "")
            )
            hashing = bytes.fromhex(values.get("REPLAYFORGE_AUTH_HMAC_KEY_HEX", ""))
            AuthVault(SecretBytes(encryption), SecretBytes(hashing))
            collection = values.get("REPLAYFORGE_ENABLE_COLLECTION_BROKER", "false")
            if collection not in {"true", "false"}:
                raise ValueError
            collection_key = None
            if collection == "true":
                import re

                key = values.get("REPLAYFORGE_COLLECTION_WORKER_KEY", "")
                if enabled != "true" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
                    raise ValueError
                collection_key = SecretStr(key)
            return cls(
                collection_key=collection_key,
                api_dsn=api,
                auth_dsn=auth,
                settings=GitHubAuthSettings(
                    client_id=values.get("GITHUB_CLIENT_ID", ""),
                    client_secret=SecretStr(values.get("GITHUB_CLIENT_SECRET", "")),
                    public_origin=values.get(
                        "REPLAYFORGE_AUTH_API_ORIGIN", "http://127.0.0.1:8000"
                    ),
                    web_origin=values.get(
                        "REPLAYFORGE_AUTH_WEB_ORIGIN", "http://127.0.0.1:3000"
                    ),
                    development=True,
                ),
                encryption_key=SecretBytes(encryption),
                hash_key=SecretBytes(hashing),
                allow_live=enabled == "true",
            )
        except (ValueError, TypeError, psycopg.Error, AuthDenied):
            raise AuthDenied("AUTH_LOCAL_CONFIG_INVALID") from None


def _role(dsn: SecretStr, *, auth: bool) -> None:
    with psycopg.connect(dsn.get_secret_value(), connect_timeout=5) as c:
        row = c.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,
            has_table_privilege(current_user,'repository_grants','INSERT'),
            has_table_privilege(current_user,'runs','INSERT'),
            has_table_privilege(current_user,'auth_credentials','SELECT'),
            pg_has_role(current_user,(SELECT relowner FROM pg_class
                WHERE oid='auth_users'::regclass),'MEMBER')
            FROM pg_roles WHERE rolname=current_user""").fetchone()
        expected = (
            (False, False, False, True, False, True, False)
            if auth
            else (False, False, False, False, True, False, False)
        )
        if row != expected:
            raise AuthDenied("AUTH_DATABASE_ROLE_DENIED")


def create_from_env() -> FastAPI:
    """Run only explicitly with Uvicorn --host 127.0.0.1 --no-access-log.

    Separate restricted auth and execution roles on one local DB are mandatory.
    Startup does not exchange credentials, list repositories or create a PR.
    """
    config = AuthLocalConfig.from_env()
    try:
        _role(config.auth_dsn, auth=True)
        _role(config.api_dsn, auth=False)
    except psycopg.Error:
        raise AuthDenied("AUTH_DATABASE_UNAVAILABLE") from None
    journal = AuthJournal(
        Store(config.auth_dsn.get_secret_value()),
        AuthVault(config.encryption_key, config.hash_key),
    )

    def client() -> RepositoryClient:
        return RepositoryClient(config.settings, allow_live=config.allow_live)

    access = AuthAccess(journal, config.settings, client)
    app = create_session_app(
        Store(config.api_dsn.get_secret_value()),
        journal,
        config.settings,
        client,
        access,
    )

    if config.collection_key is not None:
        from services.auth.collection import CollectionBroker, install_routes

        install_routes(
            app,
            CollectionBroker(
                Store(config.api_dsn.get_secret_value()),
                access,
                config.collection_key,
                allow_live=config.allow_live,
            ),
        )
    return app
