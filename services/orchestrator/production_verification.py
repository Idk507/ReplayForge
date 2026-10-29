"""Durable four-phase evidence loading and seven-gate finalization.

This is an internal worker adapter. Its inputs are an owner-authorized run ID,
the active lease, and operator-provisioned trust dependencies; it accepts no
caller-supplied success claims or gate records.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import ValidationError

from packages.contracts.execution import TestCommandPlan
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import ArtifactID, RunState, Stage
from packages.policies.repository import admit, allowed_entry, patchable_source
from services.persistence.baseline import (
    BaselineAction,
    RetainedReceipt,
)
from services.persistence.baseline import (
    _decode as decode_baseline,
)
from services.persistence.evidence import EvidenceStore
from services.persistence.materialization import _decode as decode_materialization
from services.persistence.operations import OperationJournal
from services.persistence.patch import PatchJournal, RetainedPatch
from services.persistence.patched_materialization import PatchedMaterializationIntent
from services.persistence.producer import Preparation
from services.persistence.producer import _decode as decode_preparation
from services.persistence.replay import ReplayJournal
from services.persistence.reproduction import ReproductionJournal
from services.persistence.store import Lease, Store, StoreError
from services.persistence.verification import VerificationJournal
from services.runner.admission import CapabilityVerifier, recheck_retained_capability
from services.runner.materialization import MaterializationPlan, MaterializationResult
from services.runner.producer import ProducedReceipt
from services.runner.receipts import (
    ReceiptVerifier,
    SignedBaselineInspection,
    SignedReplayInspection,
    SignedReplayReceipt,
    SignedSuiteInspection,
    canonical,
    plan_ref,
)
from services.verifier.authenticated import (
    GateContext,
    GateDenied,
    GatePayload,
    GatePhaseBinding,
    GateVerifier,
)
from services.verifier.azure_es256 import (
    AzureES256GateSignerV5,
    AzureES256GateVerifierV5,
)
from services.verifier.gates import (
    EXECUTION_GATES,
    PHASE_BY_GATE,
    REQUIRED_GATES,
    GateEvidence,
    GateName,
    runtime_phase_for_gate,
)
from services.verifier.manifest import ArtifactInput
from services.verifier.replay import ReplayActions
from services.verifier.signing import ProductionGateSigner

PHASES: tuple[
    Literal["BASELINE", "CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"], ...
] = (
    "BASELINE",
    "CANDIDATE",
    "EXISTING_SUITE",
    "CLEAN_REPLAY",
)
REPLAY_PHASES: tuple[Literal["CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"], ...] = (
    "CANDIDATE",
    "EXISTING_SUITE",
    "CLEAN_REPLAY",
)


@dataclass(frozen=True)
class PhaseTrust:
    """Explicit externally provisioned identity for a phase's runtime image."""

    image_uuid: UUID
    image_digest: ArtifactID
    configuration_ref: ArtifactID
    runner_id: UUID
    receipt_verifier: ReceiptVerifier
    capability_verifier: CapabilityVerifier


@dataclass(frozen=True)
class _Phase:
    plan: TestCommandPlan
    execution_id: UUID
    operation_id: UUID
    workspace_operation_id: UUID
    materialization: MaterializationResult
    preparation: Preparation
    receipt: RetainedReceipt | ProducedReceipt
    junit: bytes
    capability_payload: bytes
    capability_ref: str


@dataclass(frozen=True)
class _Loaded:
    run: RunState
    owner: UUID
    grant: RepositoryGrant
    actions: ReplayActions
    baseline_action: BaselineAction
    phases: Mapping[str, _Phase]
    artifacts: tuple[ArtifactInput, ...]
    patch: RetainedPatch
    source_manifest_bytes: bytes
    reproduction_bytes: bytes


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _bytes(value: object, code: str, *, limit: int) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise StoreError(code)
    body = bytes(value)
    if not 1 <= len(body) <= limit:
        raise StoreError(code)
    return body


class ProductionGateProducer:
    """Load persisted four-phase evidence, derive gates, then sign and commit.

    ``phase_trust`` and the gate signer/verifier are application configuration,
    provisioned independently from the execution provider. No key is generated
    or accepted from a run, request, report, or workbench.
    """

    def __init__(
        self,
        store: Store,
        phase_trust: Mapping[str, PhaseTrust],
        signer: object,
        gate_verifier: object,
    ) -> None:
        if (
            set(phase_trust) != set(PHASES)
            or any(
                phase_trust[p].capability_verifier._origin != "REMOTE_SERVICE"
                for p in PHASES
            )
            or any(
                phase_trust[p].receipt_verifier._origin != "REMOTE_SERVICE"
                for p in PHASES
            )
        ):
            raise StoreError("GATE_TRUST_CONFIGURATION_INVALID")
        if isinstance(signer, ProductionGateSigner) and isinstance(
            gate_verifier, GateVerifier
        ):
            if signer.key_id not in gate_verifier._keys:
                raise StoreError("GATE_TRUST_CONFIGURATION_INVALID")
        elif isinstance(signer, AzureES256GateSignerV5) and isinstance(
            gate_verifier, AzureES256GateVerifierV5
        ):
            if (
                signer.backend.key_id not in gate_verifier._keys
                or gate_verifier._keys[signer.backend.key_id].key_version
                != signer.backend.key_version
            ):
                raise StoreError("GATE_TRUST_CONFIGURATION_INVALID")
        else:
            raise StoreError("GATE_TRUST_CONFIGURATION_INVALID")
        self.store = store
        self.phase_trust = dict(phase_trust)
        self.signer: Any = signer
        self.gate_verifier: Any = gate_verifier

    def artifacts(self, lease: Lease, owner: UUID) -> tuple[ArtifactInput, ...]:
        """Return exact durable evidence bytes to include before bundle retention."""
        lease = Lease.model_validate(lease.model_dump())
        return self._load(
            lease.run_id, owner, require_bundle=False, lease=lease
        ).artifacts

    def finalize(self, lease: Lease, owner: UUID, *, expected_version: int) -> RunState:
        """Derive and externally sign gates, then accept under the current fence."""
        lease = Lease.model_validate(lease.model_dump())
        if lease.run_id is None or type(expected_version) is not int:
            raise StoreError("GATE_FINALIZATION_INVALID")
        current = self.store.get(lease.run_id, owner)
        if current.current_stage in {
            Stage.VERIFIED,
            Stage.NOT_VERIFIED,
            Stage.INCONCLUSIVE,
        }:
            loaded = self._load(
                lease.run_id,
                owner,
                require_bundle=True,
                allow_terminal=True,
            )
            bindings = self._bindings(loaded)
            baseline = next(item for item in bindings if item.phase == "BASELINE")
            try:
                VerificationJournal(self.store).get_verified(
                    lease.run_id,
                    owner,
                    self.gate_verifier,
                    image_digest=baseline.image_digest,
                    configuration_ref=baseline.configuration_ref,
                    phase_bindings=bindings,
                )
            except GateDenied:
                raise StoreError("GATE_PERSISTED_SIGNATURE_INVALID") from None
            if current.version != expected_version:
                raise StoreError("STALE_VERSION")
            return current
        loaded = self._load(lease.run_id, owner, require_bundle=True, lease=lease)
        run = loaded.run
        if run.version != expected_version or run.current_stage != Stage.VERIFYING:
            raise StoreError("STALE_VERSION")
        manifest, retained = EvidenceStore(self.store).read(
            run.run_id, owner, run.evidence_manifest_ref or ""
        )
        retained_hashes = {_digest(item.content) for item in retained}
        if not {_digest(item.content) for item in loaded.artifacts} <= retained_hashes:
            raise StoreError("GATE_RETAINED_ARTIFACTS_MISSING")
        candidate = loaded.phases["CANDIDATE"]
        if manifest.workspace_sha256 != candidate.preparation.source.tree_ref[7:]:
            raise StoreError("GATE_CANDIDATE_WORKSPACE_BINDING_DENIED")
        now = self._database_now()
        gates, bindings = self._derive(loaded, now)
        payload = GatePayload(
            context=self._context(run, loaded, manifest.workspace_sha256, bindings),
            origin="REMOTE_SERVICE",
            observed_at=now,
            gates=gates,
        )
        try:
            return VerificationJournal(self.store).sign_and_accept(
                lease,
                owner,
                payload,
                self.signer,
                self.gate_verifier,
                image_digest=bindings[0].image_digest,
                configuration_ref=bindings[0].configuration_ref,
                phase_bindings=bindings,
                expected_version=expected_version,
            )
        except (GateDenied, ValidationError, ValueError):
            raise StoreError("GATE_FINALIZATION_DENIED") from None

    def _database_now(self) -> datetime:
        with self.store._connection() as conn:
            row = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        if row is None or not isinstance(row["now"], datetime):
            raise StoreError("GATE_CLOCK_INVALID")
        return row["now"]

    def _load(
        self,
        run_id: UUID,
        owner: UUID,
        *,
        require_bundle: bool,
        allow_terminal: bool = False,
        lease: Lease | None = None,
    ) -> _Loaded:
        run = self.store.get(run_id, owner)
        allowed_stages = {Stage.VERIFYING}
        if allow_terminal:
            allowed_stages.update(
                {Stage.VERIFIED, Stage.NOT_VERIFIED, Stage.INCONCLUSIVE}
            )
        if (
            run.current_stage not in allowed_stages
            or run.cancellation_requested
            or run.source_manifest_ref is None
            or run.reproduction_test_ref is None
            or not run.patch_attempt_refs
            or run.baseline_result_ref is None
        ):
            raise StoreError("GATE_RUN_NOT_READY")
        with self.store._connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            now_row = conn.execute("SELECT clock_timestamp() AS now").fetchone()
            scope_row = conn.execute(
                "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
                (run.repository_id, owner),
            ).fetchone()
            if now_row is None or not isinstance(now_row["now"], datetime):
                raise StoreError("GATE_CLOCK_INVALID")
            if scope_row is None:
                raise StoreError("GATE_CONSENT_DENIED")
            grant = RepositoryGrant.model_validate(scope_row["grant_json"])
            if not admit(grant, owner, run.repository_id, run.source_commit).allowed:
                raise StoreError("GATE_CONSENT_DENIED")
            if lease is not None:
                active_lease = conn.execute(
                    "SELECT 1 FROM job_leases WHERE run_id=%s AND worker_id=%s "
                    "AND token=%s AND status='leased' "
                    "AND leased_until>clock_timestamp()",
                    (run_id, lease.worker_id, lease.token),
                ).fetchone()
                if active_lease is None:
                    raise StoreError("LEASE_LOST")
            reservation = conn.execute(
                "SELECT payload,digest,from_version,to_version,"
                "expires_at>clock_timestamp() AS active "
                "FROM replay_reservations WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            if reservation is None or not reservation["active"]:
                raise StoreError("GATE_REPLAY_RESERVATION_MISSING")
            from services.persistence.replay import _decode as decode_actions

            actions = decode_actions(reservation["payload"], reservation["digest"])
            if (
                actions.suite_plan is None
                or actions.suite_execution_id is None
                or int(str(reservation["to_version"])) > run.version
                or int(str(reservation["to_version"]))
                <= int(str(reservation["from_version"]))
            ):
                raise StoreError("GATE_SUITE_PHASE_MISSING")
            baseline_row = conn.execute(
                "SELECT a.payload,a.digest,i.action_digest,i.to_version,"
                "i.reserved_seconds,s.payload AS receipt_payload,s.digest AS "
                "receipt_digest,s.report_payload,b.payload AS preparation_payload,"
                "b.digest AS preparation_digest,b.expires_at>clock_timestamp() AS "
                "preparation_active,x.capability_payload,x.capability_ref,x.plan_ref,"
                "x.runner_id,x.configuration_ref,x.origin,"
                "x.expires_at>clock_timestamp() "
                "AS submission_active FROM baseline_actions a JOIN baseline_receipts s "
                "USING(execution_id) JOIN baseline_submissions x USING(execution_id) "
                "JOIN baseline_preparations b USING(execution_id) "
                "JOIN baseline_dispatch_intents i USING(execution_id) "
                "WHERE a.execution_id=%s "
                "AND a.run_id=%s AND a.expires_at>clock_timestamp() "
                "AND s.expires_at>clock_timestamp()",
                (actions.baseline_execution_id, run_id),
            ).fetchone()
            if baseline_row is None:
                raise StoreError("GATE_BASELINE_EVIDENCE_MISSING")
            action = decode_baseline(
                baseline_row["payload"], baseline_row["digest"], BaselineAction
            )
            candidate_request = actions.candidate_plan.request
            if (
                action.expected.test_name != actions.test_name
                or action.plan.command_id != "PYTEST_FILES_MEASURED_V1"
                or action.plan.request.reproduction_path
                != candidate_request.reproduction_path
                or any(
                    not allowed_entry(grant, path, "100644")
                    for plan in (
                        actions.candidate_plan,
                        actions.suite_plan,
                        actions.clean_plan,
                    )
                    for path in plan.request.test_paths
                )
            ):
                raise StoreError("GATE_PLAN_SCOPE_DENIED")
            for phase, plan in (
                ("CANDIDATE", actions.candidate_plan),
                ("EXISTING_SUITE", actions.suite_plan),
                ("CLEAN_REPLAY", actions.clean_plan),
            ):
                if (
                    plan is None
                    or plan.command_id != "PYTEST_FILES_MEASURED_V1"
                    or plan.request.phase != phase
                    or plan.request.run_id != run_id
                    or plan.request.repository_id != run.repository_id
                    or plan.request.source_commit != run.source_commit
                    or plan.request.source_manifest_ref != run.source_manifest_ref
                    or plan.request.reproduction_test_ref != run.reproduction_test_ref
                    or plan.request.patch_ref != run.patch_attempt_refs[-1]
                ):
                    raise StoreError("GATE_PLAN_BINDING_DENIED")
            if (
                actions.clean_plan.request.model_dump(exclude={"phase"})
                != candidate_request.model_dump(exclude={"phase"})
                or actions.suite_plan.request.model_dump(
                    exclude={"phase", "test_paths"}
                )
                != candidate_request.model_dump(exclude={"phase", "test_paths"})
                or not set(candidate_request.test_paths).issubset(
                    actions.suite_plan.request.test_paths
                )
            ):
                raise StoreError("GATE_PLAN_LINEAGE_DENIED")
            retained = decode_baseline(
                baseline_row["receipt_payload"],
                baseline_row["receipt_digest"],
                RetainedReceipt,
            )
            baseline_prep = decode_preparation(
                {
                    "payload": baseline_row["preparation_payload"],
                    "digest": baseline_row["preparation_digest"],
                    "active": baseline_row["preparation_active"],
                },
                Preparation,
                32768,
            )
            source_workspace_row = conn.execute(
                "SELECT i.payload AS intent_payload,i.digest AS intent_digest,"
                "i.origin,o.operation_id,r.payload AS result_payload,"
                "r.digest AS result_digest FROM materialization_intents i "
                "JOIN materialization_operations o USING(run_id) "
                "JOIN materialization_results r USING(run_id) WHERE i.run_id=%s "
                "AND i.expires_at>clock_timestamp()",
                (run_id,),
            ).fetchone()
            if source_workspace_row is None:
                raise StoreError("GATE_SOURCE_MATERIALIZATION_MISSING")
            source_materialization = decode_materialization(
                source_workspace_row["result_payload"],
                source_workspace_row["result_digest"],
                MaterializationResult,
            )
            source_plan = decode_materialization(
                source_workspace_row["intent_payload"],
                source_workspace_row["intent_digest"],
                MaterializationPlan,
            )
            cap_payload = _bytes(
                baseline_row["capability_payload"],
                "GATE_BASELINE_CAPABILITY_MISSING",
                limit=16384,
            )
            if (
                baseline_row["origin"] != "REMOTE_SERVICE"
                or not baseline_row["submission_active"]
                or baseline_row["plan_ref"] != plan_ref(action.plan)
                or baseline_row["action_digest"] != baseline_row["digest"]
                or int(str(baseline_row["to_version"])) > run.version
                or baseline_row["reserved_seconds"] != action.plan.timeout_seconds
                or baseline_prep.plan != action.plan
                or baseline_prep.binding.execution_id != action.execution_id
                or baseline_prep.source.tree_ref != action.source_tree_ref
                or source_workspace_row["origin"] != "REMOTE_SERVICE"
                or source_workspace_row["operation_id"]
                != source_materialization.operation_id
                or source_plan.run_id != run_id
                or source_plan.repository_id != run.repository_id
                or source_plan.source_commit != run.source_commit
                or source_plan.source_manifest_ref != run.source_manifest_ref
                or source_plan.reproduction_test_ref != run.reproduction_test_ref
                or source_plan.workspace_ref != action.source_tree_ref
                or source_materialization.plan != source_plan
                or source_materialization.measurement.evidence_origin
                != "REMOTE_SERVICE"
                or source_materialization.measurement.tree_ref != action.source_tree_ref
                or source_materialization.measurement.image_uuid
                != source_materialization.result_image_uuid
                or baseline_prep.source != source_materialization.measurement
                or baseline_row["configuration_ref"]
                != baseline_prep.binding.configuration_ref
                or baseline_row["runner_id"] != baseline_prep.binding.runner_id
                or hashlib.sha256(cap_payload).hexdigest()
                != str(baseline_row["capability_ref"])[7:]
            ):
                raise StoreError("GATE_BASELINE_BINDING_DENIED")
            report = _bytes(
                baseline_row["report_payload"],
                "GATE_BASELINE_REPORT_MISSING",
                limit=131072,
            )
            operation_history = OperationJournal(self.store).history(
                actions.baseline_execution_id, owner
            )
            if (
                len(operation_history) != 3
                or operation_history[0].phase != "SUBMISSION_UNKNOWN"
                or operation_history[1].phase != "OPERATION_KNOWN"
                or operation_history[2].phase != "COMPUTE_TERMINAL"
                or operation_history[1].operation_id is None
                or operation_history[2].operation_id
                != operation_history[1].operation_id
                or operation_history[0].provider != "NEBIUS_CONTREE"
            ):
                raise StoreError("GATE_BASELINE_OPERATION_UNCERTAIN")
            phases: dict[str, _Phase] = {
                "BASELINE": _Phase(
                    action.plan,
                    actions.baseline_execution_id,
                    operation_history[1].operation_id,
                    source_materialization.operation_id,
                    source_materialization,
                    baseline_prep,
                    retained,
                    report,
                    cap_payload,
                    str(baseline_row["capability_ref"]),
                )
            }
            for phase in REPLAY_PHASES:
                plan, execution = {
                    "CANDIDATE": (
                        actions.candidate_plan,
                        actions.candidate_execution_id,
                    ),
                    "EXISTING_SUITE": (
                        actions.suite_plan,
                        actions.suite_execution_id,
                    ),
                    "CLEAN_REPLAY": (actions.clean_plan, actions.clean_execution_id),
                }[phase]
                if plan is None or execution is None:
                    raise StoreError("GATE_PHASE_MISSING")
                row = conn.execute(
                    "SELECT s.plan_ref,s.capability_ref,s.capability_payload,s.origin,"
                    "TRUE AS active,r.operation_id,p.payload "
                    "AS preparation_payload,p.digest AS preparation_digest,"
                    "p.expires_at>clock_timestamp() AS preparation_active,"
                    "t.payload AS receipt_payload,t.digest AS receipt_digest,"
                    "t.expires_at>clock_timestamp() AS receipt_active,"
                    "m.payload AS materialization_intent_payload,"
                    "m.digest AS materialization_intent_digest,"
                    "m.origin AS materialization_origin,"
                    "mo.operation_id AS workspace_operation_id,mr.payload AS "
                    "materialization_result_payload,mr.digest AS "
                    "materialization_result_digest "
                    "FROM replay_submissions s "
                    "LEFT JOIN replay_submission_results r USING(run_id,phase) "
                    "LEFT JOIN replay_preparations p USING(run_id,phase) "
                    "LEFT JOIN replay_produced_receipts t USING(run_id,phase) "
                    "LEFT JOIN patched_materialization_intents m USING(run_id,phase) "
                    "LEFT JOIN patched_materialization_operations mo "
                    "USING(run_id,phase) "
                    "LEFT JOIN patched_materialization_results mr USING(run_id,phase) "
                    "WHERE s.run_id=%s AND s.phase=%s",
                    (run_id, phase),
                ).fetchone()
                if (
                    row is None
                    or not row["active"]
                    or not row["preparation_active"]
                    or not row["receipt_active"]
                ):
                    raise StoreError("GATE_PHASE_EVIDENCE_MISSING")
                if row["origin"] != "REMOTE_SERVICE" or row["plan_ref"] != plan_ref(
                    plan
                ):
                    raise StoreError("GATE_PHASE_SUBMISSION_DENIED")
                op_id = ReplayJournal(self.store).operation_id(run_id, owner, phase)
                if (
                    row["operation_id"] is None
                    or UUID(str(row["operation_id"])) != op_id
                ):
                    raise StoreError("GATE_PHASE_OPERATION_MISMATCH")
                prep = decode_preparation(
                    {
                        "payload": row["preparation_payload"],
                        "digest": row["preparation_digest"],
                        "active": row["preparation_active"],
                    },
                    Preparation,
                    32768,
                )
                produced = self._decode_produced(
                    row["receipt_payload"], row["receipt_digest"]
                )
                materialization_intent = decode_materialization(
                    row["materialization_intent_payload"],
                    row["materialization_intent_digest"],
                    PatchedMaterializationIntent,
                )
                materialization = decode_materialization(
                    row["materialization_result_payload"],
                    row["materialization_result_digest"],
                    MaterializationResult,
                )
                if (
                    prep.plan != plan
                    or prep.binding.execution_id != execution
                    or prep.binding.tree_ref != actions.patched_tree_ref
                    or prep.source.evidence_origin != "REMOTE_SERVICE"
                    or row["materialization_origin"] != "REMOTE_SERVICE"
                    or materialization_intent.phase != phase
                    or materialization_intent.patch_ref != run.patch_attempt_refs[-1]
                    or materialization_intent.plan.run_id != run_id
                    or materialization_intent.plan.repository_id != run.repository_id
                    or materialization_intent.plan.source_commit != run.source_commit
                    or materialization_intent.plan.source_manifest_ref
                    != run.source_manifest_ref
                    or materialization_intent.plan.reproduction_test_ref
                    != run.reproduction_test_ref
                    or materialization_intent.plan.workspace_ref
                    != actions.patched_tree_ref
                    or materialization_intent.plan != materialization.plan
                    or materialization.operation_id != row["workspace_operation_id"]
                    or materialization.measurement.evidence_origin != "REMOTE_SERVICE"
                    or materialization.measurement.tree_ref != actions.patched_tree_ref
                    or materialization.measurement.image_uuid
                    != materialization.result_image_uuid
                    or prep.source != materialization.measurement
                    or prep.binding.image_uuid != materialization.result_image_uuid
                    or produced.operation_id != op_id
                    or produced.source != prep.source
                    or produced.final.image_uuid == prep.binding.image_uuid
                    or produced.signed.payload.origin != "REMOTE_SERVICE"
                    or produced.signed.payload.execution_id != execution
                    or produced.signed.payload.run_id != run_id
                    or produced.signed.payload.plan_ref != plan_ref(plan)
                    or hashlib.sha256(
                        _bytes(
                            row["capability_payload"],
                            "GATE_CAPABILITY_MISSING",
                            limit=16384,
                        )
                    ).hexdigest()
                    != str(row["capability_ref"])[7:]
                ):
                    raise StoreError("GATE_PHASE_BINDING_DENIED")
                phases[phase] = _Phase(
                    plan,
                    execution,
                    op_id,
                    UUID(str(row["workspace_operation_id"])),
                    materialization,
                    prep,
                    produced,
                    produced.junit,
                    _bytes(
                        row["capability_payload"],
                        "GATE_CAPABILITY_MISSING",
                        limit=16384,
                    ),
                    str(row["capability_ref"]),
                )
            conn.execute("SELECT 1")

        if (
            len(phases) != 4
            or action.plan.request.source_commit != run.source_commit
            or action.plan.request.repository_id != run.repository_id
            or action.plan.request.source_manifest_ref != run.source_manifest_ref
            or action.plan.request.reproduction_test_ref != run.reproduction_test_ref
            or retained.signed.payload.origin != "REMOTE_SERVICE"
            or retained.result.status != "MATCHED_SIGNED_REPORT"
            or run.baseline_result_ref != _digest(canonical(retained))
            or actions.baseline_execution_id != action.execution_id
            or action.plan.request.phase != "BASELINE"
            or action.expected.test_name != actions.test_name
            or baseline_prep.source.tree_ref != action.source_tree_ref
            or actions.patched_tree_ref == action.source_tree_ref
        ):
            raise StoreError("GATE_SOURCE_OR_BASELINE_BINDING_DENIED")
        source_manifest = self.store.source_manifest(
            run_id, owner, run.source_manifest_ref
        )
        if (
            source_manifest.repository_id != run.repository_id
            or source_manifest.source_commit != run.source_commit
            or source_manifest.evidence_origin != "REMOTE_SERVICE"
        ):
            raise StoreError("GATE_SOURCE_MANIFEST_DENIED")
        manifest_paths = {item.path for item in source_manifest.files}
        suite_paths = tuple(
            sorted(
                path
                for path in manifest_paths
                if path.startswith("tests/") and path.endswith(".py")
            )
        )
        reproduction = ReproductionJournal(self.store).get(run_id, owner)
        reproduction_bytes = reproduction.canonical_bytes()
        reproduction_path = reproduction.proposal.target_path
        expected_suite_paths = tuple(sorted({*suite_paths, reproduction_path}))
        if (
            not suite_paths
            or actions.suite_plan is None
            or actions.suite_plan.request.reproduction_path != reproduction_path
            or actions.suite_plan.request.test_paths != expected_suite_paths
            or actions.candidate_plan.request.reproduction_path != reproduction_path
            or actions.candidate_plan.request.test_paths != (reproduction_path,)
            or actions.clean_plan.request.test_paths != (reproduction_path,)
        ):
            raise StoreError("GATE_SOURCE_MANIFEST_COVERAGE_DENIED")
        source_bytes = self.store._source_payload(source_manifest)
        if (
            _digest(source_bytes) != run.source_manifest_ref
            or _digest(reproduction_bytes) != run.reproduction_test_ref
        ):
            raise StoreError("GATE_SOURCE_LINEAGE_DENIED")
        expected_materialized_paths = tuple(
            sorted({*manifest_paths, reproduction.proposal.target_path})
        )
        if source_plan.paths != expected_materialized_paths or any(
            phases[phase].materialization.plan.paths != expected_materialized_paths
            for phase in REPLAY_PHASES
        ):
            raise StoreError("GATE_MATERIALIZATION_PATH_COVERAGE_DENIED")
        patch = PatchJournal(self.store).get(run_id, owner)
        if (
            patch.admission.patch_ref != run.patch_attempt_refs[-1]
            or patch.admission.source_commit != run.source_commit
            or patch.admission.source_manifest_ref != run.source_manifest_ref
            or patch.admission.reproduction_test_ref != run.reproduction_test_ref
            or not patch.proposal.unified_diff
        ):
            raise StoreError("GATE_PATCH_LINEAGE_DENIED")
        from services.runner.patches import _paths

        paths, changed = _paths(patch.proposal.unified_diff)
        if (
            paths != patch.admission.files_changed
            or changed != patch.admission.changed_lines
            or any(path not in manifest_paths for path in paths)
            or any(not patchable_source(grant, path) for path in paths)
        ):
            raise StoreError("GATE_PATCH_SCOPE_DENIED")
        artifacts = self._artifact_inputs(
            run,
            owner,
            grant,
            lease,
            actions,
            phases,
            source_bytes,
            reproduction_bytes,
            patch,
        )
        if require_bundle:
            if run.evidence_manifest_ref is None:
                raise StoreError("GATE_EVIDENCE_BUNDLE_MISSING")
        return _Loaded(
            run,
            owner,
            grant,
            actions,
            action,
            phases,
            artifacts,
            patch,
            source_bytes,
            reproduction_bytes,
        )

    @staticmethod
    def _decode_produced(payload: object, digest: object) -> ProducedReceipt:
        body = _bytes(payload, "GATE_RECEIPT_MISSING", limit=100000)
        if hashlib.sha256(body).hexdigest() != digest:
            raise StoreError("GATE_RECEIPT_INTEGRITY")
        try:
            parsed = ProducedReceipt.model_validate_json(body)
        except ValidationError:
            raise StoreError("GATE_RECEIPT_INTEGRITY") from None
        if canonical(parsed) != body:
            raise StoreError("GATE_RECEIPT_INTEGRITY")
        return parsed

    def _artifact_inputs(
        self,
        run: RunState,
        owner: UUID,
        grant: RepositoryGrant,
        lease: Lease | None,
        actions: ReplayActions,
        phases: Mapping[str, _Phase],
        source_bytes: bytes,
        reproduction_bytes: bytes,
        patch: RetainedPatch,
    ) -> tuple[ArtifactInput, ...]:
        files: list[ArtifactInput] = [
            ArtifactInput(name="source-manifest.json", content=source_bytes),
            ArtifactInput(name="reproduction.json", content=reproduction_bytes),
            ArtifactInput(
                name="patch.diff", content=patch.proposal.unified_diff.encode()
            ),
            ArtifactInput(
                name="patch-admission.json", content=canonical(patch.admission)
            ),
        ]
        baseline = phases["BASELINE"]
        files.extend(
            (
                ArtifactInput(
                    name="baseline-receipt.json",
                    content=canonical(baseline.receipt.signed),
                ),
                ArtifactInput(name="baseline-junit.xml", content=baseline.junit),
                ArtifactInput(
                    name="baseline-preparation.json",
                    content=canonical(baseline.preparation),
                ),
                ArtifactInput(
                    name="baseline-capability.json", content=baseline.capability_payload
                ),
                ArtifactInput(
                    name="baseline-materialization.json",
                    content=canonical(baseline.materialization),
                ),
            )
        )
        for phase in ("CANDIDATE", "EXISTING_SUITE", "CLEAN_REPLAY"):
            record = phases[phase]
            receipt = record.receipt
            if not isinstance(receipt, ProducedReceipt) or not isinstance(
                receipt.signed, SignedReplayReceipt
            ):
                raise StoreError("GATE_RECEIPT_SCHEMA_DENIED")
            prefix = {
                "CANDIDATE": "candidate",
                "EXISTING_SUITE": "suite",
                "CLEAN_REPLAY": "clean",
            }[phase]
            files.extend(
                (
                    ArtifactInput(
                        name=f"{prefix}-preparation.json",
                        content=canonical(record.preparation),
                    ),
                    ArtifactInput(
                        name=f"{prefix}-capability.json",
                        content=record.capability_payload,
                    ),
                    ArtifactInput(
                        name=f"{prefix}-final-workspace.json",
                        content=canonical(receipt.final),
                    ),
                    ArtifactInput(
                        name=f"{prefix}-materialization.json",
                        content=canonical(record.materialization),
                    ),
                )
            )
        integrity_facts = {
            "source_manifest_ref": run.source_manifest_ref,
            "reproduction_ref": run.reproduction_test_ref,
            "patch_ref": patch.admission.patch_ref,
            "patch_files": patch.admission.files_changed,
            "patch_lines": patch.admission.changed_lines,
            "phases": {
                phase: {
                    "execution_id": str(record.execution_id),
                    "operation_id": str(record.operation_id),
                    "workspace_operation_id": str(record.workspace_operation_id),
                    "plan_ref": plan_ref(record.plan),
                    "receipt_ref": _digest(canonical(record.receipt.signed)),
                    "junit_ref": _digest(record.junit),
                    "preparation_ref": _digest(canonical(record.preparation)),
                    "materialization_ref": _digest(canonical(record.materialization)),
                    "capability_ref": record.capability_ref,
                }
                for phase, record in phases.items()
            },
        }
        security_facts = {
            "owner_id": str(owner),
            "repository_id": str(run.repository_id),
            "source_commit": run.source_commit,
            "consent_current": admit(
                grant, owner, run.repository_id, run.source_commit
            ).allowed,
            "remaining_budget": run.remaining_budget.model_dump(mode="json"),
            "active_lease_required_at_retention_and_final_commit": True,
            "capability_refs": {
                phase: record.capability_ref for phase, record in phases.items()
            },
            "all_phase_origins_remote": all(
                record.preparation.source.evidence_origin == "REMOTE_SERVICE"
                for record in phases.values()
            ),
        }
        files.extend(
            (
                ArtifactInput(
                    name="integrity-evidence.json",
                    content=json.dumps(
                        integrity_facts,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                ),
                ArtifactInput(
                    name="security-evidence.json",
                    content=json.dumps(
                        security_facts,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                ),
            )
        )
        if len(files) > 32:
            raise StoreError("GATE_ARTIFACT_LIMIT")
        return tuple(files)

    def _derive(
        self, loaded: _Loaded, now: datetime
    ) -> tuple[tuple[GateEvidence, ...], tuple[GatePhaseBinding, ...]]:
        phases = loaded.phases
        bindings: list[GatePhaseBinding] = []
        inspections: dict[str, object] = {}
        for phase in PHASES:
            evidence = phases[phase]
            trust = self.phase_trust[phase]
            binding = evidence.preparation.binding
            if (
                binding.image_uuid != trust.image_uuid
                or binding.image_digest != trust.image_digest
                or binding.configuration_ref != trust.configuration_ref
                or binding.runner_id != trust.runner_id
                or evidence.plan.request.image_digest != trust.image_digest
                or evidence.plan.request.phase != phase
                or evidence.preparation.source.image_uuid != trust.image_uuid
                or evidence.preparation.source.tree_ref != binding.tree_ref
                or evidence.preparation.source.evidence_origin != "REMOTE_SERVICE"
                or (
                    phase != "BASELINE"
                    and evidence.preparation.source.tree_ref
                    != loaded.actions.patched_tree_ref
                )
            ):
                raise StoreError("GATE_RUNTIME_BINDING_DENIED")
            attestation, decision = recheck_retained_capability(
                evidence.capability_payload,
                evidence.capability_ref,
                evidence.plan,
                trust.capability_verifier,
                now=now,
            )
            if (
                decision.status != "CAPABILITY_REQUIREMENTS_MET"
                or binding.runner_id != trust.runner_id
                or attestation.payload.runner_id != trust.runner_id
            ):
                raise StoreError("GATE_CAPABILITY_DENIED")
            bindings.append(
                GatePhaseBinding(
                    phase=phase,
                    image_uuid=trust.image_uuid,
                    image_digest=trust.image_digest,
                    configuration_ref=trust.configuration_ref,
                    workspace_sha256=binding.tree_ref[7:],
                )
            )
            verifier = trust.receipt_verifier
            if phase == "BASELINE":
                record = evidence.receipt
                if not isinstance(record, RetainedReceipt):
                    raise StoreError("GATE_BASELINE_RECEIPT_INVALID")
                inspections[phase] = verifier.inspect_baseline(
                    record.signed,
                    evidence.plan,
                    evidence.junit,
                    execution_id=evidence.execution_id,
                    source_tree_ref=evidence.preparation.source.tree_ref,
                    expected=loaded.baseline_action.expected,
                    now=now,
                )
            else:
                record = evidence.receipt
                if not isinstance(record, ProducedReceipt) or not isinstance(
                    record.signed, SignedReplayReceipt
                ):
                    raise StoreError("GATE_PHASE_RECEIPT_INVALID")
                if phase == "EXISTING_SUITE":
                    inspections[phase] = verifier.inspect_existing_suite(
                        record.signed,
                        evidence.plan,
                        evidence.junit,
                        execution_id=evidence.execution_id,
                        source_tree_ref=evidence.preparation.source.tree_ref,
                        test_paths=evidence.plan.request.test_paths,
                        now=now,
                    )
                else:
                    inspections[phase] = verifier.inspect_replay(
                        record.signed,
                        evidence.plan,
                        evidence.junit,
                        execution_id=evidence.execution_id,
                        source_tree_ref=evidence.preparation.source.tree_ref,
                        test_name=loaded.actions.test_name,
                        now=now,
                    )
        if len(bindings) != 4:
            raise StoreError("GATE_PHASE_BINDINGS_INVALID")
        operation_ids = {phases[phase].operation_id for phase in PHASES}
        if len(operation_ids) != 4:
            raise StoreError("GATE_OPERATIONS_NOT_INDEPENDENT")
        workspace_operations = {
            phases[phase].workspace_operation_id for phase in PHASES
        }
        workspace_images = {
            phases[phase].materialization.result_image_uuid for phase in PHASES
        }
        if len(workspace_operations) != 4 or len(workspace_images) != 4:
            raise StoreError("GATE_WORKSPACES_NOT_INDEPENDENT")
        final_images: set[UUID] = set()
        for phase in REPLAY_PHASES:
            replay_receipt = phases[phase].receipt
            if not isinstance(replay_receipt, ProducedReceipt):
                raise StoreError("GATE_REPLAY_RECEIPT_INVALID")
            final_images.add(replay_receipt.final.image_uuid)
        if len(final_images) != 3:
            raise StoreError("GATE_REPLAY_OUTPUTS_NOT_INDEPENDENT")
        return self._gate_records(loaded, inspections, tuple(bindings)), tuple(bindings)

    def _bindings(self, loaded: _Loaded) -> tuple[GatePhaseBinding, ...]:
        """Resolve expected identities from operator config plus retained measures."""
        result: list[GatePhaseBinding] = []
        for phase in PHASES:
            trust = self.phase_trust[phase]
            preparation = loaded.phases[phase].preparation
            binding = preparation.binding
            if (
                binding.image_uuid != trust.image_uuid
                or binding.image_digest != trust.image_digest
                or binding.configuration_ref != trust.configuration_ref
                or binding.runner_id != trust.runner_id
                or preparation.plan.request.image_digest != trust.image_digest
                or preparation.source.image_uuid != trust.image_uuid
                or preparation.source.tree_ref != binding.tree_ref
            ):
                raise StoreError("GATE_RUNTIME_BINDING_DENIED")
            result.append(
                GatePhaseBinding(
                    phase=phase,
                    image_uuid=trust.image_uuid,
                    image_digest=trust.image_digest,
                    configuration_ref=trust.configuration_ref,
                    workspace_sha256=binding.tree_ref[7:],
                )
            )
        return tuple(result)

    def _context(
        self,
        run: RunState,
        loaded: _Loaded,
        workspace: str,
        bindings: tuple[GatePhaseBinding, ...],
    ) -> GateContext:
        if (
            run.reproduction_test_ref is None
            or run.evidence_manifest_ref is None
            or not run.patch_attempt_refs
        ):
            raise StoreError("GATE_RUN_NOT_READY")
        baseline = next(binding for binding in bindings if binding.phase == "BASELINE")
        return GateContext(
            run_id=run.run_id,
            repository_id=run.repository_id,
            run_version=run.version,
            source_commit=run.source_commit,
            patch_ref=run.patch_attempt_refs[-1],
            image_digest=baseline.image_digest,
            configuration_ref=baseline.configuration_ref,
            test_ref=run.reproduction_test_ref,
            workspace_sha256=workspace,
            manifest_ref=run.evidence_manifest_ref,
            phase_bindings=bindings,
        )

    def _gate_records(
        self,
        loaded: _Loaded,
        inspections: Mapping[str, object],
        bindings: tuple[GatePhaseBinding, ...],
    ) -> tuple[GateEvidence, ...]:
        run, phases = loaded.run, loaded.phases
        status: dict[GateName, Literal["PASS", "FAIL"]] = {}
        baseline = inspections["BASELINE"]
        if not isinstance(baseline, SignedBaselineInspection):
            raise StoreError("GATE_BASELINE_RECEIPT_INVALID")
        status["BASELINE_REPRODUCED"] = (
            "PASS"
            if baseline.status == "MATCHED_SIGNED_REPORT"
            and baseline.inspection.status == "MATCHED_ASSERTION"
            else "FAIL"
        )
        for phase, gate in (
            ("CANDIDATE", "PATCHED_REGRESSION_PASS"),
            ("EXISTING_SUITE", "EXISTING_SUITE_PASS"),
            ("CLEAN_REPLAY", "CLEAN_REPLAY_PASS"),
        ):
            inspected = inspections[phase]
            if phase == "EXISTING_SUITE":
                passed = (
                    isinstance(inspected, SignedSuiteInspection)
                    and inspected.status == "MATCHED_SIGNED_REPORT"
                )
            else:
                passed = (
                    isinstance(inspected, SignedReplayInspection)
                    and inspected.status == "MATCHED_SIGNED_REPORT"
                )
            status[cast(GateName, gate)] = "PASS" if passed else "FAIL"
        status["PATCH_SCOPE_PASS"] = "PASS"
        status["ARTIFACT_INTEGRITY_PASS"] = "PASS"
        status["SECURITY_GATE_PASS"] = "PASS"
        phase_bindings = {item.phase: item for item in bindings}
        artifact_id = {item.name: _digest(item.content) for item in loaded.artifacts}
        for phase, name in (
            ("CANDIDATE", "candidate-receipt.json"),
            ("EXISTING_SUITE", "suite-receipt.json"),
            ("CLEAN_REPLAY", "clean-receipt.json"),
        ):
            receipt = phases[phase].receipt
            if not isinstance(receipt, ProducedReceipt):
                raise StoreError("GATE_PHASE_RECEIPT_INVALID")
            artifact_id[name] = _digest(canonical(receipt.signed))
        result: list[GateEvidence] = []
        for gate in REQUIRED_GATES:
            phase = runtime_phase_for_gate(gate)
            record = phases[phase]
            identity = phase_bindings[phase]
            if gate == "BASELINE_REPRODUCED":
                ref = artifact_id["baseline-receipt.json"]
            elif gate == "PATCHED_REGRESSION_PASS":
                ref = artifact_id["candidate-receipt.json"]
            elif gate == "EXISTING_SUITE_PASS":
                ref = artifact_id["suite-receipt.json"]
            elif gate == "CLEAN_REPLAY_PASS":
                ref = artifact_id["clean-receipt.json"]
            elif gate == "PATCH_SCOPE_PASS":
                ref = artifact_id["patch-admission.json"]
            elif gate == "ARTIFACT_INTEGRITY_PASS":
                ref = artifact_id["integrity-evidence.json"]
            else:
                ref = artifact_id["security-evidence.json"]
            execution = gate in EXECUTION_GATES
            result.append(
                GateEvidence(
                    gate=gate,
                    phase=PHASE_BY_GATE[gate],
                    status=status[gate],
                    evidence_origin=(
                        "REMOTE_SERVICE" if execution else "TRUSTED_VERIFIER"
                    ),
                    execution_ref=ref,
                    execution_id=record.execution_id if execution else None,
                    operation_id=record.operation_id if execution else None,
                    image_uuid=identity.image_uuid,
                    image_digest=identity.image_digest,
                    configuration_ref=identity.configuration_ref,
                    source_commit=run.source_commit,
                    patch_ref=run.patch_attempt_refs[-1],
                    workspace_sha256=identity.workspace_sha256,
                )
            )
        return tuple(result)
