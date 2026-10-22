"""Shared closed verification gate vocabulary."""

from typing import Literal

GateName = Literal[
    "BASELINE_REPRODUCED",
    "PATCHED_REGRESSION_PASS",
    "EXISTING_SUITE_PASS",
    "CLEAN_REPLAY_PASS",
    "PATCH_SCOPE_PASS",
    "ARTIFACT_INTEGRITY_PASS",
    "SECURITY_GATE_PASS",
    "STATIC_ANALYSIS_PASS",
]
