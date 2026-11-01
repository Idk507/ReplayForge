"""Operator tool: list or explicitly reconcile expired collection claims.

Offline by default; ``--run`` is required for any database access. Never sends a
collection request. The DSN comes from REPLAYFORGE_RECONCILE_DATABASE_URL (a
privileged operator role, never the worker role). Requeue re-checks durable
repository consent here and the live installation again at the next dispatch.
"""

import argparse
import json
import os
import re
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

from services.persistence.collection_journal import CollectionJournal
from services.persistence.store import Store, StoreError


def durable_consent(store: Store):  # type: ignore[no-untyped-def]
    """Fail unless the owner's connection is unrevoked and consent is recorded."""

    def check(owner: UUID, repository_id: UUID) -> None:
        with store._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM auth_repository_connections WHERE repository_id=%s "
                "AND user_id=%s AND revoked_at IS NULL",
                (repository_id, owner),
            ).fetchone()
        if row is None or not store.repository(repository_id, owner).consent:
            raise PermissionError("CONSENT_NOT_CURRENT")

    return check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--list", action="store_true", dest="listing")
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--owner-id", type=UUID)
    parser.add_argument("--action", choices=["requeue", "fail"])
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    parser.add_argument("--accept-read-only-replay", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    if args.listing == bool(args.action):
        parser.error("choose exactly one of --list or --action")
    if args.action and not (
        args.run_id and args.owner_id and args.operator and args.reason
    ):
        parser.error("--action needs --run-id --owner-id --operator --reason")
    dsn = os.environ.get("REPLAYFORGE_RECONCILE_DATABASE_URL", "")
    try:
        info = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        info = {}
    if not info.get("host") or not info.get("user") or not info.get("dbname"):
        print(json.dumps({"status": "STOPPED", "reason": "RECONCILE_DSN_INVALID"}))
        return 1
    store = Store(dsn)
    journal = CollectionJournal(store)
    try:
        if args.listing:
            for obs in journal.candidates():
                print(obs.model_dump_json())
            return 0
        result = journal.reconcile(
            args.run_id,
            args.owner_id,
            args.action.upper(),
            operator=args.operator,
            reason=args.reason,
            authorize=durable_consent(store),
            accept_read_only_replay=args.accept_read_only_replay,
        )
        print(result.model_dump_json())
        return 0
    except StoreError as error:
        code = str(error)
        print(
            json.dumps(
                {
                    "status": "STOPPED",
                    "reason": code
                    if re.fullmatch(r"[A-Z_:]{1,96}", code)
                    else "RECONCILE_FAILED",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
