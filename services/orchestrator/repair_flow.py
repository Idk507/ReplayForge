"""Single-attempt triage orchestration over persisted, sanitized context."""

import hashlib
import json
from uuid import UUID

import httpx
from pydantic import ValidationError

from packages.contracts.inference import ModelCostPolicy, ModelDispatch, RequestIdentity
from packages.contracts.run import RunState, Stage
from packages.providers.nebius import NebiusConfig, ProbeError
from packages.providers.patch import request_patch
from packages.providers.triage import request_triage
from services.orchestrator.research import ResearchBroker
from services.persistence.baseline import BaselineJournal
from services.persistence.patch import PatchJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import ReceiptVerifier, canonical, plan_ref


def _dispatch(
    identity: RequestIdentity, policy: ModelCostPolicy | None
) -> ModelDispatch:
    if policy is None:
        raise ProbeError("MODEL_COST_POLICY_REQUIRED")
    try:
        return ModelDispatch(
            request=RequestIdentity.model_validate(identity.model_dump()),
            cost=ModelCostPolicy.model_validate(policy.model_dump()),
        )
    except ValidationError:
        raise ProbeError("MODEL_COST_POLICY_DENIED") from None


def continue_from_baseline(
    store: Store,
    lease: Lease,
    owner: UUID,
    config: NebiusConfig,
    baseline_execution_id: UUID,
    junit: bytes,
    verifier: ReceiptVerifier,
    *,
    transport: httpx.BaseTransport | None = None,
    cost_policy: ModelCostPolicy | None = None,
) -> RunState:
    """Accept retained remote evidence, then generate/retain one bounded patch.

    Trusted worker entry point, not a model tool or execution dispatcher. Resume
    from the durable stage: never repeat acceptance after PATCHING, and let the
    patch journal reconcile completed output or deny uncertain inference.
    Invalid reproduction terminates without contacting the model.
    """
    run = store.get(lease.run_id, owner)
    if run.current_stage == Stage.REPRODUCING:
        run = BaselineJournal(store).accept(
            lease,
            owner,
            baseline_execution_id,
            junit,
            verifier,
            expected_version=run.version,
        )
        if run.current_stage == Stage.INCONCLUSIVE:
            return run
    return run_patch(
        store,
        lease,
        owner,
        config,
        baseline_execution_id,
        transport=transport,
        cost_policy=cost_policy,
    )


def run_patch(
    store: Store,
    lease: Lease,
    owner: UUID,
    config: NebiusConfig,
    baseline_execution_id: UUID,
    *,
    transport: httpx.BaseTransport | None = None,
    cost_policy: ModelCostPolicy | None = None,
) -> RunState:
    """Generate from retained context/baseline once; never apply generated code.

    PATCHING must already have been entered by the trusted baseline reducer.
    This controller adds correlation checks, not runner admission or signature
    authority. It cannot promote REPRODUCING to PATCHING.
    """
    config = NebiusConfig.from_env(
        {
            "NEBIUS_API_KEY": config.api_key.get_secret_value(),
            "NEBIUS_BASE_URL": config.base_url,
            "NEBIUS_MODEL_PRIMARY": config.model,
        }
    )
    run = store.get(lease.run_id, owner)
    journal = PatchJournal(store)
    if run.current_stage == Stage.VERIFYING and run.patch_attempt_refs:
        journal.get(run.run_id, owner)
        return run
    if run.current_stage != Stage.PATCHING or run.cancellation_requested:
        raise StoreError("PATCH_STAGE_DENIED")
    baseline = BaselineJournal(store)
    retained = baseline.receipt(baseline_execution_id, owner)
    action = baseline.action(baseline_execution_id, owner)
    request = action.plan.request
    if (
        run.baseline_result_ref
        != "sha256:" + hashlib.sha256(canonical(retained)).hexdigest()
        or retained.result.status != "MATCHED_SIGNED_REPORT"
        or retained.signed.payload.origin != "REMOTE_SERVICE"
        or retained.result.receipt.origin != "REMOTE_SERVICE"
        or retained.signed.payload.plan_ref != plan_ref(action.plan)
        or retained.result.inspection.source_commit != run.source_commit
        or retained.result.inspection.reproduction_test_ref != run.reproduction_test_ref
        or retained.signed.payload.run_id != run.run_id
        or request.run_id != run.run_id
        or request.source_commit != run.source_commit
        or request.repository_id != run.repository_id
        or request.source_manifest_ref != run.source_manifest_ref
        or request.reproduction_test_ref != run.reproduction_test_ref
    ):
        raise StoreError("PATCH_BASELINE_BINDING_DENIED")
    sources: dict[str, str] = {}
    for ref in run.context_refs:
        bundle = store.context(run.run_id, owner, ref)
        if bundle.source_commit != run.source_commit:
            raise StoreError("PATCH_CONTEXT_BINDING")
        for file in bundle.files:
            if file.path.startswith(("app/", "src/")) and file.path.endswith(".py"):
                if file.path in sources and sources[file.path] != file.content:
                    raise StoreError("PATCH_CONTEXT_BINDING")
                sources[file.path] = file.content
    failure = "Observed baseline assertion marker: " + action.expected.assertion_marker
    grant = store.repository(run.repository_id, owner)
    # Reject size before debiting; the gateway checks the exact same encoding.
    context = json.dumps({"sources": sources, "failure": failure}, sort_keys=True)
    if not sources or len(sources) > 8 or len(context.encode()) > 4096:
        raise ProbeError("PATCH_CONTEXT_DENIED")
    version: int | None = None

    def reserve(identity: RequestIdentity) -> None:
        nonlocal version
        _, version = journal.reserve(
            lease,
            owner,
            expected_version=run.version,
            dispatch=_dispatch(identity, cost_policy),
        )
        if store.get(run.run_id, owner).version != version:
            raise StoreError("STALE_VERSION")

    proposal, _ = request_patch(
        config,
        sources,
        failure,
        run,
        grant,
        owner,
        transport=transport,
        before_submit=reserve,
    )
    if version is None:
        raise StoreError("MODEL_RESERVATION_REQUIRED")
    return journal.retain(lease, owner, proposal, expected_version=version)


def run_triage(
    store: Store,
    lease: Lease,
    owner: UUID,
    config: NebiusConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    cost_policy: ModelCostPolicy | None = None,
    finish_job: bool = False,
) -> RunState:
    """Reserve once, request once, retain once; never retry uncertain inference.

    Only internal worker code may call this with authorized provider usage.
    Context comes from owner-scoped persisted artifacts, not a caller's prompt.
    Provider errors and lease loss propagate; the sticky intent prevents another
    paid request after an uncertain outcome. Completed retention is reconciled
    from the checkpoint without making a new call.
    """
    config = NebiusConfig.from_env(
        {
            "NEBIUS_API_KEY": config.api_key.get_secret_value(),
            "NEBIUS_BASE_URL": config.base_url,
            "NEBIUS_MODEL_PRIMARY": config.model,
        }
    )
    run = store.get(lease.run_id, owner)
    journal = ReproductionJournal(store)
    if run.current_stage == Stage.REPRODUCING and run.reproduction_test_ref:
        journal.get(run.run_id, owner)
        return run
    if run.current_stage != Stage.TRIAGING or run.cancellation_requested:
        raise StoreError("TRIAGE_STAGE_DENIED")
    context: dict[str, str] = {}
    for index, reference in enumerate(run.context_refs):
        bundle = store.context(run.run_id, owner, reference)
        if bundle.source_commit != run.source_commit:
            raise StoreError("TRIAGE_CONTEXT_BINDING")
        context[f"context_{index}"] = bundle.model_dump_json()
    if run.research_refs:
        reports = ResearchBroker(store).reports(run.run_id, owner)
        if len(reports) != len(run.research_refs):
            raise StoreError("TRIAGE_RESEARCH_UNAVAILABLE")
        for index, report in enumerate(reports):
            context[f"untrusted_documentation_{index}"] = report.model_dump_json()
    # Match the gateway's byte/count bounds before reserving a paid attempt.
    if (
        not context
        or len(context) > 8
        or len(json.dumps(context, sort_keys=True, separators=(",", ":")).encode())
        > 4096
    ):
        raise ProbeError("TRIAGE_CONTEXT_DENIED")
    reserved_version: int | None = None

    def reserve(identity: RequestIdentity) -> None:
        nonlocal reserved_version
        _, reserved_version = journal.reserve_inference(
            lease,
            owner,
            expected_version=run.version,
            dispatch=_dispatch(identity, cost_policy),
        )
        if store.get(run.run_id, owner).version != reserved_version:
            raise StoreError("STALE_VERSION")

    proposal = request_triage(
        config, context, transport=transport, before_submit=reserve
    )
    if reserved_version is None:
        raise StoreError("MODEL_RESERVATION_REQUIRED")
    return journal.retain(
        lease,
        owner,
        proposal.reproduction,
        expected_version=reserved_version,
        finish_job=finish_job,
    )
