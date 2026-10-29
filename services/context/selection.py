"""Pure bounded incident-path selection within an independently registered grant."""

import re

from packages.contracts.repository import RepositoryGrant
from packages.policies.repository import allowed_entry

REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])"
    r"([A-Za-z0-9_./\\-]+\.(?:py|toml|txt|md))"
    r"(?=$|\.(?=\s|$)|[^A-Za-z0-9_./\\-])"
)


def select_paths(
    grant: RepositoryGrant, incident: str, fallback: tuple[str, ...]
) -> tuple[str, ...]:
    """Prefer explicit relative references; never infer roots or execute instructions.

    Missing files remain acquisition errors. No basename guessing or stripping
    absolute prefixes: those could silently select a different source file.
    """
    grant = RepositoryGrant.model_validate(grant.model_dump())
    if (
        len(incident) > 1024
        or not 1 <= len(fallback) <= 16
        or len(set(fallback)) != len(fallback)
        or any(not allowed_entry(grant, p, "100644") for p in fallback)
    ):
        raise ValueError("CONTEXT_SELECTION_INVALID")
    selected: list[str] = []
    for match in REFERENCE.finditer(incident):
        path = match.group(1)
        if allowed_entry(grant, path, "100644") and path not in selected:
            selected.append(path)
            if len(selected) == 16:
                break
    return tuple(selected) or fallback
