from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from deploy.postgres_recovery import (
    REQUIRED_TABLES_AT_0031,
    DatabaseState,
    RecoveryRefused,
    _migration_chain,
    assess_database_state,
    assess_revision_objects,
)


def _state(
    revision: str | None,
    *,
    tables: frozenset[str] = frozenset(),
    relations: frozenset[str] | None = None,
    owner_mismatches: tuple[str, ...] = (),
    active_sessions: int = 0,
) -> DatabaseState:
    return DatabaseState(
        revision=revision,
        tables=tables,
        relations=relations if relations is not None else tables,
        relation_owner_mismatches=owner_mismatches,
        user_schemas=("public",),
        active_other_sessions=active_sessions,
        migration_create=False,
        migration_usage=True,
    )


def _base_constraints() -> dict[str, str]:
    return {
        "baseline_action_events_kind_check": "CHECK (kind IN ('PLANNED'))",
        "image_import_events_kind_check": "CHECK (kind IN ('INTENT'))",
        "replay_submissions_phase_check": "CHECK (phase IN ('CANDIDATE'))",
        "patched_materialization_intents_phase_check": "CHECK (phase IN ('CANDIDATE'))",
    }


def test_pristine_database_is_eligible_for_forward_recovery() -> None:
    assess_database_state(_state(None))


def test_actual_migration_chain_is_linear_and_ends_at_0037() -> None:
    config = Path(__file__).resolve().parents[1] / "alembic.ini"
    scripts = ScriptDirectory.from_config(Config(str(config)))
    assert scripts.get_heads() == ["0037"]
    chain = [r.revision for r in reversed(list(scripts.walk_revisions()))]
    assert chain[-7:] == [
        "0031",
        "0032",
        "0033",
        "0034",
        "0035",
        "0036",
        "0037",
    ]


def test_deployment_recovery_tool_refuses_unreviewed_0037_head() -> None:
    """Deployment recovery is intentionally still pinned to 0036 and fails closed.

    Advancing it needs a reviewed assessment of the 0037 collection journal
    tables and grants; local development never relies on that tool.
    """
    config = Path(__file__).resolve().parents[1] / "alembic.ini"
    with pytest.raises(RecoveryRefused, match="unexpected_alembic_heads"):
        _migration_chain(str(config))


def test_initial_bootstrap_grants_migration_schema_before_alembic() -> None:
    bootstrap = Path(__file__).resolve().parents[1] / "deploy" / "postgres-bootstrap.py"
    source = bootstrap.read_text(encoding="utf-8")
    grant = source.index("GRANT USAGE, CREATE ON SCHEMA public TO")
    migration = source.index("subprocess.run(")
    assert grant < migration


def test_unversioned_database_with_any_public_relation_is_refused() -> None:
    with pytest.raises(RecoveryRefused, match="unversioned_nonempty_schema"):
        assess_database_state(_state(None, relations=frozenset({"unexpected_seq"})))


def test_known_0031_database_requires_all_expected_tables() -> None:
    assess_database_state(_state("0031", tables=REQUIRED_TABLES_AT_0031))
    with pytest.raises(RecoveryRefused, match="incomplete_versioned_schema"):
        assess_database_state(_state("0031", tables=frozenset({"alembic_version"})))


def test_unknown_revision_and_active_session_fail_closed() -> None:
    with pytest.raises(RecoveryRefused, match="revision_outside_recovery_window"):
        assess_database_state(_state("0029", tables=REQUIRED_TABLES_AT_0031))
    with pytest.raises(RecoveryRefused, match="active_database_sessions"):
        assess_database_state(
            _state("0031", tables=REQUIRED_TABLES_AT_0031, active_sessions=1)
        )


def test_non_migrator_owned_relation_fails_closed() -> None:
    with pytest.raises(RecoveryRefused, match="unexpected_relation_owners"):
        assess_database_state(
            _state(
                "0031",
                tables=REQUIRED_TABLES_AT_0031,
                owner_mismatches=("verification_results",),
            )
        )


def test_partial_0032_schema_is_refused_even_if_revision_is_0031() -> None:
    with pytest.raises(RecoveryRefused, match="partial_baseline_producer_schema"):
        assess_revision_objects(
            "0031",
            tables=REQUIRED_TABLES_AT_0031 | {"baseline_preparations"},
            columns=frozenset(),
            constraints=_base_constraints(),
        )


def test_0031_schema_boundary_accepts_absence_of_later_revision_objects() -> None:
    assess_revision_objects(
        "0031",
        tables=REQUIRED_TABLES_AT_0031,
        columns=frozenset(),
        constraints=_base_constraints(),
    )


def test_0036_requires_complete_envelope_columns_and_constraints() -> None:
    with pytest.raises(RecoveryRefused, match="envelope_schema_mismatch"):
        assess_revision_objects(
            "0036",
            tables=REQUIRED_TABLES_AT_0031
            | {"baseline_preparations", "baseline_produced_receipts"},
            columns=frozenset(
                {
                    ("baseline_submissions", "capability_payload"),
                    ("replay_submissions", "capability_payload"),
                    ("verification_results", "envelope_bytes"),
                }
            ),
            constraints={
                **_base_constraints(),
                "baseline_action_events_kind_check": "PRODUCED_RECEIPT_RETAINED",
                "image_import_events_kind_check": "UPLOAD_INTENT",
                "replay_submissions_phase_check": "EXISTING_SUITE",
                "patched_materialization_intents_phase_check": "EXISTING_SUITE",
                "baseline_capability_payload_size": "CHECK",
                "replay_capability_payload_size": "CHECK",
                "verification_results_envelope_version_check": "CHECK",
                "verification_results_envelope_bytes_check": "CHECK",
                "verification_results_envelope_pair_check": "CHECK",
            },
        )


def test_0036_complete_schema_boundary_is_accepted() -> None:
    assess_revision_objects(
        "0036",
        tables=REQUIRED_TABLES_AT_0031
        | {"baseline_preparations", "baseline_produced_receipts"},
        columns=frozenset(
            {
                ("baseline_submissions", "capability_payload"),
                ("replay_submissions", "capability_payload"),
                ("verification_results", "envelope_version"),
                ("verification_results", "envelope_bytes"),
            }
        ),
        constraints={
            "baseline_action_events_kind_check": "PRODUCED_RECEIPT_RETAINED",
            "image_import_events_kind_check": "UPLOAD_INTENT",
            "replay_submissions_phase_check": "EXISTING_SUITE",
            "patched_materialization_intents_phase_check": "EXISTING_SUITE",
            "baseline_capability_payload_size": "CHECK",
            "replay_capability_payload_size": "CHECK",
            "verification_results_envelope_version_check": "CHECK",
            "verification_results_envelope_bytes_check": "CHECK",
            "verification_results_envelope_pair_check": "CHECK",
        },
    )
