"""Fresh candidate/clean workspace custody with no provider call."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from packages.contracts.run import Stage
from services.orchestrator.patched_materialization import materialize_patched_once
from services.persistence.patched_materialization import (
    PatchedMaterializationIntent,
    PatchedMaterializationJournal,
)
from services.persistence.store import Store, StoreError
from services.runner.candidate_workspace import CandidateWorkspace
from services.runner.materialization import MaterializationResult
from services.runner.workspace import WorkspaceMeasurement
from tests.contract.test_sandbox_smoke import config
from tests.integration import test_postgres
from tests.integration.test_materialization import provider, setup

store = test_postgres.store


def verifying_fixture(store):
    owner, run, lease, _files, upload = setup(store)
    patch_ref = "sha256:" + "e" * 64
    run = run.model_copy(
        update={"current_stage": Stage.VERIFYING, "patch_attempt_refs": (patch_ref,)}
    )
    with Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"])._connection() as db:
        db.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
    return owner, run, lease, upload.plan, patch_ref


def result(plan, operation):
    image = uuid4()
    return MaterializationResult(
        plan=plan,
        operation_id=operation,
        result_image_uuid=image,
        measurement=WorkspaceMeasurement(
            image_uuid=image,
            tree_ref=plan.workspace_ref,
            files=2,
            bytes_measured=36,
            evidence_origin="TEST_TRANSPORT",
        ),
        observed_at=datetime.now(UTC),
    )


def test_candidate_and_clean_intents_are_distinct_sticky_and_fresh(store):
    owner, run, lease, plan, patch_ref = verifying_fixture(store)
    candidate = PatchedMaterializationJournal(
        store, "CANDIDATE", origin="TEST_TRANSPORT"
    )
    clean = PatchedMaterializationJournal(
        store, "CLEAN_REPLAY", origin="TEST_TRANSPORT"
    )
    candidate_intent = PatchedMaterializationIntent(
        phase="CANDIDATE", plan=plan, patch_ref=patch_ref
    )
    clean_intent = candidate_intent.model_copy(update={"phase": "CLEAN_REPLAY"})
    assert candidate.claim(lease, owner, candidate_intent, expected_version=run.version)
    after_candidate = store.get(run.run_id, owner)
    assert clean.claim(
        lease, owner, clean_intent, expected_version=after_candidate.version
    )
    after_clean = store.get(run.run_id, owner)
    assert (
        after_clean.remaining_budget.sandbox_commands
        == run.remaining_budget.sandbox_commands - 2
    )

    candidate_operation, clean_operation = uuid4(), uuid4()
    assert (
        candidate.operation(lease, owner, candidate_intent, candidate_operation)
        == candidate_operation
    )
    assert (
        clean.operation(lease, owner, clean_intent, clean_operation) == clean_operation
    )
    candidate_result = candidate.result(
        lease, owner, candidate_intent, result(plan, candidate_operation)
    )
    clean_result = clean.result(
        lease, owner, clean_intent, result(plan, clean_operation)
    )
    assert candidate_result is not None and clean_result is not None
    assert candidate_result.operation_id != clean_result.operation_id
    assert candidate.result(lease, owner, candidate_intent) == candidate_result
    assert store.get(run.run_id, owner).current_stage == Stage.VERIFYING


def test_patched_materialization_rejects_stale_patch_before_intent(store):
    owner, run, lease, plan, _ = verifying_fixture(store)
    journal = PatchedMaterializationJournal(store, "CANDIDATE", origin="TEST_TRANSPORT")
    with pytest.raises(StoreError, match="BINDING_DENIED"):
        journal.claim(
            lease,
            owner,
            PatchedMaterializationIntent(
                phase="CANDIDATE", plan=plan, patch_ref="sha256:" + "f" * 64
            ),
            expected_version=run.version,
        )


def test_remote_candidate_materialization_is_one_shot_and_recoverable(store):
    # The provider fixture only echoes stdin; the backend validates the returned
    # archive identity, so use a valid candidate archive from the original setup.
    owner, run, lease, _files, source_upload = setup(store)
    patch_ref = "sha256:" + "e" * 64
    run = run.model_copy(
        update={"current_stage": Stage.VERIFYING, "patch_attempt_refs": (patch_ref,)}
    )
    with Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"])._connection() as db:
        db.execute(
            "UPDATE runs SET checkpoint=%s WHERE id=%s",
            (Jsonb(run.model_dump(mode="json")), run.run_id),
        )
    workspace = CandidateWorkspace(
        original=source_upload.plan,
        clean_upload=source_upload,
        upload=source_upload,
        patch_ref=patch_ref,
    )
    calls, ready, transport = provider(source_upload, pending=True)
    assert (
        materialize_patched_once(
            store,
            lease,
            owner,
            workspace,
            "CANDIDATE",
            config(),
            lambda: None,
            expected_version=run.version,
            transport=transport,
        )
        is None
    )
    ready[0] = True
    current = store.get(run.run_id, owner)
    received = materialize_patched_once(
        Store(store.dsn),
        lease,
        owner,
        workspace,
        "CANDIDATE",
        config(),
        lambda: None,
        expected_version=current.version,
        transport=transport,
    )
    assert received is not None and sum(r.method == "POST" for r in calls) == 1
