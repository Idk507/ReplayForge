"""Apply tiny byte fixtures as data; never import or execute repository code."""

import io
import tarfile
from hashlib import sha256
from uuid import uuid4

import pytest

from packages.contracts.repair import PatchProposal, ReproductionProposal
from packages.contracts.run import Stage
from services.orchestrator.reproduction import ReproductionArtifact
from services.persistence.patch import RetainedPatch
from services.runner.candidate_workspace import prepare_candidate, workspace_for_phase
from services.runner.patches import PatchDenied, admit_patch
from services.runner.receipts import canonical
from tests.unit.test_patch_admission import DIFF, context
from tests.unit.test_source_archive import source
from tests.unit.test_source_manifest import manifest


def inputs(body=b"x=1\n", diff=DIFF, source_path="src/a.py"):
    run, grant, owner = context()
    if source_path.startswith("checkout/"):
        grant = grant.model_copy(update={"approved_paths": ("checkout/",)})
    run = run.model_copy(update={"context_refs": ("sha256:" + "b" * 64,)})
    files = {source_path: body}
    proof = manifest(files=(source(source_path, body),)).model_copy(
        update={"repository_id": run.repository_id, "source_commit": run.source_commit}
    )
    reproduction = ReproductionArtifact(
        run_id=run.run_id,
        repository_id=run.repository_id,
        source_commit=run.source_commit,
        source_manifest_ref="sha256:" + sha256(canonical(proof)).hexdigest(),
        context_refs=("sha256:" + "b" * 64,),
        proposal=ReproductionProposal(
            target_path="tests/test_a.py",
            behavior="x is two",
            failure_signature="1 != 2",
            test_source="def test_a():\n    assert 1 == 2\n",
        ),
    )
    run = run.model_copy(
        update={
            "source_manifest_ref": reproduction.source_manifest_ref,
            "reproduction_test_ref": reproduction.artifact_ref(),
        }
    )
    admission = admit_patch(
        diff,
        run,
        grant,
        owner,
        source_commit=run.source_commit,
        source_manifest_ref=run.source_manifest_ref,
        baseline_result_ref=run.baseline_result_ref,
        reproduction_test_ref=run.reproduction_test_ref,
    )
    patch = RetainedPatch(
        proposal=PatchProposal(
            unified_diff=diff,
            files_changed=(source_path,),
            justification="fixture",
            expected_test_delta="pass",
            risk_notes="not executed",
        ),
        admission=admission,
    )
    run = run.model_copy(
        update={
            "current_stage": Stage.VERIFYING,
            "patch_attempt_refs": (admission.patch_ref,),
        }
    )
    return proof, files, reproduction, patch, run, grant, owner


def call(values, image_uuid=None):
    proof, files, reproduction, patch, run, grant, owner = values
    return prepare_candidate(
        image_uuid or uuid4(),
        proof,
        files,
        reproduction,
        patch=patch,
        run=run,
        grant=grant,
        owner=owner,
    )


def test_candidate_contains_actual_patch_and_byte_identical_reproduction():
    values = inputs()
    candidate = call(values)
    with tarfile.open(fileobj=io.BytesIO(candidate.upload.archive), mode="r:") as tree:
        assert tree.extractfile("src/a.py").read() == b"x=2\n"
        assert (
            tree.extractfile("tests/test_a.py").read()
            == values[2].proposal.test_source.encode()
        )
    assert candidate.original.workspace_ref != candidate.upload.plan.workspace_ref
    clean = workspace_for_phase(candidate, "CLEAN_REPLAY")
    with tarfile.open(fileobj=io.BytesIO(clean.upload.archive), mode="r:") as tree:
        assert tree.extractfile("src/a.py").read() == b"x=2\n"
        assert (
            tree.extractfile("tests/test_a.py").read()
            == values[2].proposal.test_source.encode()
        )
    assert clean.upload.plan.workspace_ref == candidate.upload.plan.workspace_ref
    assert clean.upload.plan.workspace_ref != candidate.original.workspace_ref
    assert candidate.patch_ref == values[3].admission.patch_ref
    assert not candidate.can_authorize_execution
    assert "x=2" not in repr(candidate)


def test_candidate_materializes_explicit_checkout_source_and_unchanged_test():
    path = "checkout/payments.py"
    diff = DIFF.replace("src/a.py", path)
    values = list(inputs(diff=diff, source_path=path))
    values[5] = values[5].model_copy(update={"approved_paths": ("checkout/",)})
    candidate = call(tuple(values))
    with tarfile.open(fileobj=io.BytesIO(candidate.upload.archive), mode="r:") as tree:
        assert tree.extractfile(path).read() == b"x=2\n"
        assert (
            tree.extractfile("tests/test_a.py").read()
            == values[2].proposal.test_source.encode()
        )


@pytest.mark.parametrize("path", ["checkout/tests/test_a.py", "checkout-other/a.py"])
def test_candidate_rejects_checkout_test_or_neighbor_root(path):
    original = "checkout/payments.py"
    diff = DIFF.replace("src/a.py", original).replace(original, path)
    values = list(inputs(diff=DIFF.replace("src/a.py", original), source_path=original))
    values[5] = values[5].model_copy(update={"approved_paths": ("checkout/",)})
    values[3] = values[3].model_copy(
        update={
            "proposal": values[3].proposal.model_copy(update={"unified_diff": diff})
        }
    )
    with pytest.raises(PatchDenied):
        call(tuple(values))


def test_replay_phases_use_same_patched_bytes_for_separate_fresh_runs():
    values = inputs()
    first_build = call(values)
    second_build = call(values, image_uuid=first_build.original.image_uuid)
    candidate = workspace_for_phase(first_build, "CANDIDATE")
    clean = workspace_for_phase(second_build, "CLEAN_REPLAY")
    assert candidate is not clean
    assert candidate.upload is not clean.upload
    assert candidate.upload.plan.workspace_ref == clean.upload.plan.workspace_ref
    assert candidate.upload.archive == clean.upload.archive
    assert candidate.patch_ref == clean.patch_ref
    assert candidate.original == clean.original


@pytest.mark.parametrize(
    "fault",
    ["stale_source", "context_mismatch", "scope", "patch_hash", "cancel", "stage"],
)
def test_candidate_rejects_wrong_source_or_unbound_patch(fault):
    values = list(inputs())
    if fault == "stale_source":
        values[1] = {"src/a.py": b"x=3\n"}
    if fault == "context_mismatch":
        values = list(inputs(b"x=3\n"))
    if fault == "scope":
        values[5] = values[5].model_copy(update={"consent": False})
    if fault == "patch_hash":
        values[4] = values[4].model_copy(
            update={"patch_attempt_refs": ("sha256:" + "f" * 64,)}
        )
    if fault == "cancel":
        values[4] = values[4].model_copy(update={"cancellation_requested": True})
    if fault == "stage":
        values[4] = values[4].model_copy(update={"current_stage": Stage.PATCHING})
    from packages.contracts.sandbox import RunnerError

    with pytest.raises((PatchDenied, RunnerError)):
        call(values)


def test_hunk_offsets_and_context_are_applied_exactly_without_fuzzy_matching():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -2 +2 @@\n-x=1\n+x=2\n"
    )
    result = call(inputs(b"# header\nx=1\n", diff))
    with tarfile.open(fileobj=io.BytesIO(result.upload.archive)) as tree:
        assert tree.extractfile("src/a.py").read() == b"# header\nx=2\n"
    with pytest.raises(PatchDenied):
        call(inputs(b"# header\nx=1\n", diff.replace("+2 @@", "+1 @@")))


def test_candidate_rejects_reproduction_from_different_context():
    values = list(inputs())
    values[2] = values[2].model_copy(update={"context_refs": ("sha256:" + "c" * 64,)})
    with pytest.raises(PatchDenied, match="PATCH_WORKSPACE_BINDING_DENIED"):
        call(values)
