"""Synthetic JUnit only; no test or repository code is executed."""

import hashlib

import pytest

from services.runner.baseline import FailureExpectation, inspect_baseline
from services.runner.commands import plan_pytest
from tests.unit.test_command_policy import inputs

XML = (
    b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
    b'<testcase classname="tests.test_regression" name="test_duplicate">'
    b'<failure message="AssertionError: RF_DUPLICATE_TRANSACTION">'
    b"AssertionError: RF_DUPLICATE_TRANSACTION</failure>"
    b"</testcase></testsuite></testsuites>"
)


def inspect(body=XML, *, exit_code=1, artifact=None):
    run, grant, owner, request = inputs()
    plan = plan_pytest(request, run, grant, owner)
    return inspect_baseline(
        plan,
        body,
        artifact or "sha256:" + hashlib.sha256(body).hexdigest(),
        exit_code=exit_code,
        expected=FailureExpectation(
            test_name="test_duplicate", assertion_marker="RF_DUPLICATE_TRANSACTION"
        ),
    )


def test_exact_failure_is_only_an_unauthenticated_match() -> None:
    result = inspect()
    assert result.status == "MATCHED_ASSERTION"
    assert result.can_authorize_patching is False
    assert result.evidence_origin == "UNAUTHENTICATED_INPUT"
    assert "AssertionError" not in result.model_dump_json()


@pytest.mark.parametrize(
    "case",
    [
        "exit",
        "name",
        "marker",
        "setup",
        "skipped",
        "counts",
        "duplicate",
        "passing",
        "hash",
    ],
)
def test_invalid_reproduction_does_not_match(case) -> None:
    body, code, artifact = XML, 1, None
    if case == "exit":
        code = 2
    elif case == "name":
        body = body.replace(b"test_duplicate", b"test_other")
    elif case == "marker":
        body = body.replace(b"RF_DUPLICATE_TRANSACTION", b"OTHER_ASSERTION")
    elif case == "setup":
        body = body.replace(b"failure", b"error")
    elif case == "skipped":
        body = body.replace(b"<failure ", b"<skipped ").replace(
            b"</failure>", b"</skipped>"
        )
    elif case == "counts":
        body = body.replace(b'tests="1"', b'tests="2"')
    elif case == "duplicate":
        start, end = (
            body.index(b"<testcase"),
            body.index(b"</testcase>") + len(b"</testcase>"),
        )
        body = body[:end] + body[start:end] + body[end:]
    elif case == "passing":
        body = (
            body[: body.index(b"<failure")]
            + body[body.index(b"</failure>") + len(b"</failure>") :]
        )
    else:
        artifact = "sha256:" + "0" * 64
    assert (
        inspect(body, exit_code=code, artifact=artifact).status != "MATCHED_ASSERTION"
    )


@pytest.mark.parametrize(
    "case", ["doctype", "entity", "utf16", "malformed", "size", "depth"]
)
def test_xml_abuse_is_rejected_without_content_in_result(case) -> None:
    bodies = {
        "doctype": b'<!DOCTYPE testsuites [<!ENTITY x "secret">]><testsuites/>',
        "entity": b'<!ENTITY x SYSTEM "file:///private"><testsuites/>',
        "utf16": "<testsuites/>".encode("utf-16"),
        "malformed": b"<testsuites>",
        "size": b"x" * 131073,
        "depth": b"<testsuites>" + b"<x>" * 65 + b"</x>" * 65 + b"</testsuites>",
    }
    result = inspect(bodies[case])
    assert result.status != "MATCHED_ASSERTION"
    assert "secret" not in result.model_dump_json()


def test_additional_passing_case_and_flat_suite_supported() -> None:
    body = XML.replace(b'tests="1"', b'tests="2"').replace(
        b"</testsuite>",
        b'<testcase classname="tests.other" name="test_ok"/></testsuite>',
    )
    assert inspect(body).status == "MATCHED_ASSERTION"
    assert (
        inspect(XML.removeprefix(b"<testsuites>").removesuffix(b"</testsuites>")).status
        == "MATCHED_ASSERTION"
    )


@pytest.mark.parametrize(
    "case",
    [
        "root_counts",
        "nested_failure",
        "nested_metadata",
        "setup_trace",
        "other_failure",
    ],
)
def test_ambiguous_structure_or_additional_failure_rejected(case) -> None:
    if case == "root_counts":
        body = XML.replace(b"<testsuites>", b'<testsuites tests="9">')
    elif case == "nested_failure":
        body = XML.replace(b"</failure>", b"<error>secret</error></failure>")
    elif case == "nested_metadata":
        body = XML.replace(
            b"</testsuite>",
            b'<properties><testcase name="hidden"/></properties></testsuite>',
        )
    elif case == "setup_trace":
        body = XML.replace(b"</failure>", b"ModuleNotFoundError: secret</failure>")
    else:
        body = XML.replace(
            b'tests="1" failures="1"', b'tests="2" failures="2"'
        ).replace(
            b"</testsuite>",
            b'<testcase classname="tests.other" name="test_other">'
            b"<failure>secret</failure></testcase></testsuite>",
        )
    assert inspect(body).status != "MATCHED_ASSERTION"
