"""Generated source remains inert and bound to one consented run."""

from uuid import uuid4

import pytest

from packages.contracts.repair import ReproductionProposal
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from services.orchestrator.reproduction import (
    ReproductionHandoffError,
    bind_reproduction,
    load_reproduction,
)


def fixture() -> tuple[RunState, RepositoryGrant, ReproductionProposal]:
    owner, repository = uuid4(), uuid4()
    run = RunState(
        run_id=uuid4(),
        incident_id=uuid4(),
        repository_id=repository,
        source_commit="a" * 40,
        policy_version="repository-v1",
        trace_id=uuid4(),
        current_stage=Stage.TRIAGING,
        context_refs=("sha256:" + "b" * 64,),
        source_manifest_ref="sha256:" + "c" * 64,
    )
    grant = RepositoryGrant(
        repository_id=repository,
        owner_id=owner,
        remote_url="https://github.com/example/fixture",
        commits=(run.source_commit,),
        approved_paths=("tests/",),
        consent=True,
    )
    proposal = ReproductionProposal(
        target_path="tests/test_checkout.py",
        behavior="retry once",
        failure_signature="2 != 1",
        test_source="def test_retry():\n    assert False\n",
    )
    return run, grant, proposal


def test_roundtrip_is_inert_and_bound_to_checkpoint() -> None:
    run, grant, proposal = fixture()
    artifact = bind_reproduction(proposal, run, grant, grant.owner_id)
    ref = artifact.artifact_ref()
    retained = run.model_copy(update={"reproduction_test_ref": ref})
    assert (
        load_reproduction(
            artifact.canonical_bytes(), ref, retained, grant, grant.owner_id
        )
        == artifact
    )
    assert run.reproduction_test_ref is None
    assert artifact.proposal.test_source_sha256() == proposal.test_source_sha256()


@pytest.mark.parametrize(
    "change",
    [
        {"source_commit": "d" * 40},
        {"run_id": uuid4()},
        {"context_refs": ("sha256:" + "e" * 64,)},
        {"source_manifest_ref": "sha256:" + "f" * 64},
        {"cancellation_requested": True},
        {"current_stage": Stage.PATCHING},
    ],
)
def test_rejects_checkpoint_mutation(change: dict[str, object]) -> None:
    run, grant, proposal = fixture()
    artifact = bind_reproduction(proposal, run, grant, grant.owner_id)
    changed = run.model_copy(
        update={"reproduction_test_ref": artifact.artifact_ref(), **change}
    )
    with pytest.raises(ReproductionHandoffError):
        load_reproduction(
            artifact.canonical_bytes(),
            artifact.artifact_ref(),
            changed,
            grant,
            grant.owner_id,
        )


def test_rejects_tampering_wrong_owner_and_unbound_reference() -> None:
    run, grant, proposal = fixture()
    artifact = bind_reproduction(proposal, run, grant, grant.owner_id)
    for body, caller in [
        (artifact.canonical_bytes() + b" ", grant.owner_id),
        (artifact.canonical_bytes(), uuid4()),
        (artifact.canonical_bytes(), grant.owner_id),
    ]:
        with pytest.raises(ReproductionHandoffError):
            load_reproduction(body, artifact.artifact_ref(), run, grant, caller)
