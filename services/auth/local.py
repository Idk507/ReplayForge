"""Explicit loopback GitHub-session startup; no provider I/O at construction."""

import os
from collections.abc import Mapping
from pathlib import Path

import psycopg
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
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
    approval_target_branch: str | None = None
    publication_key_path: str | None = None

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
            publication = values.get("REPLAYFORGE_ENABLE_GITHUB_PUBLICATION", "false")
            if publication not in {"true", "false"}:
                raise ValueError
            key_path = values.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
            branch = values.get("REPLAYFORGE_APPROVAL_TARGET_BRANCH") or None
            if publication == "true" and (
                enabled != "true"
                or not key_path
                or len(key_path) > 1024
                or branch is None
            ):
                raise ValueError
            return cls(
                approval_target_branch=branch,
                publication_key_path=key_path if publication == "true" else None,
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
        approval_target_branch=config.approval_target_branch,
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
    if config.publication_key_path is not None:
        from services.auth.installation import InstallationTokenBroker
        from services.auth.publication import install_publication_route

        try:
            path = Path(config.publication_key_path)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError
            with path.open("rb") as file:
                pem = file.read(16385)
            if len(pem) > 16384:
                raise ValueError
            private_key = load_pem_private_key(pem, password=None)
            if (
                not isinstance(private_key, RSAPrivateKey)
                or private_key.key_size < 2048
            ):
                raise ValueError
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            raise AuthDenied("AUTH_INSTALLATION_CONFIG_INVALID") from None

        def broker() -> InstallationTokenBroker:
            return InstallationTokenBroker(
                config.settings.client_id, private_key, allow_live=True
            )

        assert config.approval_target_branch is not None
        install_publication_route(
            app,
            Store(config.api_dsn.get_secret_value()),
            access,
            broker,
            target_branch=config.approval_target_branch,
            allow_live_writes=True,
        )
    return app
