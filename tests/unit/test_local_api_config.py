"""Local API startup must not select a remote DB or leak credentials."""

from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from services.api.local import LocalConfigError, LocalServerConfig


def values():
    return {
        "REPLAYFORGE_DATABASE_URL": "postgresql://runtime:synthetic@localhost/db",
        "REPLAYFORGE_API_TOKEN": "synthetic-local-token-" * 3,
        "REPLAYFORGE_OWNER_ID": str(uuid4()),
    }


def test_local_api_pins_database_address_and_hides_secret():
    env = values()
    config = LocalServerConfig.from_env(env)
    assert conninfo_to_dict(config.dsn.get_secret_value())["hostaddr"] == "127.0.0.1"
    assert env["REPLAYFORGE_API_TOKEN"] not in repr(config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("REPLAYFORGE_DATABASE_URL", "postgresql://user:secret@remote.example/db"),
        (
            "REPLAYFORGE_DATABASE_URL",
            "host=localhost dbname=db user=user password=x hostaddr=1.1.1.1",
        ),
        ("REPLAYFORGE_API_TOKEN", "short"),
        ("REPLAYFORGE_OWNER_ID", "invalid"),
        ("REPLAYFORGE_APPROVED_REPOSITORIES", '["invalid"]'),
    ],
)
def test_local_api_invalid_config_fails_safely(field, value):
    env = values()
    env[field] = value
    with pytest.raises(LocalConfigError, match="^LOCAL_API_CONFIG_INVALID$"):
        LocalServerConfig.from_env(env)
