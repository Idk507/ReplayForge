"""Journal models store references and safe observations, never workload logs."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.persistence.baseline import BaselineAction
from services.runner.baseline import FailureExpectation
from services.runner.commands import plan_pytest
from tests.unit.test_command_policy import inputs


def test_action_is_immutable_and_closed() -> None:
    run, grant, owner, request = inputs()
    action = BaselineAction(
        execution_id=uuid4(),
        plan=plan_pytest(request, run, grant, owner),
        source_tree_ref="sha256:" + "e" * 64,
        expected=FailureExpectation(
            test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
        ),
    )
    with pytest.raises(ValidationError):
        BaselineAction.model_validate({**action.model_dump(), "raw_log": "secret"})
    with pytest.raises(ValidationError):
        action.source_tree_ref = "sha256:" + "a" * 64
