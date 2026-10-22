"""Measured backend through real durable dispatch, with synthetic HTTP only."""

import tarfile
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services.orchestrator.replay_dispatch import dispatch_replay
from services.persistence.replay import ReplayJournal
from services.persistence.store import Store
from services.runner.prepared import PreparedImageBinding
from services.runner.producer import MeasuredPreparedBackend
from services.runner.receipts import ReceiptVerifier, plan_ref
from services.runner.workspace import archive_identity
from services.verifier.replay import ReplayActions
from tests.contract.test_sandbox_smoke import config
from tests.contract.test_workspace_measurement import archive
from tests.integration import test_postgres
from tests.integration.test_replay_delivery import prepared
from tests.unit.test_replay_receipts import XML
from tests.unit.test_runner_admission import fixture as capability_fixture

store = test_postgres.store


@pytest.mark.parametrize("timeout", [False, True])
def test_measured_dispatch_qualifies_actual_report_and_restart_never_posts(
    store, timeout
):
    lease, owner, run, actions, *_ = prepared(store, reserve=False)
    content = archive([("app.py", b"x=1\n", tarfile.REGTYPE)])
    tree = archive_identity(content)[0]

    def measured(plan):
        return plan.model_copy(
            update={
                "command_id": "PYTEST_FILES_MEASURED_V1",
                "argv": (plan.argv[0], "-B", *plan.argv[1:]),
            }
        )

    actions = ReplayActions.model_validate(
        {
            **actions.model_dump(),
            "candidate_plan": measured(actions.candidate_plan),
            "clean_plan": measured(actions.clean_plan),
            "patched_tree_ref": tree,
        }
    )
    run = ReplayJournal(store).reserve(
        lease, owner, actions, expected_version=run.version
    )
    _, profile, _, sign, verifier = capability_fixture("REMOTE_SERVICE")
    binding = PreparedImageBinding(
        plan_ref=plan_ref(actions.candidate_plan),
        execution_id=actions.candidate_execution_id,
        tree_ref=tree,
        image_uuid=config().image_uuid,
        image_digest=actions.candidate_plan.request.image_digest,
        runner_id=profile.runner_id,
        configuration_ref=profile.configuration_ref,
    )
    operation, image, calls = uuid4(), uuid4(), []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            if timeout:
                raise httpx.ReadTimeout("synthetic", request=request)
            return httpx.Response(201, json={"uuid": str(operation)})
        if request.url.path.endswith("/archive"):
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=XML)
        return httpx.Response(
            200,
            json={
                "uuid": str(operation),
                "kind": "instance",
                "status": "SUCCESS",
                "image_uuid": str(binding.image_uuid),
                "result_image_uuid": str(image),
                "metadata": {
                    "result": {
                        "state": {"exit_code": 0, "timed_out": False, "signal": None}
                    }
                },
            },
        )

    key = Ed25519PrivateKey.generate()

    def backend():
        return MeasuredPreparedBackend(
            config(),
            binding,
            sign(),
            verifier,
            receipt_key=key,
            receipt_key_id="test",
            transport=httpx.MockTransport(handler),
        )

    with backend() as current:
        first = dispatch_replay(
            store,
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            current,
            expected_version=run.version,
        )
        assert first.status == ("SUBMISSION_UNKNOWN" if timeout else "SUBMITTED")
        if not timeout:
            result = current.collect_receipt(first.operation_id)
            checked = ReceiptVerifier(
                {"test": key.public_key()}, origin="TEST_TRANSPORT"
            ).inspect_replay(
                result.signed,
                actions.candidate_plan,
                result.junit,
                execution_id=actions.candidate_execution_id,
                source_tree_ref=tree,
                test_name="test_duplicate",
                now=datetime.now(UTC),
            )
            assert checked.reason == "PASS"
            assert checked.receipt.origin == "TEST_TRANSPORT"
    count = len(calls)
    with backend() as restarted:
        again = dispatch_replay(
            Store(store.dsn),
            lease,
            owner,
            "CANDIDATE",
            sign(),
            verifier,
            restarted,
            expected_version=run.version,
        )
        assert again.status == (
            "SUBMISSION_UNKNOWN" if timeout else "ALREADY_SUBMITTED"
        )
    assert len(calls) == count == (2 if timeout else 5)
    assert sum(r.method == "POST" for r in calls) == 1
    assert store.get(run.run_id, owner) == run
