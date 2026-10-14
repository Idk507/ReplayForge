"""Offline-first fixed repository read smoke; credentials never enter output."""

import argparse
import os
import re
import subprocess

from pydantic import SecretStr

from services.repository.smoke import GitHubProbe, probe


def main() -> int:
    """CLI credential retrieval is opt-in, in memory, and shell-free."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--use-gh-cli", action="store_true")
    args = parser.parse_args()
    if args.live and (
        args.commit is None or not re.fullmatch(r"[a-f0-9]{40}", args.commit)
    ):
        parser.error("live reads require an exact lowercase 40-character commit SHA")
    token = None
    if args.live and args.use_gh_cli:
        try:
            result = subprocess.run(
                ["gh", "auth", "token", "--hostname", "github.com"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            token = SecretStr(result.stdout.strip())
    elif args.live:
        value = os.environ.get("REPLAYFORGE_GITHUB_READ_TOKEN")
        token = SecretStr(value) if value else None
    report: GitHubProbe = probe(args.commit, token, live=args.live)
    print(report.model_dump_json())
    if args.live and report.status == "NOT_RUN":
        return 2
    return 1 if report.status == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
