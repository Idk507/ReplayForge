"""Disposable DB harness child: real loopback API + browser, no cloud/execution."""

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from packages.contracts.repository import RepositoryGrant
from services.persistence.store import Store
from tests.integration.test_postgres import prepare_store


def main() -> int:
    """Use only the explicitly provisioned disposable PostgreSQL test database."""
    if not os.environ.get("REPLAYFORGE_TEST_DATABASE_URL"):
        print("WEB_TEST_REQUIRES_DISPOSABLE_DB")
        return 2
    prepare_store()
    owner, repository = uuid4(), uuid4()
    grant = RepositoryGrant(
        repository_id=repository,
        owner_id=owner,
        remote_url="https://github.com/example/tiny-fixture",
        commits=("a" * 40,),
        approved_paths=("src/", "tests/"),
        consent=True,
    )
    Store(os.environ["REPLAYFORGE_MIGRATION_DATABASE_URL"]).register_repository(grant)
    env = {
        **os.environ,
        "REPLAYFORGE_DATABASE_URL": os.environ["REPLAYFORGE_TEST_DATABASE_URL"],
        "REPLAYFORGE_API_TOKEN": secrets.token_urlsafe(48),
        "REPLAYFORGE_OWNER_ID": str(owner),
        "REPLAYFORGE_APPROVED_REPOSITORIES": json.dumps([str(repository)]),
        "REPLAYFORGE_WEB_REPOSITORY_ID": str(repository),
        "REPLAYFORGE_API_URL": "http://127.0.0.1:8109",
    }
    # stdout/stderr are intentionally suppressed: no DB or token values in logs.
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.api.local:create_from_env",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8109",
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            for _ in range(50):
                try:
                    if (
                        client.get("http://127.0.0.1:8109/health/live").status_code
                        == 200
                    ):
                        break
                except httpx.TransportError:
                    pass  # Bounded startup readiness, never retries provider actions.
                time.sleep(0.2)
            else:
                print("WEB_API_STARTUP_FAILED")
                return 1
        npm = shutil.which("npm")
        if npm is None:
            print("WEB_NPM_MISSING")
            return 2
        return subprocess.run(
            [npm, "test"], cwd="apps/web", env=env, timeout=180, check=False
        ).returncode
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
