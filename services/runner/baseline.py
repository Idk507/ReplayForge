"""Bounded unauthenticated JUnit inspection; never run tests or commit verdicts."""

import hashlib
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts.execution import TestCommandPlan
from packages.contracts.run import ArtifactID


class FailureExpectation(BaseModel):
    """Exact module-level regression function and explicit behavioral marker."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    test_name: str = Field(max_length=128, pattern=r"^test_[A-Za-z0-9_]+$")
    assertion_marker: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Z][A-Z0-9_]+$"
    )


Reason = Literal[
    "ASSERTION_MATCH",
    "PROCESS_FAILURE",
    "INVALID_XML",
    "REPORT_LIMIT",
    "REPORT_SCHEMA",
    "ARTIFACT_MISMATCH",
    "TARGET_MISMATCH",
    "NO_EXPECTED_ASSERTION",
    "PLAN_PHASE",
]


class BaselineInspection(BaseModel):
    """Matching attacker-supplied XML does not authenticate execution evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    status: Literal["MATCHED_ASSERTION", "INVALID_REPRODUCTION", "INCONCLUSIVE"]
    reason: Reason
    evidence_origin: Literal["UNAUTHENTICATED_INPUT"] = "UNAUTHENTICATED_INPUT"
    can_authorize_patching: Literal[False] = False
    junit_ref: ArtifactID | None
    run_id: UUID
    source_commit: str
    reproduction_test_ref: ArtifactID
    observed_at: AwareDatetime
    trace_id: UUID


class ReportInvalid(Exception):
    """Internal structural denial; contains no XML or provider details."""


def _metadata(element: ET.Element) -> None:
    if element.tag == "properties":
        if any(c.tag != "property" or len(c) for c in element):
            raise ReportInvalid
    elif len(element):
        raise ReportInvalid


def _cases(root: ET.Element) -> list[ET.Element]:
    """Accept flat pytest suites, require exact declared/observed outcome counts."""
    stack = [(root, 0)]
    nodes = 0
    while stack:
        element, depth = stack.pop()
        nodes += 1
        if nodes > 2048 or depth > 64:
            raise ReportInvalid
        stack.extend((child, depth + 1) for child in element)
    suites = [root] if root.tag == "testsuite" else list(root)
    if root.tag not in {"testsuite", "testsuites"} or not 1 <= len(suites) <= 16:
        raise ReportInvalid
    cases = []
    identities = set()
    for suite in suites:
        if suite.tag != "testsuite" or any(
            child.tag not in {"testcase", "properties", "system-out", "system-err"}
            for child in suite
        ):
            raise ReportInvalid
        for child in suite:
            if child.tag != "testcase":
                _metadata(child)
        group = [child for child in suite if child.tag == "testcase"]
        counts = {"tests": len(group), "failures": 0, "errors": 0, "skipped": 0}
        for case in group:
            for child in case:
                _metadata(child)
            identity = (case.get("classname"), case.get("name"))
            if not all(identity) or identity in identities:
                raise ReportInvalid
            identities.add(identity)
            outcomes = [c for c in case if c.tag in {"failure", "error", "skipped"}]
            if len(outcomes) > 1 or any(
                c.tag
                not in {
                    "failure",
                    "error",
                    "skipped",
                    "properties",
                    "system-out",
                    "system-err",
                }
                for c in case
            ):
                raise ReportInvalid
            for outcome in outcomes:
                counts[
                    {"failure": "failures", "error": "errors", "skipped": "skipped"}[
                        outcome.tag
                    ]
                ] += 1
        for name, observed in counts.items():
            declared = suite.get(name, "")
            if not re.fullmatch(r"[0-9]{1,4}", declared) or int(declared) != observed:
                raise ReportInvalid
        cases.extend(group)
    if not 1 <= len(cases) <= 256:
        raise ReportInvalid
    if root.tag == "testsuites":
        aggregate = {
            "tests": len(cases),
            "failures": sum(c.find("failure") is not None for c in cases),
            "errors": sum(c.find("error") is not None for c in cases),
            "skipped": sum(c.find("skipped") is not None for c in cases),
        }
        for name, count in aggregate.items():
            aggregate_declared = root.get(name)
            if aggregate_declared is not None and (
                not re.fullmatch(r"[0-9]{1,4}", aggregate_declared)
                or int(aggregate_declared) != count
            ):
                raise ReportInvalid
    return cases


def inspect_baseline(
    plan: TestCommandPlan,
    junit: bytes,
    artifact_ref: str,
    *,
    exit_code: int,
    expected: FailureExpectation,
) -> BaselineInspection:
    """Qualify a report only; caller must authenticate runner and artifact origin.

    XML is data. No raw messages, tracebacks, output, test names or markers are
    returned. Successful qualification cannot set baseline_result_ref/PATCHING.
    """
    plan = TestCommandPlan.model_validate(plan.model_dump())
    expected = FailureExpectation.model_validate(expected.model_dump())
    actual = None

    def result(
        status: Literal["MATCHED_ASSERTION", "INVALID_REPRODUCTION", "INCONCLUSIVE"],
        reason: Reason,
    ) -> BaselineInspection:
        return BaselineInspection(
            status=status,
            reason=reason,
            junit_ref=actual,
            run_id=plan.request.run_id,
            source_commit=plan.request.source_commit,
            reproduction_test_ref=plan.request.reproduction_test_ref,
            observed_at=datetime.now(UTC),
            trace_id=uuid4(),
        )

    if len(junit) > 131072:
        return result("INCONCLUSIVE", "REPORT_LIMIT")
    actual = "sha256:" + hashlib.sha256(junit).hexdigest()
    if artifact_ref != actual:
        return result("INCONCLUSIVE", "ARTIFACT_MISMATCH")
    if plan.request.phase != "BASELINE":
        return result("INVALID_REPRODUCTION", "PLAN_PHASE")
    if type(exit_code) is not int or exit_code != 1:
        return result("INVALID_REPRODUCTION", "PROCESS_FAILURE")
    try:
        text = junit.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper() or "\0" in text:
            return result("INCONCLUSIVE", "INVALID_XML")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError, ValueError):
        return result("INCONCLUSIVE", "INVALID_XML")
    try:
        cases = _cases(root)
    except ReportInvalid:
        return result("INVALID_REPRODUCTION", "REPORT_SCHEMA")
    target_class = plan.request.reproduction_path[:-3].replace("/", ".")
    target = [
        c
        for c in cases
        if (c.get("classname"), c.get("name")) == (target_class, expected.test_name)
    ]
    if len(target) != 1:
        return result("INVALID_REPRODUCTION", "TARGET_MISMATCH")
    if any(c.find("error") is not None or c.find("skipped") is not None for c in cases):
        return result("INVALID_REPRODUCTION", "REPORT_SCHEMA")
    failures = [c.find("failure") for c in cases if c.find("failure") is not None]
    if len(failures) != 1 or target[0].find("failure") is None:
        return result("INVALID_REPRODUCTION", "NO_EXPECTED_ASSERTION")
    failure = failures[0]
    if failure is None:
        return result("INVALID_REPRODUCTION", "NO_EXPECTED_ASSERTION")
    detail = failure.get("message", "") + "\n" + "".join(failure.itertext())
    if (
        "AssertionError" not in detail
        or any(
            word in detail
            for word in (
                "SyntaxError",
                "ImportError",
                "ModuleNotFoundError",
                "ERROR collecting",
            )
        )
        or re.search(
            r"(?<![A-Z0-9_])" + re.escape(expected.assertion_marker) + r"(?![A-Z0-9_])",
            detail,
        )
        is None
    ):
        return result("INVALID_REPRODUCTION", "NO_EXPECTED_ASSERTION")
    return result("MATCHED_ASSERTION", "ASSERTION_MATCH")
