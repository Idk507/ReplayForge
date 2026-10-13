"""Pure reducer for unprivileged workflow progression; no service side effects."""

from packages.contracts.run import RunState, Stage

TERMINAL = frozenset(
    {
        Stage.PR_OPENED,
        Stage.PR_FAILED,
        Stage.CLOSED,
        Stage.NOT_VERIFIED,
        Stage.INCONCLUSIVE,
        Stage.POLICY_BLOCKED,
        Stage.CANCELLED,
    }
)
EDGES: dict[Stage, frozenset[Stage]] = {
    Stage.CREATED: frozenset({Stage.VALIDATING}),
    Stage.VALIDATING: frozenset({Stage.QUEUED, Stage.POLICY_BLOCKED}),
    Stage.QUEUED: frozenset({Stage.COLLECTING, Stage.POLICY_BLOCKED}),
    Stage.COLLECTING: frozenset(
        {Stage.TRIAGING, Stage.POLICY_BLOCKED, Stage.INCONCLUSIVE}
    ),
    Stage.TRIAGING: frozenset({Stage.REPRODUCING}),
    Stage.REPRODUCING: frozenset({Stage.PATCHING, Stage.INCONCLUSIVE}),
    Stage.PATCHING: frozenset({Stage.VERIFYING, Stage.INCONCLUSIVE}),
    Stage.VERIFYING: frozenset(
        {Stage.PATCHING, Stage.NOT_VERIFIED, Stage.INCONCLUSIVE}
    ),
    Stage.VERIFIED: frozenset({Stage.AWAITING_APPROVAL}),
    Stage.AWAITING_APPROVAL: frozenset({Stage.CLOSED}),
}


class TransitionError(Exception):
    """Stable safe error code; never includes run contents."""


def transition(state: RunState, target: Stage, *, expected_version: int) -> RunState:
    """Validate and produce a new checkpoint without mutating identity.

    A caller must persist with transactional compare-and-swap; this function
    alone is not concurrency control. Privileged transitions remain unavailable
    until authenticated evidence/approval services are implemented.
    """
    state = RunState.model_validate(state.model_dump())
    if type(expected_version) is not int or state.version != expected_version:
        raise TransitionError("STALE_VERSION")
    if state.current_stage in TERMINAL:
        raise TransitionError("TERMINAL_STATE")
    if target == Stage.VERIFIED:
        raise TransitionError("VERIFIER_REQUIRED")
    if target in {Stage.PR_CREATING, Stage.PR_OPENED, Stage.PR_FAILED}:
        raise TransitionError("APPROVAL_SERVICE_REQUIRED")
    if state.cancellation_requested and target != Stage.CANCELLED:
        raise TransitionError("CANCELLATION_PENDING")
    if target == Stage.TRIAGING and not state.context_refs:
        raise TransitionError("CONTEXT_EVIDENCE_REQUIRED")
    if target == Stage.PATCHING and (
        state.baseline_result_ref is None or state.reproduction_test_ref is None
    ):
        raise TransitionError("BASELINE_EVIDENCE_REQUIRED")
    if target == Stage.VERIFYING and not state.patch_attempt_refs:
        raise TransitionError("PATCH_EVIDENCE_REQUIRED")
    if target != Stage.CANCELLED and target not in EDGES.get(
        state.current_stage, frozenset()
    ):
        raise TransitionError("INVALID_TRANSITION")
    data = state.model_dump()
    data.update(
        current_stage=target,
        version=state.version + 1,
        cancellation_requested=state.cancellation_requested
        or target == Stage.CANCELLED,
    )
    return RunState.model_validate(data)
