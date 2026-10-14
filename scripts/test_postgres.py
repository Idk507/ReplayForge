"""Run real integration tests in a disposable local database; prompts securely."""

import argparse
import getpass
import os
import re
import secrets
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


def main() -> int:
    """Create only random task-owned resources; drop them even on test failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-name", default="m1-postgres-tests.xml")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}\.xml", args.evidence_name):
        parser.error("evidence-name must be a simple lowercase XML filename")
    password = getpass.getpass("Local postgres administrator password: ")
    suffix = uuid4().hex
    role, database = f"rf_test_{suffix}", f"rf_test_{suffix}"
    secret = secrets.token_urlsafe(32)
    runtime_role = f"rf_app_{suffix}"
    runtime_secret = secrets.token_urlsafe(32)
    admin_dsn = make_conninfo(
        host="127.0.0.1",
        port=5432,
        user="postgres",
        dbname="postgres",
        password=password,
        connect_timeout=5,
    )
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(role), sql.Literal(secret))
            )
            try:
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                try:
                    admin.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                        ).format(
                            sql.Identifier(runtime_role), sql.Literal(runtime_secret)
                        )
                    )
                    env = os.environ.copy()
                    env["REPLAYFORGE_MIGRATION_DATABASE_URL"] = (
                        f"postgresql://{role}:{secret}@127.0.0.1:5432/{database}"
                    )
                    env["REPLAYFORGE_TEST_DATABASE_URL"] = (
                        f"postgresql://{runtime_role}:{runtime_secret}"
                        f"@127.0.0.1:5432/{database}"
                    )
                    env["REPLAYFORGE_TEST_ROLE"] = runtime_role
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            f"--junitxml=docs/evidence/{args.evidence_name}",
                        ],
                        env=env,
                        timeout=180,
                        check=False,
                    )
                    return result.returncode
                finally:
                    admin.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(database)
                        )
                    )
                    admin.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(runtime_role)
                        )
                    )
            finally:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    except (psycopg.Error, subprocess.TimeoutExpired):
        print("DB_TEST_FAILURE: inspect local service; credentials omitted")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
