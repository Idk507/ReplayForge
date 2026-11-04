"""Secret scan: finds credential shapes, reports locations only, honors exclusions."""

from scripts.check_secrets import scan

FAKE_PAT = "ghp_" + "A1b2C3d4" * 5


def test_finds_credential_shapes_without_returning_values(tmp_path):
    (tmp_path / "app.py").write_text(f'x = 1\nTOKEN = "{FAKE_PAT}"\n', encoding="utf-8")
    (tmp_path / "key.txt").write_text(
        "-----BEGIN PRIVATE KEY-----\n" + "QUJD" * 40 + "\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    found = scan(tmp_path, ["app.py", "key.txt"])
    assert ("app.py", 2, "GITHUB_TOKEN") in found
    assert ("key.txt", 1, "PRIVATE_KEY_BLOCK") in found
    assert all(FAKE_PAT not in repr(item) for item in found)


def test_synthetic_fixtures_and_evidence_are_excluded(tmp_path):
    for folder in ("tests", "docs/evidence"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "f.py").write_text(FAKE_PAT, encoding="utf-8")
    assert scan(tmp_path, ["tests/f.py", "docs/evidence/f.py"]) == []


def test_bare_headers_and_short_strings_are_not_findings(tmp_path):
    (tmp_path / "doc.md").write_text(
        "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n"
        "ghp_short\n",
        encoding="utf-8",
    )
    assert scan(tmp_path, ["doc.md"]) == []
