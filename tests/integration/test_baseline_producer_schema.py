"""Schema constraints for the future durable baseline producer."""

import os
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from services.runner.receipts import plan_ref
from tests.integration import test_postgres
from tests.integration.test_baseline_delivery import prepared

store = test_postgres.store


def test_baseline_producer_tables_are_append_only_and_bound_to_submission(store):
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN "
            "('baseline_preparations','baseline_produced_receipts') ORDER BY table_name"
        ).fetchall()
        assert [row[0] for row in rows] == [
            "baseline_preparations",
            "baseline_produced_receipts",
        ]
        for table in ("baseline_preparations", "baseline_produced_receipts"):
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
            ):
                allowed = conn.execute(
                    "SELECT has_table_privilege(%s,%s,%s)",
                    (os.environ["REPLAYFORGE_TEST_ROLE"], table, privilege),
                ).fetchone()[0]
                assert allowed is expected

    with psycopg.connect(store.dsn) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO baseline_preparations "
                "(execution_id,digest,payload,expires_at) "
                "VALUES (%s,%s,%s,clock_timestamp()+interval '1 hour')",
                (uuid4(), "a" * 64, b"{}"),
            )


def test_nonempty_baseline_producer_ledger_refuses_downgrade(store):
    _, _, _, _, action, _, _, _ = prepared(store)
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO baseline_submissions "
            "(execution_id,plan_ref,capability_ref,runner_id,configuration_ref,"
            "origin,expires_at) VALUES (%s,%s,%s,%s,%s,'TEST_TRANSPORT',"
            "clock_timestamp()+interval '1 hour')",
            (
                action.execution_id,
                plan_ref(action.plan),
                "sha256:" + "a" * 64,
                uuid4(),
                "sha256:" + "b" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO baseline_preparations "
            "(execution_id,digest,payload,expires_at) "
            "VALUES (%s,%s,%s,clock_timestamp()+interval '1 hour')",
            (action.execution_id, "a" * 64, b"{}"),
        )
    with pytest.raises(
        RuntimeError, match="ARCHIVE_BASELINE_PRODUCER_BEFORE_DOWNGRADE"
    ):
        command.downgrade(Config("alembic.ini"), "0031")
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0032"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM baseline_preparations WHERE execution_id=%s",
                (action.execution_id,),
            ).fetchone()[0]
            == 1
        )
