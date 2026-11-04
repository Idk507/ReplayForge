"""Scan tracked source for credential-shaped strings; prints locations, never values.

Offline and read-only. Test fixtures and retained evidence (synthetic by contract) are
excluded by path. This complements, and does not replace, a full entropy scanner.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "PRIVATE_KEY_BLOCK": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{80,}"
    ),
    "GITHUB_TOKEN": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),
    "GITHUB_PAT": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    "AWS_ACCESS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "NVIDIA_KEY": re.compile(r"\bnvapi-[A-Za-z0-9_-]{30,}"),
    "TAVILY_KEY": re.compile(r"\btvly-[A-Za-z0-9_-]{20,}"),
    "OPENAI_STYLE_KEY": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "AZURE_STORAGE_KEY": re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}"),
}
EXCLUDED = ("tests/", "docs/evidence/", "apps/web/tests/", "uv.lock")
MAX_BYTES = 1_000_000


def scan(root: Path, files: list[str]) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for name in files:
        if name.startswith(EXCLUDED):
            continue
        path = root / name
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append((name, text.count("\n", 0, match.start()) + 1, label))
    return findings


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    findings = scan(Path("."), files)
    print(
        json.dumps(
            {
                "status": "FOUND" if findings else "CLEAN",
                "scanned": len(files),
                "findings": [
                    {"file": f, "line": line, "pattern": p} for f, line, p in findings
                ],
            }
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
