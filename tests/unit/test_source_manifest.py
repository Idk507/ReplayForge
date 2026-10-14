"""Source provenance stores bounded metadata, never repository file bytes."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.source import SourceManifest


def manifest(**changes):
    data = dict(
        repository_id=uuid4(),
        source_commit="a" * 40,
        root_tree="b" * 40,
        evidence_origin="TEST_TRANSPORT",
        observed_at="2026-10-14T00:00:00Z",
        trace_id=uuid4(),
        files=(),
    )
    data.update(changes)
    return SourceManifest.model_validate(data)


def test_manifest_is_metadata_only_and_cannot_claim_raw_commit_verification() -> None:
    proof = manifest()
    assert not proof.raw_commit_verified
    assert proof.evidence_origin == "TEST_TRANSPORT"
    with pytest.raises(ValidationError):
        manifest(raw_commit_verified=True)
    with pytest.raises(ValidationError):
        manifest(content="raw source must not enter manifest")


@pytest.mark.parametrize(
    "changes",
    [
        {"root_tree": "main"},
        {"evidence_origin": "NOT_RECORDED"},
        {"observed_at": "2026-10-14T00:00:00"},
        {
            "files": [
                {
                    "path": "../secret",
                    "blob_id": "c" * 40,
                    "sha256": "d" * 64,
                    "size_bytes": 1,
                }
            ]
        },
    ],
)
def test_invalid_manifest_is_rejected(changes) -> None:
    with pytest.raises(ValidationError):
        manifest(**changes)


def test_observed_context_requires_manifest_before_database() -> None:
    from datetime import UTC, datetime

    from packages.contracts.context import ContextBundle
    from services.persistence.store import Lease, Store, StoreError

    lease = Lease(
        run_id=uuid4(), worker_id=uuid4(), token=uuid4(), leased_until=datetime.now(UTC)
    )
    context = ContextBundle(
        source_commit="a" * 40,
        sanitized_errors="",
        commit_identity="GITHUB_HTTPS_METADATA",
        evidence_origin="TEST_TRANSPORT",
    )
    with pytest.raises(StoreError, match="SOURCE_MANIFEST_REQUIRED"):
        Store("unused").store_context(lease, context, expected_version=0)
    with pytest.raises(StoreError, match="SOURCE_MANIFEST_MISMATCH"):
        Store("unused").store_context(
            lease,
            context,
            expected_version=0,
            source_manifest=manifest(source_commit="c" * 40),
        )
