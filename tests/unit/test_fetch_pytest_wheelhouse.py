"""Wheelhouse fetch: exact hosts, sizes and hashes; no network in these tests."""

import hashlib
import json

import pytest

from scripts.fetch_pytest_wheelhouse import fetch, main

BODY = b"synthetic wheel bytes"


def manifest(
    tmp_path, *, url=None, digest=None, size=None, filename="a-1.0-py3-none-any.whl"
):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "filename": filename,
                        "url": url or "https://files.pythonhosted.org/x/" + filename,
                        "sha256": "sha256:"
                        + (digest or hashlib.sha256(BODY).hexdigest()),
                        "size_bytes": size if size is not None else len(BODY),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_downloads_verifies_and_keeps_only_matching_files(tmp_path):
    dest = tmp_path / "wheels"
    names = fetch(manifest(tmp_path), dest, lambda url: BODY)
    assert names == ["a-1.0-py3-none-any.whl"]
    assert (dest / names[0]).read_bytes() == BODY
    assert not list(dest.glob("*.part"))


def test_existing_verified_file_is_not_downloaded_again(tmp_path):
    dest = tmp_path / "wheels"
    fetch(manifest(tmp_path), dest, lambda url: BODY)

    def forbidden(url):
        raise AssertionError("must not download")

    assert fetch(manifest(tmp_path), dest, forbidden) == ["a-1.0-py3-none-any.whl"]


@pytest.mark.parametrize(
    "bad",
    [
        {"digest": "0" * 64},
        {"size": 3},
        {"url": "http://files.pythonhosted.org/x/a.whl"},
        {"url": "https://evil.example/a-1.0-py3-none-any.whl"},
        {"filename": "../escape.whl"},
    ],
)
def test_mismatch_or_untrusted_source_is_refused_and_nothing_is_kept(tmp_path, bad):
    dest = tmp_path / "wheels"
    with pytest.raises(ValueError):
        fetch(manifest(tmp_path, **bad), dest, lambda url: BODY)
    assert not list(dest.glob("*.whl")) and not list(dest.glob("*.part"))


def test_cli_is_offline_without_run(capsys):
    assert main([]) == 2
    assert "NOT_RUN" in capsys.readouterr().out
