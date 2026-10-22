"""REST serialization of validated commands; planning never grants dispatch."""

from uuid import UUID

from packages.contracts.execution import TestCommandPlan


def build_test_spawn(plan: TestCommandPlan, image_uuid: UUID) -> dict[str, object]:
    """Build a network-disabled spawn with no model-controlled process settings.

    Source: Token Factory Sandboxes security-and-data-handling requires network
    denial on every relevant spawn. This uses the same documented fields as the
    existing REST smoke client, with the application-owned pytest template.
    Image UUID remains provider identity, not proof of the plan's OCI digest.
    An independent binding/admission check is mandatory before submission.
    """
    plan = TestCommandPlan.model_validate(plan.model_dump())
    if not isinstance(image_uuid, UUID):
        raise ValueError("REST_IMAGE_IDENTITY_DENIED")
    return {
        "image": str(image_uuid),
        "command": plan.argv[0],
        "args": list(plan.argv[1:]),
        "shell": False,
        "env": dict(plan.env),
        "preserve_env": False,
        "networking": {"enabled": False},
        "disposable": plan.command_id
        not in {"PYTEST_FILES_SAVED_JUNIT_V1", "PYTEST_FILES_MEASURED_V1"},
        "uid": 1000,
        "gid": 1000,
        "cwd": plan.cwd,
        "timeout": plan.timeout_seconds,
        "truncate_output_at": 65536,
        "resources_limits": {"max_layer_bytes": 64 * 1024 * 1024},
    }
