"""Contract-only synthetic signers/ports; no real REMOTE_SERVICE facts asserted."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.contracts.context import ContextBundle, ContextFile
from packages.contracts.run import Stage
from packages.providers.nebius import NebiusConfig
from services.orchestrator import repair_flow
from services.persistence.baseline import BaselineAction, RetainedReceipt
from services.persistence.store import StoreError
from services.runner.baseline import FailureExpectation
from services.runner.receipts import ReceiptVerifier, SignedReceipt, canonical
from tests.unit.test_baseline_inspection import XML
from tests.unit.test_patch_admission import DIFF
from tests.unit.test_runner_receipts import fixture


@pytest.mark.parametrize(
    "fault", [None, "test_origin", "wrong_reference", "wrong_commit"]
)
def test_patch_controller_correlates_before_call_and_reconciles(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    plan, receipt, now, _, _ = fixture()
    origin = "TEST_TRANSPORT" if fault == "test_origin" else "REMOTE_SERVICE"
    receipt = receipt.model_copy(
        update={
            "origin": origin,
            "junit_ref": "sha256:" + hashlib.sha256(XML).hexdigest(),
        }
    )
    key = Ed25519PrivateKey.generate()
    signed = SignedReceipt(payload=receipt, key_id="synthetic", signature="0" * 128)
    signed = signed.model_copy(
        update={"signature": key.sign(canonical(signed, unsigned=True)).hex()}
    )
    expected = FailureExpectation(
        test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
    )
    result = ReceiptVerifier(
        {"synthetic": key.public_key()}, origin=origin
    ).inspect_baseline(
        signed,
        plan,
        XML,
        execution_id=receipt.execution_id,
        source_tree_ref=receipt.source_tree_ref,
        expected=expected,
        now=now,
    )
    retained = RetainedReceipt(signed=signed, result=result, accepted_at=now)
    action = BaselineAction(
        execution_id=receipt.execution_id,
        plan=plan,
        source_tree_ref=receipt.source_tree_ref,
        expected=expected,
    )
    from tests.unit.test_patch_admission import context

    run, grant, owner = context()
    run = run.model_copy(
        update={
            "run_id": plan.request.run_id,
            "repository_id": plan.request.repository_id,
            "source_commit": plan.request.source_commit,
            "source_manifest_ref": plan.request.source_manifest_ref,
            "reproduction_test_ref": plan.request.reproduction_test_ref,
            "context_refs": ("sha256:" + "f" * 64,),
            "baseline_result_ref": "sha256:"
            + hashlib.sha256(canonical(retained)).hexdigest(),
        }
    )
    if fault == "wrong_reference":
        run = run.model_copy(update={"baseline_result_ref": "sha256:" + "0" * 64})
    if fault == "wrong_commit":
        run = run.model_copy(update={"source_commit": "f" * 40})
    body = "x=1\n"
    bundle = ContextBundle(
        source_commit=run.source_commit,
        sanitized_errors="",
        files=(
            ContextFile(
                path="src/a.py",
                source_sha256=hashlib.sha256(body.encode()).hexdigest(),
                sanitized_sha256=hashlib.sha256(body.encode()).hexdigest(),
                content=body,
                parse_status="PARSED",
            ),
        ),
    )
    state = SimpleNamespace(run=run, calls=0, reservations=0, retained=None)

    class StorePort:
        def get(self, *args):
            return state.run

        def context(self, *args):
            return bundle

        def repository(self, *args):
            return grant

    class JournalPort:
        def __init__(self, *args):
            pass

        def reserve(self, *args, **kwargs):
            state.reservations += 1
            state.run = state.run.model_copy(update={"version": state.run.version + 1})
            return receipt.execution_id, state.run.version

        def retain(self, lease, owner, proposal, **kwargs):
            state.retained = proposal
            state.run = state.run.model_copy(
                update={
                    "current_stage": Stage.VERIFYING,
                    "patch_attempt_refs": (
                        "sha256:" + hashlib.sha256(DIFF.encode()).hexdigest(),
                    ),
                }
            )
            return state.run

        def get(self, *args):
            return state.retained

    monkeypatch.setattr(
        repair_flow,
        "BaselineJournal",
        lambda *args: SimpleNamespace(
            receipt=lambda *args: retained, action=lambda *args: action
        ),
    )
    monkeypatch.setattr(repair_flow, "PatchJournal", JournalPort)

    def respond(request):
        state.calls += 1
        assert (
            body
            in json.loads(json.loads(request.content)["messages"][1]["content"])[
                "sources"
            ].values()
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "replacements": {"src/a.py": "x=2\n"},
                                    "justification": "synthetic",
                                    "expected_test_delta": "assertion passes",
                                    "risk_notes": "unexecuted",
                                }
                            )
                        },
                    }
                ]
            },
        )

    args = (
        StorePort(),
        SimpleNamespace(run_id=run.run_id),
        owner,
        NebiusConfig.from_env({"NEBIUS_API_KEY": "test-secret"}),
        receipt.execution_id,
    )
    if fault:
        with pytest.raises(StoreError, match="PATCH_BASELINE_BINDING_DENIED"):
            repair_flow.run_patch(*args, transport=httpx.MockTransport(respond))
        assert state.calls == state.reservations == 0
    else:
        # Patch gateway's real scope check still requires the matching grant.
        grant = grant.model_copy(update={"repository_id": run.repository_id})
        first = repair_flow.run_patch(*args, transport=httpx.MockTransport(respond))
        assert (
            repair_flow.run_patch(*args, transport=httpx.MockTransport(respond))
            == first
        )
        assert state.calls == state.reservations == 1
