"""Tiny inert archives only; no code execution or external calls."""

import io
import tarfile
from uuid import uuid4

import httpx
import pytest

from packages.contracts.sandbox import RunnerError
from services.runner.workspace import archive_identity, measure_workspace
from tests.contract.test_sandbox_smoke import config


def archive(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as output:
        for name, content, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
            item.size = len(content)
            output.addfile(item, io.BytesIO(content))
    return result.getvalue()


def test_measurement_hashes_actual_bytes_independent_of_archive_order():
    entries = [("a.py", b"x=1\n", tarfile.REGTYPE), ("tests", b"", tarfile.DIRTYPE)]
    original = archive_identity(archive(entries))
    assert original == archive_identity(archive(list(reversed(entries))))
    assert original[1:] == (1, 4)
    assert original != archive_identity(archive([("a.py", b"x=2\n", tarfile.REGTYPE)]))


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("a//b", tarfile.REGTYPE),
        ("a\\b", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hard", tarfile.LNKTYPE),
        ("pipe", tarfile.FIFOTYPE),
    ],
)
def test_workspace_rejects_unsafe_entries(name, kind):
    with pytest.raises(RunnerError):
        archive_identity(archive([(name, b"", kind)]))


@pytest.mark.parametrize(
    "entries",
    [
        [("a", b"x", tarfile.REGTYPE), ("A", b"y", tarfile.REGTYPE)],
        [("a", b"x", tarfile.REGTYPE), ("a/b", b"y", tarfile.REGTYPE)],
        [("empty", b"", tarfile.DIRTYPE)],
    ],
)
def test_workspace_rejects_aliases_and_empty_tree(entries):
    with pytest.raises(RunnerError):
        archive_identity(archive(entries))


@pytest.mark.parametrize(
    "body", [b"", b"invalid", b"x" * 65537], ids=["empty", "invalid", "oversized"]
)
def test_workspace_rejects_bad_archive(body):
    with pytest.raises(RunnerError):
        archive_identity(body)


def test_provider_measurement_is_fixed_read_only_and_offline_by_default():
    image = uuid4()
    body = archive([("a.py", b"x=1\n", tarfile.REGTYPE)])
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/sandboxes/v1/inspect/{image}/archive"
        assert request.url.params["path"] == "/workspace/repo"
        return httpx.Response(200, content=body)

    measured = measure_workspace(
        config(), image, transport=httpx.MockTransport(handler)
    )
    assert measured.tree_ref == archive_identity(body)[0]
    assert measured.evidence_origin == "TEST_TRANSPORT"
    assert len(calls) == 1
    with pytest.raises(RunnerError, match="S_POLICY_DENIED"):
        measure_workspace(config(), image)


@pytest.mark.parametrize("status", [302, 403, 500])
def test_provider_failure_never_produces_measurement(status):
    with pytest.raises(RunnerError):
        measure_workspace(
            config(),
            uuid4(),
            transport=httpx.MockTransport(lambda r: httpx.Response(status)),
        )


def test_permission_changes_affect_identity_and_special_modes_are_denied():
    body = archive([("a.py", b"x", tarfile.REGTYPE)])
    changed = bytearray(body)
    # Build valid tar headers through tarfile rather than modifying checksums.
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(changed), mode="r:") as source:
        item = source.next()
        item.mode = 0o755
        with tarfile.open(fileobj=output, mode="w") as target:
            target.addfile(item, io.BytesIO(b"x"))
    assert archive_identity(body) != archive_identity(output.getvalue())
    output = io.BytesIO()
    item.mode = 0o4755
    with tarfile.open(fileobj=output, mode="w") as target:
        target.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(RunnerError):
        archive_identity(output.getvalue())
