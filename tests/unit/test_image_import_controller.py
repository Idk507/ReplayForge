"""Import policy must reject before any persistent or provider action."""

import httpx
import pytest

from packages.contracts.sandbox import RunnerError
from services.persistence.store import Store
from services.runner.image_import_controller import ImageImportController
from tests.unit.test_one_shot_image_import import config, plan


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [
        {"max_polls": 0},
        {"max_polls": 1.5},
        {"max_polls": True},
        {"timeout_seconds": 0},
        {"poll_interval_seconds": float("nan")},
    ],
)
async def test_invalid_poll_policy_never_touches_database_or_provider(options) -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(500, request=request)

    controller = ImageImportController(
        Store("deliberately-invalid-dsn"),
        config(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RunnerError, match="S_POLICY_DENIED"):
        await controller.run(plan(), allow_live=True, **options)
    assert requests == []
