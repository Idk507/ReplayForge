"""Local collection graph; PostgreSQL domain checkpoints are authoritative."""

from typing import Literal, Self
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from packages.contracts.context import ContextBundle
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit, allowed_entry
from packages.policies.transitions import TERMINAL
from services.context.collector import ContextError, collect
from services.persistence.store import Lease, Store, StoreError
from services.repository.snapshot import Snapshot

FailureReason = Literal["SCOPE_DENIED", "SOURCE_MISMATCH", "CONTEXT_REJECTED"]


class CollectionError(Exception):
    """Stable orchestration boundary code, without source or credentials."""


class CollectionDelta(BaseModel):
    """Only trusted reducer may commit this closed planning vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    target: Literal[
        Stage.VALIDATING,
        Stage.QUEUED,
        Stage.COLLECTING,
        Stage.TRIAGING,
        Stage.POLICY_BLOCKED,
        Stage.INCONCLUSIVE,
    ]
    context: ContextBundle | None = Field(default=None, repr=False)
    failure_reason: FailureReason | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.target == Stage.TRIAGING) != (self.context is not None):
            raise ValueError("CONTEXT_DELTA_REQUIRED")
        if (self.target in {Stage.POLICY_BLOCKED, Stage.INCONCLUSIVE}) != (
            self.failure_reason is not None
        ):
            raise ValueError("FAILURE_REASON_REQUIRED")
        return self


class CollectionState(BaseModel):
    """Ephemeral graph state; never persisted or streamed by LangGraph."""

    run: RunState
    delta: CollectionDelta | None = None


def plan(state: RunState) -> CollectionDelta:
    """Pure early-stage routing without arbitrary updates or privileged stages."""
    routes = {
        Stage.CREATED: Stage.VALIDATING,
        Stage.VALIDATING: Stage.QUEUED,
        Stage.QUEUED: Stage.COLLECTING,
    }
    if state.current_stage not in routes:
        raise CollectionError("COLLECTION_STAGE_DENIED")
    return CollectionDelta.model_validate({"target": routes[state.current_stage]})


def run_collection(
    store: Store,
    owner: UUID,
    lease: Lease,
    snapshot: Snapshot,
    paths: tuple[str, ...],
    errors: str,
    *,
    known_secrets: tuple[SecretStr, ...] = (),
) -> RunState:
    """Resume collection from persisted stage; stop before any triage inference.

    All raw inputs remain private closure data. Only reducer writes the database.
    No native LangGraph saver/hosted tracing is enabled, avoiding duplicate
    checkpoint authority and persistence of uncommitted context text.
    """
    persisted = store.get(lease.run_id, owner)
    if persisted.current_stage in TERMINAL or persisted.current_stage == Stage.TRIAGING:
        return persisted
    if persisted.current_stage not in {
        Stage.CREATED,
        Stage.VALIDATING,
        Stage.QUEUED,
        Stage.COLLECTING,
    }:
        raise CollectionError("COLLECTION_STAGE_DENIED")

    def planning(state: CollectionState) -> dict[str, CollectionDelta]:
        run = RunState.model_validate(state.run.model_dump())
        if run.current_stage == Stage.CREATED:
            return {"delta": plan(run)}
        try:
            grant = store.repository(run.repository_id, owner)
        except StoreError as error:
            if str(error) != "NOT_FOUND":
                raise
            return {
                "delta": CollectionDelta(
                    target=Stage.POLICY_BLOCKED, failure_reason="SCOPE_DENIED"
                )
            }
        if not admit(grant, owner, run.repository_id, run.source_commit).allowed or any(
            not allowed_entry(grant, path, "100644") for path in paths
        ):
            return {
                "delta": CollectionDelta(
                    target=Stage.POLICY_BLOCKED, failure_reason="SCOPE_DENIED"
                )
            }
        if snapshot.source_commit != run.source_commit:
            return {
                "delta": CollectionDelta(
                    target=Stage.POLICY_BLOCKED, failure_reason="SOURCE_MISMATCH"
                )
            }
        if run.current_stage == Stage.COLLECTING:
            try:
                bundle = collect(snapshot, paths, errors, known_secrets=known_secrets)
            except ContextError:
                return {
                    "delta": CollectionDelta(
                        target=Stage.INCONCLUSIVE, failure_reason="CONTEXT_REJECTED"
                    )
                }
            return {"delta": CollectionDelta(target=Stage.TRIAGING, context=bundle)}
        return {"delta": plan(run)}

    def reducer(state: CollectionState) -> dict[str, RunState | None]:
        if state.delta is None:
            raise CollectionError("MISSING_DELTA")
        delta = CollectionDelta.model_validate(state.delta.model_dump())
        if delta.context is not None:
            updated = store.store_context(
                lease, delta.context, expected_version=state.run.version
            )
        else:
            updated = store.advance(
                lease,
                delta.target,
                expected_version=state.run.version,
                failure_reason=delta.failure_reason,
            )
        return {"run": updated, "delta": None}

    def route(state: CollectionState) -> str:
        return (
            END
            if state.run.current_stage in TERMINAL
            or state.run.current_stage == Stage.TRIAGING
            else "plan"
        )

    builder: StateGraph[CollectionState, None, CollectionState, CollectionState] = (
        StateGraph(CollectionState)
    )
    builder.add_node("plan", planning)
    builder.add_node("reducer", reducer)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "reducer")
    builder.add_conditional_edges("reducer", route)
    with tracing_context(enabled=False):
        result = builder.compile().invoke(
            CollectionState(run=persisted),
            config={"recursion_limit": 16, "callbacks": []},
        )
    return RunState.model_validate(result["run"])
