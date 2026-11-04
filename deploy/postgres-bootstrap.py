"""One-shot private Azure PostgreSQL bootstrap for the approved demo deploy.

This file is passed as a fixed ``python -c`` payload to a single-attempt
Container Apps Job. It fails closed if the database or any dedicated role
already exists; it never drops or resets existing PostgreSQL objects.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import quote

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

DATABASE = "replayforge_demo"
MIGRATOR = "rf_migrator"
API_ROLE = "rf_runtime_api"
AUTH_ROLE = "rf_runtime_auth"
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("required configuration missing")
    return value


def connection_string(*, host: str, user: str, password: str, database: str) -> str:
    return make_conninfo(
        host=host,
        port=5432,
        dbname=database,
        user=user,
        password=password,
        sslmode="verify-full",
        sslrootcert=CA_BUNDLE,
        connect_timeout=10,
    )


def migration_url(*, host: str, user: str, password: str) -> str:
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:5432/{DATABASE}?sslmode=verify-full"
        f"&sslrootcert={quote(CA_BUNDLE, safe='')}"
    )


def fail(message: str, code: int) -> int:
    print(message, flush=True)
    return code


def main() -> int:
    host = required("PG_HOST")
    admin_password = required("PG_ADMIN_PASSWORD")
    role_passwords = {
        MIGRATOR: required("RF_MIGRATOR_PASSWORD"),
        API_ROLE: required("RF_API_PASSWORD"),
        AUTH_ROLE: required("RF_AUTH_PASSWORD"),
    }

    # Check all names before the first mutation. This job is never a reset tool.
    with psycopg.connect(
        connection_string(
            host=host,
            user="rf_pg_admin",
            password=admin_password,
            database="postgres",
        ),
        autocommit=True,
    ) as admin:
        database_exists = admin.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)",
            (DATABASE,),
        ).fetchone()[0]
        role_rows = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            (list(role_passwords),),
        ).fetchall()
        if database_exists or role_rows:
            return fail("BOOTSTRAP_REFUSED preexisting_database_or_role", 10)

        for role, password in role_passwords.items():
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(DATABASE)
            )
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}, {}").format(
                sql.Identifier(DATABASE),
                sql.Identifier(MIGRATOR),
                sql.Identifier(API_ROLE),
                sql.Identifier(AUTH_ROLE),
            )
        )

    # PostgreSQL 15+ no longer grants CREATE on the public schema to PUBLIC.
    # The dedicated migration role must receive schema rights before Alembic
    # attempts to create alembic_version or the first application table.
    with psycopg.connect(
        connection_string(
            host=host,
            user="rf_pg_admin",
            password=admin_password,
            database=DATABASE,
        ),
        autocommit=True,
    ) as admin:
        admin.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(MIGRATOR)
            )
        )
        admin.execute(
            sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC, {}, {}").format(
                sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE)
            )
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(
                sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE)
            )
        )

    migration_env = os.environ.copy()
    migration_env["REPLAYFORGE_MIGRATION_DATABASE_URL"] = migration_url(
        host=host,
        user=MIGRATOR,
        password=role_passwords[MIGRATOR],
    )
    migration = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd="/app",
        env=migration_env,
        capture_output=True,
        text=True,
        timeout=1350,
        check=False,
    )
    if migration.returncode != 0:
        # Do not put driver/Alembic output in job logs; it can contain DSNs.
        return fail(f"BOOTSTRAP_FAILED migration_exit={migration.returncode}", 20)

    admin_dsn = connection_string(
        host=host,
        user="rf_pg_admin",
        password=admin_password,
        database=DATABASE,
    )
    api_dsn = connection_string(
        host=host,
        user=API_ROLE,
        password=role_passwords[API_ROLE],
        database=DATABASE,
    )
    auth_dsn = connection_string(
        host=host,
        user=AUTH_ROLE,
        password=role_passwords[AUTH_ROLE],
        database=DATABASE,
    )

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(DATABASE),
                sql.Identifier(API_ROLE),
                sql.Identifier(AUTH_ROLE),
            )
        )
        admin.execute(
            sql.SQL(
                "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, {}, {}"
            ).format(sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE))
        )
        admin.execute(
            sql.SQL(
                "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, {}, {}"
            ).format(sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE))
        )

        tables = admin.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
        if not tables or ("alembic_version",) not in tables:
            return fail("BOOTSTRAP_FAILED expected_schema_missing", 21)

        for (table,) in tables:
            identifier = sql.Identifier("public", table)
            if table == "alembic_version":
                admin.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )
            elif table.startswith("auth_"):
                if table == "auth_security_events":
                    permissions = "SELECT, INSERT"
                else:
                    permissions = "SELECT, INSERT, UPDATE"
                admin.execute(
                    sql.SQL("GRANT " + permissions + " ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(AUTH_ROLE)
                    )
                )
            elif table == "repository_grants":
                admin.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(AUTH_ROLE)
                    )
                )
            else:
                admin.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )

        sequences = admin.execute(
            """SELECT s.relname, t.relname
               FROM pg_class s
               JOIN pg_namespace n ON n.oid=s.relnamespace
               JOIN pg_depend d ON d.objid=s.oid
                 AND d.classid='pg_class'::regclass
                 AND d.refclassid='pg_class'::regclass
                 AND d.deptype IN ('a','i')
               JOIN pg_class t ON t.oid=d.refobjid
               WHERE n.nspname='public' AND s.relkind='S'
               ORDER BY s.relname"""
        ).fetchall()
        for sequence, table in sequences:
            roles = (
                (AUTH_ROLE,)
                if table.startswith("auth_") or table == "repository_grants"
                else (API_ROLE,)
            )
            for role in roles:
                admin.execute(
                    sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(
                        sql.Identifier("public", sequence), sql.Identifier(role)
                    )
                )

        revision = admin.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision != ("0036",):
            return fail("BOOTSTRAP_FAILED unexpected_revision", 22)

    expected = {
        API_ROLE: (False, False, False, False, True, False, False),
        AUTH_ROLE: (False, False, False, True, False, True, False),
    }
    for role, dsn, expected_row in (
        (API_ROLE, api_dsn, expected[API_ROLE]),
        (AUTH_ROLE, auth_dsn, expected[AUTH_ROLE]),
    ):
        with psycopg.connect(dsn, connect_timeout=10) as connection:
            row = connection.execute(
                """SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                   has_table_privilege(current_user,'repository_grants','INSERT'),
                   has_table_privilege(current_user,'runs','INSERT'),
                   has_table_privilege(current_user,'auth_credentials','SELECT'),
                   pg_has_role(current_user,(SELECT relowner FROM pg_class
                     WHERE oid='auth_users'::regclass),'MEMBER')
                   FROM pg_roles r WHERE r.rolname=current_user"""
            ).fetchone()
            tls = connection.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone()
            if row != expected_row or tls != (True,):
                return fail(f"BOOTSTRAP_FAILED role_contract role={role}", 23)

    print(
        "BOOTSTRAP_OK database=replayforge_demo revision=0036 "
        "tls=verified roles=verified",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # Safe class only; never serialize driver details.
        print(f"BOOTSTRAP_FAILED error_class={type(exc).__name__}", flush=True)
        raise SystemExit(1) from None
