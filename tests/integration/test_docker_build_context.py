"""Check Docker ignore rules against synthetic sensitive-path sentinels."""

import os
import subprocess
from pathlib import Path

import pytest


def test_build_context_excludes_private_inputs(tmp_path):
    if os.environ.get("REPLAYFORGE_TEST_DOCKER_CONTEXT") != "true":
        pytest.skip("requires explicitly enabled local Docker build")
    (tmp_path / ".dockerignore").write_bytes(Path(".dockerignore").read_bytes())
    permitted = [
        "apps/web/public/example.js",
        "services/example.py",
        "deploy/example",
        "migrations/example.sql",
    ]
    excluded = [
        ".env",
        "apps/web/.env.local",
        "apps/web/private.pem",
        "apps/web/private.key",
        "apps/web/private.pfx",
        "apps/web/private.p12",
        "apps/web/credentials.json",
        "apps/web/node_modules/hidden.js",
        "apps/web/.next/hidden.js",
        "docs/evidence/private.txt",
        "services/local.sqlite3",
        "services/local.db",
        "services/database-dump.sql",
        "services/database.dump",
        "services/database.backup",
        "services/pgdata/PG_VERSION",
    ]
    for filename in permitted + excluded:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic sentinel", encoding="utf-8")
    checks = [f"RUN test -f /context/{name}" for name in permitted]
    checks += [f"RUN test ! -e /context/{name}" for name in excluded]
    dockerfile = (
        "FROM nginx:1.28.3@sha256:"
        "146adea4768b83c607d0bdfa4188464e3da6e0a3ad4475db1d1d8f64f27c29cc\n"
        "COPY . /context\n" + "\n".join(checks) + "\n"
    )
    result = subprocess.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "--output=type=cacheonly",
            "-f",
            "-",
            str(tmp_path),
        ],
        input=dockerfile,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
