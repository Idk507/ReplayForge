"""Explicit loopback-only development startup; no provider credentials or workers."""

import json
import os
from collections.abc import Mapping
from uuid import UUID

import psycopg
from fastapi import FastAPI
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from services.api.app import LocalAuth, create_app
from services.persistence.store import Store


class LocalConfigError(Exception):
    """Safe startup code without environment values."""


class LocalServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    dsn: SecretStr
    auth: LocalAuth

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "LocalServerConfig":
        values = os.environ if values is None else values
        try:
            raw = values.get("REPLAYFORGE_DATABASE_URL", "")
            if not raw or len(raw) > 4096:
                raise ValueError
            info = conninfo_to_dict(raw)
            if (
                info.get("host") not in {"localhost", "127.0.0.1", "::1"}
                or not all(info.get(k) for k in ("dbname", "user", "password"))
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
            auth = LocalAuth(
                token=SecretStr(values.get("REPLAYFORGE_API_TOKEN", "")),
                owner_id=UUID(values.get("REPLAYFORGE_OWNER_ID", "")),
                approved_repositories=tuple(
                    UUID(value)
                    for value in json.loads(
                        values.get("REPLAYFORGE_APPROVED_REPOSITORIES", "[]")
                    )
                ),
            )
            return cls(dsn=SecretStr(make_conninfo("", **info)), auth=auth)
        except (ValueError, TypeError, ValidationError, psycopg.Error):
            raise LocalConfigError("LOCAL_API_CONFIG_INVALID") from None


def create_from_env() -> FastAPI:
    """Uvicorn factory; operator starts with --host 127.0.0.1, runtime role only."""
    config = LocalServerConfig.from_env()
    try:
        with psycopg.connect(config.dsn.get_secret_value()) as conn:
            role = conn.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,"
                "has_table_privilege(current_user,'repository_grants','INSERT') "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role is None or any(role):
                raise LocalConfigError("LOCAL_API_ROLE_DENIED")
    except psycopg.Error:
        raise LocalConfigError("LOCAL_API_DATABASE_UNAVAILABLE") from None
    return create_app(Store(config.dsn.get_secret_value()), config.auth)
