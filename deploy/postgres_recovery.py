"""One-shot, forward-only recovery for the isolated Azure demo database.

The script is sent as a fixed command to the already-approved private ACA Job.
It inspects before mutation, refuses unknown or inconsistent schema state, and
never changes database/role passwords or drops existing objects.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from psycopg.conninfo import make_conninfo

DATABASE = "replayforge_demo"
ADMIN_ROLE = "rf_pg_admin"
MIGRATOR = "rf_migrator"
API_ROLE = "rf_runtime_api"
AUTH_ROLE = "rf_runtime_auth"
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
EXPECTED_HEAD = "0036"
MIN_RECOVERABLE_REVISION = "0031"
REQUIRED_TABLES_AT_0031 = frozenset(
    {
        "alembic_version",
        "baseline_action_events",
        "baseline_submissions",
        "image_import_attempts",
        "image_import_events",
        "patched_materialization_intents",
        "replay_submissions",
        "verification_results",
    }
)
ROLE_FLAGS = (True, False, False, False, False, False, False)


class RecoveryRefused(RuntimeError):
    """Raised when read-only preflight cannot prove safe forward recovery."""


@dataclass(frozen=True)
class DatabaseState:
    revision: str | None
    tables: frozenset[str]
    relations: frozenset[str]
    relation_owner_mismatches: tuple[str, ...]
    user_schemas: tuple[str, ...]
    active_other_sessions: int
    migration_create: bool
    migration_usage: bool
    api_privileged_tables: int = 0
    auth_privileged_tables: int = 0
    api_privileged_sequences: int = 0
    auth_privileged_sequences: int = 0


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RecoveryRefused(f"CONFIG_MISSING name={name}")
    return value


def connection_string(*, host: str, user: str, password: str) -> str:
    return make_conninfo(
        host=host,
        port=5432,
        dbname=DATABASE,
        user=user,
        password=password,
        sslmode="verify-full",
        sslrootcert=CA_BUNDLE,
        connect_timeout=12,
        application_name="replayforge-db-recovery",
    )


def migration_url(*, host: str, password: str) -> str:
    return (
        f"postgresql://{quote(MIGRATOR, safe='')}:{quote(password, safe='')}"
        f"@{host}:5432/{DATABASE}?sslmode=verify-full"
        f"&sslrootcert={quote(CA_BUNDLE, safe='')}"
    )


def assess_database_state(state: DatabaseState) -> None:
    """Accept only pristine state or an intact 0031..0036 migration boundary."""
    if state.active_other_sessions:
        raise RecoveryRefused("PREFLIGHT_REFUSED active_database_sessions")
    if state.user_schemas != ("public",):
        raise RecoveryRefused("PREFLIGHT_REFUSED unexpected_schema_state")
    if state.relation_owner_mismatches:
        raise RecoveryRefused("PREFLIGHT_REFUSED unexpected_relation_owners")

    if state.revision is None:
        if state.relations:
            raise RecoveryRefused("PREFLIGHT_REFUSED unversioned_nonempty_schema")
        return

    if state.revision < MIN_RECOVERABLE_REVISION or state.revision > EXPECTED_HEAD:
        raise RecoveryRefused("PREFLIGHT_REFUSED revision_outside_recovery_window")
    if not REQUIRED_TABLES_AT_0031.issubset(state.tables):
        raise RecoveryRefused("PREFLIGHT_REFUSED incomplete_versioned_schema")


def assess_revision_objects(
    revision: str,
    *,
    tables: frozenset[str],
    columns: frozenset[tuple[str, str]],
    constraints: dict[str, str],
) -> None:
    """Refuse partial DDL that does not match the recorded Alembic boundary."""
    if revision < MIN_RECOVERABLE_REVISION or revision > EXPECTED_HEAD:
        raise RecoveryRefused("PREFLIGHT_REFUSED revision_outside_recovery_window")
    if not REQUIRED_TABLES_AT_0031.issubset(tables):
        raise RecoveryRefused("PREFLIGHT_REFUSED incomplete_versioned_schema")

    expected_preparation_tables = {
        "baseline_preparations",
        "baseline_produced_receipts",
    }
    present_preparation_tables = expected_preparation_tables.intersection(tables)
    if (
        "0032" <= revision and present_preparation_tables != expected_preparation_tables
    ) or (revision < "0032" and present_preparation_tables):
        raise RecoveryRefused("PREFLIGHT_REFUSED partial_baseline_producer_schema")
    preparation_constraint = constraints.get("baseline_action_events_kind_check", "")
    if not preparation_constraint:
        raise RecoveryRefused("PREFLIGHT_REFUSED baseline_kind_constraint_missing")
    if ("0032" <= revision) != ("PRODUCED_RECEIPT_RETAINED" in preparation_constraint):
        raise RecoveryRefused("PREFLIGHT_REFUSED baseline_producer_constraint_mismatch")

    upload_constraint = constraints.get("image_import_events_kind_check", "")
    if not upload_constraint:
        raise RecoveryRefused("PREFLIGHT_REFUSED image_import_constraint_missing")
    if ("0033" <= revision) != ("UPLOAD_INTENT" in upload_constraint):
        raise RecoveryRefused("PREFLIGHT_REFUSED image_preparation_constraint_mismatch")

    suite_constraints = (
        constraints.get("replay_submissions_phase_check", ""),
        constraints.get("patched_materialization_intents_phase_check", ""),
    )
    if any(not value for value in suite_constraints):
        raise RecoveryRefused("PREFLIGHT_REFUSED existing_suite_constraint_missing")
    suite_ready = all("EXISTING_SUITE" in value for value in suite_constraints)
    if ("0034" <= revision) != suite_ready:
        raise RecoveryRefused("PREFLIGHT_REFUSED existing_suite_constraint_mismatch")

    capability_columns = {
        ("baseline_submissions", "capability_payload"),
        ("replay_submissions", "capability_payload"),
    }
    present_capability_columns = capability_columns.intersection(columns)
    capability_constraints = {
        "baseline_capability_payload_size",
        "replay_capability_payload_size",
    }.intersection(constraints)
    if ("0035" <= revision and present_capability_columns != capability_columns) or (
        revision < "0035" and present_capability_columns
    ):
        raise RecoveryRefused("PREFLIGHT_REFUSED capability_payload_schema_mismatch")
    if ("0035" <= revision and len(capability_constraints) != 2) or (
        revision < "0035" and capability_constraints
    ):
        raise RecoveryRefused("PREFLIGHT_REFUSED capability_payload_schema_mismatch")

    envelope_columns = {
        ("verification_results", "envelope_version"),
        ("verification_results", "envelope_bytes"),
    }
    present_envelope_columns = envelope_columns.intersection(columns)
    envelope_constraints = {
        "verification_results_envelope_version_check",
        "verification_results_envelope_bytes_check",
        "verification_results_envelope_pair_check",
    }.intersection(constraints)
    if ("0036" <= revision and present_envelope_columns != envelope_columns) or (
        revision < "0036" and present_envelope_columns
    ):
        raise RecoveryRefused("PREFLIGHT_REFUSED envelope_schema_mismatch")
    if ("0036" <= revision and len(envelope_constraints) != 3) or (
        revision < "0036" and envelope_constraints
    ):
        raise RecoveryRefused("PREFLIGHT_REFUSED envelope_schema_mismatch")


def _roles_and_database(admin: psycopg.Connection[Any]) -> None:
    row = admin.execute(
        """SELECT current_database(), current_user,
                  COALESCE((SELECT ssl FROM pg_stat_ssl
                            WHERE pid=pg_backend_pid()), false),
                  has_schema_privilege(%s, 'public', 'USAGE'),
                  has_schema_privilege(%s, 'public', 'CREATE')""",
        (MIGRATOR, MIGRATOR),
    ).fetchone()
    if row is None or row[0] != DATABASE or row[1] != ADMIN_ROLE or row[2] is not True:
        raise RecoveryRefused("PREFLIGHT_REFUSED database_identity_or_tls")
    role_rows = admin.execute(
        """SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                  rolreplication, rolbypassrls, rolinherit
             FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname""",
        ([MIGRATOR, API_ROLE, AUTH_ROLE],),
    ).fetchall()
    by_name = {
        str(role[0]): tuple(bool(value) for value in role[1:]) for role in role_rows
    }
    if set(by_name) != {MIGRATOR, API_ROLE, AUTH_ROLE}:
        raise RecoveryRefused("PREFLIGHT_REFUSED expected_roles_missing")
    if any(by_name[name] != ROLE_FLAGS for name in by_name):
        raise RecoveryRefused("PREFLIGHT_REFUSED role_security_flags")


def _read_state(admin: psycopg.Connection[Any]) -> DatabaseState:
    """Inspect current schema/owners/leases using a read-only transaction."""
    with admin.transaction():
        admin.execute("SET TRANSACTION READ ONLY")
        _roles_and_database(admin)
        revision_row = admin.execute(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ).fetchone()
        if revision_row is None:
            raise RecoveryRefused("PREFLIGHT_REFUSED revision_query_empty")
        revision_table = bool(revision_row[0])
        revisions: list[str] = []
        if revision_table:
            revisions = [
                str(row[0])
                for row in admin.execute(
                    "SELECT version_num FROM public.alembic_version "
                    "ORDER BY version_num"
                ).fetchall()
            ]
            if len(revisions) != 1:
                raise RecoveryRefused("PREFLIGHT_REFUSED alembic_revision_cardinality")

        relation_rows = admin.execute(
            """SELECT c.relname, c.relkind, owner.rolname
                 FROM pg_class AS c
                 JOIN pg_namespace AS n ON n.oid=c.relnamespace
                 JOIN pg_roles AS owner ON owner.oid=c.relowner
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
                ORDER BY c.relname"""
        ).fetchall()
        tables = frozenset(
            str(name) for name, kind, _owner in relation_rows if kind in ("r", "p")
        )
        relations = frozenset(str(name) for name, _kind, _owner in relation_rows)
        owner_mismatches = tuple(
            sorted(
                str(name)
                for name, _kind, owner in relation_rows
                if str(owner) != MIGRATOR
            )
        )
        schemas = tuple(
            str(row[0])
            for row in admin.execute(
                """SELECT schema_name FROM information_schema.schemata
                     WHERE schema_name NOT LIKE 'pg_%'
                       AND schema_name <> 'information_schema'
                     ORDER BY schema_name"""
            ).fetchall()
        )
        active_row = admin.execute(
            """SELECT count(*) FROM pg_stat_activity
                WHERE datname=%s AND pid <> pg_backend_pid()
                  AND state IS DISTINCT FROM 'idle'""",
            (DATABASE,),
        ).fetchone()
        if active_row is None:
            raise RecoveryRefused("PREFLIGHT_REFUSED activity_query_empty")
        active = int(active_row[0])
        access = admin.execute(
            """SELECT has_schema_privilege(%s, 'public', 'USAGE'),
                      has_schema_privilege(%s, 'public', 'CREATE')""",
            (MIGRATOR, MIGRATOR),
        ).fetchone()
        if access is None:
            raise RecoveryRefused("PREFLIGHT_REFUSED schema_privilege_query_empty")
        table_privileges = admin.execute(
            """SELECT
                    count(*) FILTER (WHERE
                      has_table_privilege(%s, c.oid, 'SELECT') OR
                      has_table_privilege(%s, c.oid, 'INSERT') OR
                      has_table_privilege(%s, c.oid, 'UPDATE') OR
                      has_table_privilege(%s, c.oid, 'DELETE')),
                    count(*) FILTER (WHERE
                      has_table_privilege(%s, c.oid, 'SELECT') OR
                      has_table_privilege(%s, c.oid, 'INSERT') OR
                      has_table_privilege(%s, c.oid, 'UPDATE') OR
                      has_table_privilege(%s, c.oid, 'DELETE'))
                 FROM pg_class AS c
                 JOIN pg_namespace AS n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')""",
            (
                API_ROLE,
                API_ROLE,
                API_ROLE,
                API_ROLE,
                AUTH_ROLE,
                AUTH_ROLE,
                AUTH_ROLE,
                AUTH_ROLE,
            ),
        ).fetchone()
        sequence_privileges = admin.execute(
            """SELECT
                    count(*) FILTER (WHERE
                      has_sequence_privilege(%s, c.oid, 'USAGE') OR
                      has_sequence_privilege(%s, c.oid, 'SELECT') OR
                      has_sequence_privilege(%s, c.oid, 'UPDATE')),
                    count(*) FILTER (WHERE
                      has_sequence_privilege(%s, c.oid, 'USAGE') OR
                      has_sequence_privilege(%s, c.oid, 'SELECT') OR
                      has_sequence_privilege(%s, c.oid, 'UPDATE'))
                 FROM pg_class AS c
                 JOIN pg_namespace AS n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind='S'""",
            (
                API_ROLE,
                API_ROLE,
                API_ROLE,
                AUTH_ROLE,
                AUTH_ROLE,
                AUTH_ROLE,
            ),
        ).fetchone()
        if table_privileges is None or sequence_privileges is None:
            raise RecoveryRefused("PREFLIGHT_REFUSED role_privilege_query_empty")
        revision = revisions[0] if revisions else None
        if revision is not None:
            columns = frozenset(
                (str(table), str(column))
                for table, column in admin.execute(
                    """SELECT table_name, column_name
                         FROM information_schema.columns
                        WHERE table_schema='public'"""
                ).fetchall()
            )
            constraints = {
                str(name): str(definition)
                for name, definition in admin.execute(
                    """SELECT conname, pg_get_constraintdef(c.oid)
                         FROM pg_constraint AS c
                         JOIN pg_namespace AS n ON n.oid=c.connamespace
                        WHERE n.nspname='public' AND contype='c'"""
                ).fetchall()
            }
            assess_revision_objects(
                revision,
                tables=tables,
                columns=columns,
                constraints=constraints,
            )
        return DatabaseState(
            revision=revision,
            tables=tables,
            relations=relations,
            relation_owner_mismatches=owner_mismatches,
            user_schemas=schemas,
            active_other_sessions=active,
            migration_usage=bool(access[0]),
            migration_create=bool(access[1]),
            api_privileged_tables=int(table_privileges[0] or 0),
            auth_privileged_tables=int(table_privileges[1] or 0),
            api_privileged_sequences=int(sequence_privileges[0] or 0),
            auth_privileged_sequences=int(sequence_privileges[1] or 0),
        )


def _migration_chain(config_path: str = "/app/alembic.ini") -> list[str]:
    config = Config(config_path)
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if heads != [EXPECTED_HEAD]:
        raise RecoveryRefused("MIGRATION_REFUSED unexpected_alembic_heads")
    descending = list(scripts.walk_revisions(base="base", head="heads"))
    ascending = list(reversed(descending))
    for index, revision in enumerate(ascending):
        expected_parent = ascending[index - 1].revision if index else None
        if revision.down_revision != expected_parent:
            raise RecoveryRefused("MIGRATION_REFUSED non_linear_revision_graph")
    if not ascending or ascending[-1].revision != EXPECTED_HEAD:
        raise RecoveryRefused("MIGRATION_REFUSED head_mismatch")
    return [str(revision.revision) for revision in ascending]


def _set_migration_permissions(
    *, host: str, admin_password: str, migrator_password: str
) -> None:
    """Apply only the repository's established schema-level role grants."""
    with psycopg.connect(
        connection_string(host=host, user=ADMIN_ROLE, password=admin_password),
        autocommit=True,
    ) as admin:
        _roles_and_database(admin)
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
    # Confirm the migration credential itself can use the granted schema over
    # the same certificate-verified channel that Alembic will use.
    with psycopg.connect(
        connection_string(host=host, user=MIGRATOR, password=migrator_password)
    ) as migrator:
        row = migrator.execute(
            """SELECT current_database(), current_user,
                      has_schema_privilege(current_user, 'public', 'USAGE'),
                      has_schema_privilege(current_user, 'public', 'CREATE'),
                      COALESCE((SELECT ssl FROM pg_stat_ssl
                                WHERE pid=pg_backend_pid()), false)"""
        ).fetchone()
        if row != (DATABASE, MIGRATOR, True, True, True):
            raise RecoveryRefused("MIGRATION_REFUSED migration_role_contract")


def _safe_migration_error(exc: BaseException) -> tuple[str, str, str]:
    original = getattr(exc, "orig", None) or exc
    diagnostic = getattr(original, "diag", None)
    state = str(getattr(original, "sqlstate", "") or "")
    primary = str(getattr(diagnostic, "message_primary", "") or "")
    # Driver primary diagnostics contain schema/object names, not SQL params.
    # Still redact URL/password-shaped text defensively before emitting logs.
    primary = re.sub(r"(?i)postgres(?:ql)?://\S+", "[redacted-url]", primary)
    primary = re.sub(r"(?i)(password\s*[=:]\s*)\S+", r"\1[redacted]", primary)
    primary = " ".join(primary.split())[:240]
    return type(exc).__name__, state[:5], primary


def _grant_runtime_privileges(*, host: str, migrator_password: str) -> None:
    with psycopg.connect(
        connection_string(host=host, user=MIGRATOR, password=migrator_password),
        autocommit=True,
    ) as migrator:
        migrator.execute(
            sql.SQL(
                "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, {}, {}"
            ).format(sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE))
        )
        migrator.execute(
            sql.SQL(
                "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, {}, {}"
            ).format(sql.Identifier(API_ROLE), sql.Identifier(AUTH_ROLE))
        )
        tables = migrator.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "ORDER BY tablename"
        ).fetchall()
        if not tables or ("alembic_version",) not in tables:
            raise RecoveryRefused("GRANT_REFUSED expected_schema_missing")
        for (table_name,) in tables:
            identifier = sql.Identifier("public", str(table_name))
            if table_name == "alembic_version":
                migrator.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )
            elif str(table_name).startswith("auth_"):
                permissions = (
                    "SELECT, INSERT"
                    if table_name == "auth_security_events"
                    else "SELECT, INSERT, UPDATE"
                )
                migrator.execute(
                    sql.SQL("GRANT " + permissions + " ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(AUTH_ROLE)
                    )
                )
            elif table_name == "repository_grants":
                migrator.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )
                migrator.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(AUTH_ROLE)
                    )
                )
            else:
                migrator.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE ON TABLE {} TO {}").format(
                        identifier, sql.Identifier(API_ROLE)
                    )
                )

        sequences = migrator.execute(
            """SELECT seq_rel.relname, tbl_rel.relname
                 FROM pg_class AS seq_rel
                 JOIN pg_namespace AS n ON n.oid=seq_rel.relnamespace
                 JOIN pg_depend AS d ON d.objid=seq_rel.oid
                 JOIN pg_class AS tbl_rel ON tbl_rel.oid=d.refobjid
                WHERE n.nspname='public' AND seq_rel.relkind='S'
                ORDER BY seq_rel.relname"""
        ).fetchall()
        for sequence_name, table_name in sequences:
            roles = (
                (AUTH_ROLE,)
                if str(table_name).startswith("auth_")
                or table_name == "repository_grants"
                else (API_ROLE,)
            )
            for role in roles:
                migrator.execute(
                    sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(
                        sql.Identifier("public", str(sequence_name)),
                        sql.Identifier(role),
                    )
                )
        current = migrator.execute("SELECT version_num FROM alembic_version").fetchone()
        if current != (EXPECTED_HEAD,):
            raise RecoveryRefused("GRANT_REFUSED unexpected_final_revision")

    _validate_runtime_roles(
        host=host,
        api_password=require("RF_API_PASSWORD"),
        auth_password=require("RF_AUTH_PASSWORD"),
    )


def _validate_runtime_roles(
    *, host: str, api_password: str, auth_password: str
) -> None:
    expected = {
        API_ROLE: (False, False, False, False, True, False, False),
        AUTH_ROLE: (False, False, False, True, False, True, False),
    }
    for role, password, expected_row in (
        (API_ROLE, api_password, expected[API_ROLE]),
        (AUTH_ROLE, auth_password, expected[AUTH_ROLE]),
    ):
        with psycopg.connect(
            connection_string(host=host, user=role, password=password)
        ) as connection:
            row = connection.execute(
                """SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                          has_table_privilege(current_user,'repository_grants','INSERT'),
                          has_table_privilege(current_user,'runs','INSERT'),
                          has_table_privilege(current_user,'auth_credentials','SELECT'),
                          pg_has_role(current_user,(SELECT relowner FROM pg_class
                            WHERE oid='auth_users'::regclass),'MEMBER')
                     FROM pg_roles AS r WHERE r.rolname=current_user"""
            ).fetchone()
            tls = connection.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone()
            if row != expected_row or tls != (True,):
                raise RecoveryRefused(f"ROLE_CONTRACT_FAILED role={role}")
            if role == API_ROLE and connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() != (EXPECTED_HEAD,):
                raise RecoveryRefused(f"ROLE_REVISION_FAILED role={role}")


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        host = require("PG_HOST")
        admin_password = require("PG_ADMIN_PASSWORD")
        migrator_password = require("RF_MIGRATOR_PASSWORD")
        require("RF_API_PASSWORD")
        require("RF_AUTH_PASSWORD")

        # Initial observation is read-only. No grant, DDL, reset or cleanup has
        # occurred at this point.
        with psycopg.connect(
            connection_string(host=host, user=ADMIN_ROLE, password=admin_password)
        ) as admin:
            state = _read_state(admin)
        assess_database_state(state)
        print(
            "PREFLIGHT_OK "
            f"database={DATABASE} revision={state.revision or 'EMPTY'} "
            f"tables={len(state.tables)} schemas={len(state.user_schemas)} "
            f"migrator_create={str(state.migration_create).lower()} "
            f"migrator_usage={str(state.migration_usage).lower()} "
            f"api_acl_tables={state.api_privileged_tables} "
            f"auth_acl_tables={state.auth_privileged_tables} "
            f"api_acl_sequences={state.api_privileged_sequences} "
            f"auth_acl_sequences={state.auth_privileged_sequences} "
            "tls=verify-full roles=verified",
            flush=True,
        )

        chain = _migration_chain()
        current_index = chain.index(state.revision) if state.revision in chain else -1
        if state.revision is not None and current_index < chain.index(
            MIN_RECOVERABLE_REVISION
        ):
            raise RecoveryRefused("MIGRATION_REFUSED unsupported_start_revision")

        _set_migration_permissions(
            host=host,
            admin_password=admin_password,
            migrator_password=migrator_password,
        )

        os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"] = migration_url(
            host=host, password=migrator_password
        )
        config = Config("/app/alembic.ini")
        config.attributes["configure_logger"] = False
        for target in chain[current_index + 1 :]:
            print(f"MIGRATION_BEGIN revision={target}", flush=True)
            try:
                command.upgrade(config, target)
            except Exception as exc:
                error_class, sqlstate, primary = _safe_migration_error(exc)
                print(
                    "MIGRATION_FAILED "
                    f"revision={target} error_class={error_class} "
                    f"sqlstate={sqlstate or 'none'} primary={primary or 'unavailable'}",
                    flush=True,
                )
                return 20
            print(f"MIGRATION_OK revision={target}", flush=True)

        _grant_runtime_privileges(
            host=host,
            migrator_password=migrator_password,
        )
        print(
            "RECOVERY_OK database=replayforge_demo revision=0036 "
            "tls=verified roles=verified destructive_action=none",
            flush=True,
        )
        return 0
    except RecoveryRefused as exc:
        print(str(exc), flush=True)
        return 30
    except Exception as exc:
        # Never emit exception text, which may contain connection information.
        print(f"RECOVERY_FAILED error_class={type(exc).__name__}", flush=True)
        return 31
    finally:
        for name in (
            "PG_ADMIN_PASSWORD",
            "RF_MIGRATOR_PASSWORD",
            "RF_API_PASSWORD",
            "RF_AUTH_PASSWORD",
            "REPLAYFORGE_MIGRATION_DATABASE_URL",
        ):
            os.environ.pop(name, None)


if __name__ == "__main__":
    sys.exit(main())
