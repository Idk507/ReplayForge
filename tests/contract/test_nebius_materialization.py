"""Small inert source uploads; guest programs are never executed on the host."""

import json
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from packages.contracts.repair import ReproductionProposal
from packages.contracts.sandbox import RunnerError
from services.orchestrator.reproduction import ReproductionArtifact
from services.runner.materialization import NebiusMaterializer, prepare_upload
from services.runner.receipts import canonical
from tests.contract.test_sandbox_smoke import config
from tests.unit.test_source_archive import source
from tests.unit.test_source_manifest import manifest


def fixture():
    files = {"src/a.py": b"x=1\n"}
    proof = manifest(files=(source("src/a.py", files["src/a.py"]),))
    reproduction = ReproductionArtifact(
        run_id=uuid4(),
        repository_id=proof.repository_id,
        source_commit=proof.source_commit,
        source_manifest_ref="sha256:" + sha256(canonical(proof)).hexdigest(),
        context_refs=("sha256:" + "b" * 64,),
        proposal=ReproductionProposal(
            target_path="tests/test_a.py",
            behavior="x is two",
            failure_signature="1 != 2",
            test_source="def test_a():\n    assert 1 == 2\n",
        ),
    )
    return prepare_upload(config().image_uuid, proof, files, reproduction)


def test_upload_is_saved_offline_and_independently_measured():
    upload = fixture()
    operation, image = uuid4(), uuid4()
    calls = []
    submitted = {}
    claims = []
    retained = []

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            submitted.update(json.loads(request.content))
            return httpx.Response(201, json={"uuid": str(operation)})
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(operation),
                    "kind": "instance",
                    "status": "SUCCESS",
                    "image_uuid": str(config().image_uuid),
                    "result_image_uuid": str(image),
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": 0,
                                "signal": None,
                                "timed_out": False,
                            }
                        }
                    },
                },
            )
        return httpx.Response(200, content=upload.archive)

    with NebiusMaterializer(config(), transport=httpx.MockTransport(handle)) as backend:
        assert (
            backend.submit(
                upload, lambda plan: claims.append(plan) or True, retained.append
            )
            == operation
        )
        result = backend.collect(upload.plan, operation, lambda: None)
        assert result.measurement.tree_ref == upload.plan.workspace_ref
        assert result.measurement.files == 2 and result.result_image_uuid == image
        assert not result.can_authorize_execution
        with pytest.raises(RunnerError):
            backend.submit(upload, lambda p: True, retained.append)
    assert len(claims) == len(retained) == 1 and len(calls) == 3
    assert submitted["networking"] == {"enabled": False}
    assert submitted["shell"] is False and submitted["disposable"] is False
    assert submitted["env"] == {} and submitted["preserve_env"] is False
    assert submitted["uid"] == submitted["gid"] == 1000
    assert submitted["stdin"]["encoding"] == "base64"


def test_denied_or_uncertain_submission_is_never_retried():
    upload = fixture()
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private provider response", request=request)

    claimed = []

    def claim(plan):
        if claimed:
            return False
        claimed.append(plan)
        return True

    transport = httpx.MockTransport(handle)
    with NebiusMaterializer(config(), transport=transport) as backend:
        with pytest.raises(RunnerError, match="S_MATERIALIZATION_SUBMISSION_UNKNOWN"):
            backend.submit(upload, claim, lambda op: None)
    with NebiusMaterializer(config(), transport=transport) as restarted:
        with pytest.raises(RunnerError, match="S_MATERIALIZATION_ALREADY_CLAIMED"):
            restarted.submit(upload, claim, lambda op: None)
    assert len(calls) == 1


def test_tampered_archive_is_rejected_before_intent_or_network():
    upload = fixture()
    claims = []
    with NebiusMaterializer(
        config(), transport=httpx.MockTransport(lambda r: pytest.fail("no HTTP"))
    ) as backend:
        with pytest.raises(RunnerError):
            backend.submit(
                upload.model_copy(update={"archive": b"changed"}),
                lambda p: claims.append(p) or True,
                lambda op: None,
            )
    assert not claims


def test_live_materialization_is_explicit():
    with pytest.raises(RunnerError, match="S_LIVE_NOT_AUTHORIZED"):
        NebiusMaterializer(config())


@pytest.mark.parametrize("fault", ["process", "image", "tree", "operation"])
def test_bad_remote_facts_never_become_a_prepared_result(fault):
    upload = fixture()
    operation, image = uuid4(), uuid4()

    def handle(request):
        if request.method == "POST":
            return httpx.Response(201, json={"uuid": str(operation)})
        if "/operations/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "uuid": str(uuid4() if fault == "operation" else operation),
                    "kind": "instance",
                    "status": "SUCCESS",
                    "image_uuid": str(config().image_uuid),
                    "result_image_uuid": str(
                        config().image_uuid if fault == "image" else image
                    ),
                    "metadata": {
                        "result": {
                            "state": {
                                "exit_code": 1 if fault == "process" else 0,
                                "signal": None,
                                "timed_out": False,
                            }
                        }
                    },
                },
            )
        return httpx.Response(
            200,
            content=upload.archive.replace(b"x=1", b"x=2")
            if fault == "tree"
            else upload.archive,
        )

    with NebiusMaterializer(config(), transport=httpx.MockTransport(handle)) as backend:
        backend.submit(upload, lambda p: True, lambda op: None)
        with pytest.raises(RunnerError):
            backend.collect(upload.plan, operation, lambda: None)


def test_materialization_plan_rejects_unsafe_paths():
    upload = fixture()
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(upload.plan).model_validate(
            upload.plan.model_dump() | {"paths": ["../escape"]}
        )
