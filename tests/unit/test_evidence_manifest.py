import pytest

from services.verifier.manifest import ArtifactInput, build_manifest, verify_manifest


def test_manifest_is_stable_and_content_addressed() -> None:
    workspace = "a" * 64
    first = build_manifest(
        workspace,
        (
            ArtifactInput(name="stderr.txt", content=b"failure"),
            ArtifactInput(name="result.xml", content=b"<x/>"),
        ),
    )
    second = build_manifest(
        workspace,
        (
            ArtifactInput(name="result.xml", content=b"<x/>"),
            ArtifactInput(name="stderr.txt", content=b"failure"),
        ),
    )
    assert first == second
    assert first.entries[0].name == "result.xml"


def test_manifest_rejects_ambiguous_names() -> None:
    with pytest.raises(ValueError, match="ARTIFACT_NAME_COLLISION"):
        build_manifest(
            "a" * 64,
            (
                ArtifactInput(name="x.txt", content=b"1"),
                ArtifactInput(name="x.txt", content=b"2"),
            ),
        )


def test_manifest_verification_rejects_tampered_bytes_or_names() -> None:
    artifacts = (ArtifactInput(name="result.xml", content=b"<pass/>"),)
    manifest = build_manifest("a" * 64, artifacts)
    assert verify_manifest(manifest, artifacts)
    assert not verify_manifest(
        manifest, (ArtifactInput(name="result.xml", content=b"<fail/>"),)
    )
    assert not verify_manifest(
        manifest, (ArtifactInput(name="other.xml", content=b"<pass/>"),)
    )
