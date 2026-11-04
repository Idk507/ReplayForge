"""Fetch the pinned pytest wheelhouse listed in the retained manifest.

Offline unless ``--run`` is given. Downloads only the exact HTTPS URLs recorded in
``docs/evidence/nebius-pytest-wheelhouse.json`` from files.pythonhosted.org, bounds
every response to the declared size, and keeps a file only if its SHA256 matches the
manifest. Existing verified files are left untouched. Used by CI and fresh checkouts.
"""

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

MANIFEST = Path("docs/evidence/nebius-pytest-wheelhouse.json")
DESTINATION = Path("artifacts/nebius-pytest-wheelhouse")
ALLOWED_HOST = "files.pythonhosted.org"

Opener = Callable[[str], bytes]


def _download(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        data: bytes = response.read(limit + 1)
    return data


def fetch(
    manifest: Path = MANIFEST,
    destination: Path = DESTINATION,
    opener: Opener | None = None,
) -> list[str]:
    """Return the verified filenames; raise ValueError on any mismatch."""
    entries = json.loads(manifest.read_text(encoding="utf-8"))["packages"]
    destination.mkdir(parents=True, exist_ok=True)
    verified: list[str] = []
    for entry in entries:
        name, url = entry["filename"], entry["url"]
        digest = entry["sha256"].removeprefix("sha256:")
        size = int(entry["size_bytes"])
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != ALLOWED_HOST
            or "/" in name
            or not name.endswith(".whl")
            or len(digest) != 64
        ):
            raise ValueError(f"MANIFEST_ENTRY_DENIED {name}")
        target = destination / name
        if (
            target.is_file()
            and hashlib.sha256(target.read_bytes()).hexdigest() == digest
        ):
            verified.append(name)
            continue
        data = opener(url) if opener is not None else _download(url, size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"WHEEL_IDENTITY_MISMATCH {name}")
        partial = target.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(target)
        verified.append(name)
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    if not parser.parse_args(argv).run:
        print(json.dumps({"status": "NOT_RUN", "reason": "Explicit --run required"}))
        return 2
    try:
        names = fetch()
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "STOPPED", "reason": type(error).__name__}))
        return 1
    print(json.dumps({"status": "VERIFIED", "files": names}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
