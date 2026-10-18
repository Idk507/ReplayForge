"""REST process settings cannot inherit credentials or enable networking."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.runner.commands import plan_pytest
from services.runner.rest_plan import build_test_spawn
from tests.unit.test_command_policy import inputs


def test_rest_request_preserves_exact_template_and_denies_network() -> None:
    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    image = uuid4()
    payload = build_test_spawn(plan, image)
    assert payload["networking"] == {"enabled": False}
    assert payload["image"] == str(image)
    assert (payload["command"], *payload["args"]) == plan.argv
    assert payload["env"] == {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    assert payload["shell"] is False
    assert payload["preserve_env"] is False
    assert payload["disposable"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"argv": ("sh", "-c", "arbitrary")},
        {"env": (("NEBIUS_API_KEY", "synthetic"),)},
        {"shell": True},
        {"timeout_seconds": 900},
        {"cwd": "/"},
    ],
)
def test_rest_request_revalidates_forged_plan(change: dict[str, object]) -> None:
    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    with pytest.raises(ValidationError):
        build_test_spawn(plan.model_copy(update=change), uuid4())
