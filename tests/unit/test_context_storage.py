"""Reject invalid artifact payloads before attempting database connections."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.contracts.context import ContextBundle, ContextFile
from packages.contracts.run import RunState, Stage
from packages.policies.transitions import TransitionError, transition
from services.persistence.store import Lease, Store, StoreError


def test_forged_sanitized_hash_denied_before_database() -> None:
    bundle = ContextBundle(
        source_commit="a" * 40,
        sanitized_errors="",
        files=(
            ContextFile(
                path="src/a.py",
                source_sha256="a" * 64,
                sanitized_sha256="b" * 64,
                content="x",
                parse_status="PARSED",
            ),
        ),
    )
    lease = Lease(
        run_id=uuid4(), worker_id=uuid4(), token=uuid4(), leased_until=datetime.now(UTC)
    )
    with pytest.raises(StoreError, match="CONTEXT_INTEGRITY"):
        Store("unused").store_context(lease, bundle, expected_version=0)


def test_invalid_artifact_identifier_denied_before_database() -> None:
    with pytest.raises(StoreError, match="ARTIFACT_ID_INVALID"):
        Store("unused").context(uuid4(), uuid4(), "../../secret")


def test_cannot_triage_without_context_reference() -> None:
    state = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=uuid4(),
        trace_id=uuid4(),
        source_commit="a" * 40,
        policy_version="v1",
        current_stage=Stage.COLLECTING,
    )
    with pytest.raises(TransitionError, match="CONTEXT_EVIDENCE_REQUIRED"):
        transition(state, Stage.TRIAGING, expected_version=0)
