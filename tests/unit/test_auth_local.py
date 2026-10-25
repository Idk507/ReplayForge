"""No secret discovery/provider calls; explicit separate local roles only."""

import pytest

from services.auth.github import AuthDenied
from services.auth.local import AuthLocalConfig


def values():
    return {
        "REPLAYFORGE_DATABASE_URL": "postgresql://worker:synthetic-database-password@127.0.0.1/demo",
        "REPLAYFORGE_AUTH_DATABASE_URL": "postgresql://auth:synthetic-database-password@127.0.0.1/demo",
        "GITHUB_CLIENT_ID": "Iv1.synthetic",
        "GITHUB_CLIENT_SECRET": "synthetic-client-secret",
        "REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX": "11" * 32,
        "REPLAYFORGE_AUTH_HMAC_KEY_HEX": "22" * 32,
    }


def test_local_auth_explicit_config_no_implicit_live():
    c = AuthLocalConfig.from_env(values())
    assert not c.allow_live and c.settings.development
    assert "synthetic-client-secret" not in repr(c)
    assert "synthetic-database-password" not in repr(c)
    assert c.publication_key_path is None and c.approval_target_branch is None


@pytest.mark.parametrize("fault", ["key", "branch", "live", "flag"])
def test_publication_requires_explicit_complete_config(fault):
    v = values() | {
        "REPLAYFORGE_AUTH_ENABLE_GITHUB": "true",
        "REPLAYFORGE_ENABLE_GITHUB_PUBLICATION": "true",
        "REPLAYFORGE_APPROVAL_TARGET_BRANCH": "main",
        "GITHUB_APP_PRIVATE_KEY_PATH": "C:/explicit/private.pem",
    }
    if fault == "key":
        v.pop("GITHUB_APP_PRIVATE_KEY_PATH")
    if fault == "branch":
        v.pop("REPLAYFORGE_APPROVAL_TARGET_BRANCH")
    if fault == "live":
        v["REPLAYFORGE_AUTH_ENABLE_GITHUB"] = "false"
    if fault == "flag":
        v["REPLAYFORGE_ENABLE_GITHUB_PUBLICATION"] = "anything"
    with pytest.raises(AuthDenied):
        AuthLocalConfig.from_env(v)


@pytest.mark.parametrize(
    "fault",
    ["missing", "same_role", "different_db", "remote", "keys", "live", "options"],
)
def test_local_auth_invalid_config_does_not_fall_back(fault):
    v = values()
    if fault == "missing":
        v.pop("GITHUB_CLIENT_ID")
    if fault == "same_role":
        v["REPLAYFORGE_AUTH_DATABASE_URL"] = v["REPLAYFORGE_DATABASE_URL"]
    if fault == "different_db":
        v["REPLAYFORGE_AUTH_DATABASE_URL"] += "2"
    if fault == "remote":
        v["REPLAYFORGE_AUTH_DATABASE_URL"] = (
            "postgresql://auth:synthetic-database-password@evil.example/demo"
        )
    if fault == "keys":
        v["REPLAYFORGE_AUTH_ENCRYPTION_KEY_HEX"] = v["REPLAYFORGE_AUTH_HMAC_KEY_HEX"]
    if fault == "live":
        v["REPLAYFORGE_AUTH_ENABLE_GITHUB"] = "anything"
    if fault == "options":
        v["REPLAYFORGE_DATABASE_URL"] += "?options=-c%20role%3Dpostgres"
    with pytest.raises(AuthDenied, match="AUTH_LOCAL_CONFIG_INVALID"):
        AuthLocalConfig.from_env(v)


@pytest.mark.parametrize("fault", ["no_live", "key", "flag"])
def test_collection_route_is_explicit_opt_in(fault):
    v = values()
    v["REPLAYFORGE_ENABLE_COLLECTION_BROKER"] = "true"
    v["REPLAYFORGE_AUTH_ENABLE_GITHUB"] = "true"
    v["REPLAYFORGE_COLLECTION_WORKER_KEY"] = "s" * 43
    if fault == "no_live":
        v["REPLAYFORGE_AUTH_ENABLE_GITHUB"] = "false"
    if fault == "key":
        v["REPLAYFORGE_COLLECTION_WORKER_KEY"] = "bad"
    if fault == "flag":
        v["REPLAYFORGE_ENABLE_COLLECTION_BROKER"] = "yes"
    with pytest.raises(AuthDenied):
        AuthLocalConfig.from_env(v)
