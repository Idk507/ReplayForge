"""Publication custody contracts with synthetic gates and ports, never live PRs."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from packages.contracts.run import Stage
from services.approval.journal import ApprovalJournal
from services.approval.policy import ApprovalBinding, ApprovalDenied, ReviewPermission
from services.orchestrator.publication import publish_approved
from services.persistence.store import Store, StoreError
from services.persistence.verification import VerificationJournal
from services.publication.github import PullRequestResult
from tests.integration import test_postgres
from tests.integration.test_verification_journal import prepared
from tests.unit.test_publication_patch import DIFF

store = test_postgres.store


def setup(store):
    lease, owner, run, signed, verifier, context = prepared(store, patch=DIFF.encode())
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE repository_grants SET grant_json=jsonb_set("
            "grant_json,'{approved_paths}','[\"src/\",\"tests/\"]') WHERE id=%s",
            (run.repository_id,),
        )
    run = VerificationJournal(store).accept(
        lease,
        owner,
        signed,
        verifier,
        image_digest=context.image_digest,
        configuration_ref=context.configuration_ref,
        phase_bindings=context.phase_bindings,
        expected_version=run.version,
    )
    binding = ApprovalBinding(
        run_id=run.run_id,
        repository_id=run.repository_id,
        owner_id=owner,
        source_commit=run.source_commit,
        patch_ref=run.patch_attempt_refs[-1],
        verification_ref=run.verification_ref,
        target_branch="main",
        policy_version=run.policy_version,
    )
    permission = ReviewPermission(
        reviewer_id=uuid4(), repository_id=run.repository_id, owner_id=owner
    )
    decision = ApprovalJournal(store, target_branch="main").record(
        binding,
        permission,
        decision="approve",
        nonce=uuid4(),
        expected_version=run.version,
    )
    return binding, permission, decision.nonce, run.version + 1


class SyntheticPort:
    """Trusted-port shape only: no HTTP and no claim of real GitHub acceptance."""

    origin = "REMOTE_SERVICE"

    def __init__(self, before=None):
        self.calls = 0
        self.closed = False
        self.before = before

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def publish(self, work, authorize):
        self.calls += 1
        assert work.diff == DIFF
        assert authorize() == work.intent
        if self.before:
            self.before(work)
        binding = work.intent.binding
        return PullRequestResult(
            run_id=binding.run_id,
            external_id=work.intent.external_id,
            source_commit=binding.source_commit,
            patch_ref=binding.patch_ref,
            head_commit="e" * 40,
            head_branch=work.intent.head_branch,
            number=1,
            html_url=work.grant.remote_url + "/pull/1",
            state="open",
            origin=self.origin,
            observed_at=datetime.now(UTC),
        )


def execute(store, setup, port, **kwargs):
    binding, permission, nonce, version = setup
    return publish_approved(
        store,
        binding,
        nonce,
        lambda: permission,
        lambda: port,
        expected_version=version,
        **kwargs,
    )


def test_publication_result_survives_restart_without_repeating_port(store):
    data = setup(store)
    port = SyntheticPort()
    result = execute(store, data, port, allow_live_writes=True)
    assert port.closed and port.calls == 1
    assert store.get(data[0].run_id, data[0].owner_id).current_stage == Stage.PR_OPENED
    again = SyntheticPort()
    assert execute(Store(store.dsn), data, again) == result
    assert again.calls == 0
    assert (
        sum(
            e.event_type == "PUBLICATION_OBSERVED"
            for e in store.events(data[0].run_id, data[0].owner_id)
        )
        == 1
    )


@pytest.mark.parametrize(
    "fault", ["nonce", "permission", "cancel", "scope", "expired", "live"]
)
def test_publication_denials_do_not_call_port(store, fault):
    data = list(setup(store))
    if fault == "nonce":
        data[2] = uuid4()
    if fault == "permission":
        data[1] = data[1].model_copy(update={"reviewer_id": uuid4()})
    with psycopg.connect(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]) as conn:
        if fault == "cancel":
            conn.execute(
                "UPDATE runs SET checkpoint=jsonb_set(checkpoint,"
                "'{cancellation_requested}','true') WHERE id=%s",
                (data[0].run_id,),
            )
        if fault == "scope":
            conn.execute(
                "UPDATE repository_grants SET grant_json=jsonb_set("
                "grant_json,'{consent}','false') WHERE id=%s",
                (data[0].repository_id,),
            )
        if fault == "expired":
            conn.execute(
                "UPDATE approval_decisions SET expires_at=clock_timestamp() "
                "WHERE run_id=%s",
                (data[0].run_id,),
            )
    port = SyntheticPort()
    with pytest.raises((StoreError, ApprovalDenied)):
        execute(store, data, port, allow_live_writes=fault != "live")
    assert port.calls == 0


def test_publication_late_cancel_retains_fact_without_reopening(store):
    data = setup(store)

    def cancel(work):
        store.cancel(
            work.intent.binding.run_id,
            work.intent.binding.owner_id,
            expected_version=store.get(
                work.intent.binding.run_id, work.intent.binding.owner_id
            ).version,
        )

    result = execute(store, data, SyntheticPort(cancel), allow_live_writes=True)
    assert result.number == 1
    assert store.get(data[0].run_id, data[0].owner_id).current_stage == Stage.CANCELLED
    assert execute(store, data, SyntheticPort()) == result


def test_publication_test_transport_cannot_become_remote_evidence(store):
    data = setup(store)
    port = SyntheticPort()
    port.origin = "TEST_TRANSPORT"
    with pytest.raises(StoreError, match="PUBLICATION_TEST_EVIDENCE_DENIED"):
        execute(store, data, port)
    assert (
        store.get(data[0].run_id, data[0].owner_id).current_stage == Stage.PR_CREATING
    )
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM publication_results WHERE run_id=%s",
                (data[0].run_id,),
            ).fetchone()["n"]
            == 0
        )


def test_publication_result_is_immutable_under_runtime_role(store):
    data = setup(store)
    execute(store, data, SyntheticPort(), allow_live_writes=True)
    for operation in [
        "UPDATE publication_results SET digest=repeat('0',64)",
        "DELETE FROM publication_results",
    ]:
        with pytest.raises(StoreError, match="DB_FAILURE_42501"):
            # Store translates the exact SQL permission denial into its safe code.
            with store._connection() as conn:
                conn.execute(operation + " WHERE run_id=%s", (data[0].run_id,))


def test_publication_rechecks_identity_before_port_write(store):
    data = setup(store)
    calls = []

    def permission():
        calls.append(1)
        return (
            data[1]
            if len(calls) == 1
            else data[1].model_copy(update={"reviewer_id": uuid4()})
        )

    port = SyntheticPort()
    with pytest.raises(ApprovalDenied):
        publish_approved(
            store,
            data[0],
            data[2],
            permission,
            lambda: port,
            expected_version=data[3],
            allow_live_writes=True,
        )
    assert port.closed and port.calls == 1
    assert (
        store.get(data[0].run_id, data[0].owner_id).current_stage == Stage.PR_CREATING
    )
