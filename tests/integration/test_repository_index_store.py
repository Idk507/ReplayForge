"""Retained repository index: atomic with context, owner-scoped, tamper-evident."""

import os
from uuid import uuid4

import psycopg
import pytest

from services.context.index import RepositoryIndex
from services.orchestrator.source_broker import dispatch_once
from services.persistence.store import Store, StoreError
from tests.integration import test_postgres
from tests.integration.test_collection_reconciliation import (
    broker_client,
    claimed,
    requeue_job,
)

store = test_postgres.store


def collected(store):
    c, session, lease, _payload, _fetched = claimed(store)
    requeue_job(store, lease.run_id)
    client, _posts = broker_client(c)
    with client:
        dispatch_once(store, uuid4(), client, ("README.md",))
    return c, session, lease.run_id


def test_index_is_committed_with_context_and_bound_to_the_bundle(store):
    _c, session, run_id = collected(store)
    state = store.get(run_id, session.user_id)
    bundle = store.context(run_id, session.user_id, state.context_refs[0])
    index = Store(store.dsn).repository_index(run_id, session.user_id)  # cold read
    assert isinstance(index, RepositoryIndex)
    assert index.digest == bundle.index_digest
    assert index.source_commit == state.source_commit
    assert index.evidence_origin == "TEST_TRANSPORT"
    assert [f.path for f in index.files] == ["README.md"]
    assert index.can_authorize_execution is False


def test_api_serves_the_owner_only_and_never_writes(store):
    c, session, run_id = collected(store)
    r = c.get(f"/api/v1/runs/{run_id}/repository-index")
    assert r.status_code == 200 and r.json()["trust"] == "UNTRUSTED_REPOSITORY_DATA"
    assert "content" not in r.text  # names, roles and hashes only, never source bytes
    assert c.post(f"/api/v1/runs/{run_id}/repository-index").status_code in {404, 405}
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.repository_index(run_id, uuid4())


def test_run_without_a_retained_index_is_not_found(store):
    _c, session, lease, _payload, _fetched = claimed(store)
    with pytest.raises(StoreError, match="NOT_FOUND"):
        store.repository_index(lease.run_id, session.user_id)


def tamper(sql, params):
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as db:
        db.execute(sql, params)


def test_modified_payload_bytes_fail_the_checksum(store):
    _c, session, run_id = collected(store)
    tamper(
        "UPDATE repository_indexes SET payload=convert_to('{}','UTF8') WHERE run_id=%s",
        (run_id,),
    )
    with pytest.raises(StoreError, match="INDEX_INTEGRITY"):
        store.repository_index(run_id, session.user_id)


def test_payload_with_a_forged_digest_and_checksum_is_still_refused(store):
    _c, session, run_id = collected(store)
    index = store.repository_index(run_id, session.user_id)
    forged = index.model_copy(update={"frameworks": ("django",)})
    body = forged.model_dump_json().encode()  # content changed, digest field stale
    import hashlib

    tamper(
        "UPDATE repository_indexes SET payload=%s,digest=%s WHERE run_id=%s",
        (body, hashlib.sha256(body).hexdigest(), run_id),
    )
    with pytest.raises(StoreError, match="INDEX_INTEGRITY"):
        store.repository_index(run_id, session.user_id)


def test_inconsistent_or_unbound_index_is_never_written():
    from services.context.index import build_index
    from tests.unit.test_context_index import snapshot

    index = build_index(snapshot())
    forged = index.model_copy(update={"digest": "0" * 64})
    with pytest.raises(StoreError, match="INDEX_INTEGRITY"):
        Store._index_payload(forged)
    assert Store._index_payload(index)  # the honest index serializes canonically
