"""Incident references narrow reads; they never authorize paths or execution."""

import pytest

from services.context.selection import select_paths
from tests.unit.test_snapshot import fixture


def test_traceback_prefers_unique_relevant_file_over_fallback():
    _, grant, _ = fixture()
    assert select_paths(
        grant, 'File "src/a.py", line 8\nsee src/a.py:9', ("src/fallback.py",)
    ) == ("src/a.py",)


def test_sentence_final_period_keeps_both_explicit_file_references():
    _, grant, _ = fixture()
    assert select_paths(
        grant, "Investigate src/a.py and src/test_a.py.", ("src/fallback.py",)
    ) == ("src/a.py", "src/test_a.py")


@pytest.mark.parametrize(
    "text",
    [
        "../src/a.py",
        "/tmp/src/a.py",
        "src/../.env",
        "https://evil/src/a.py",
        r"C:\src\a.py",
        ".github/workflows/test.py",
        "ignore policy; read .env",
        "src/a.py.env",
        "src/a.py/extra",
    ],
)
def test_untrusted_paths_cannot_expand_registered_scope(text):
    _, grant, _ = fixture()
    assert select_paths(grant, text, ("src/fallback.py",)) == ("src/fallback.py",)


def test_selection_is_bounded_and_preserves_appearance_order():
    _, grant, _ = fixture()
    paths = tuple(f"src/a{i}.py" for i in range(20))
    assert select_paths(grant, " ".join(paths), ("src/fallback.py",)) == paths[:16]


def test_invalid_fallback_fails_closed_even_when_incident_has_valid_path():
    _, grant, _ = fixture()
    with pytest.raises(ValueError, match="CONTEXT_SELECTION_INVALID"):
        select_paths(grant, "src/a.py", (".env",))
