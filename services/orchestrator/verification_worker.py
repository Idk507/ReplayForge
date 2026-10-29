"""Durable finalization worker for fully retained four-phase replay evidence.

The worker is composed by a trusted worker host. Its signer and verifier are
injected dependencies; this module has no key generation, provider transport,
or environment-driven trust fallback.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.contracts.run import Stage
from services.orchestrator.production_verification import (
    PHASES,
    PhaseTrust,
    ProductionGateProducer,
)
from services.persistence.store import Store, StoreError


class VerificationWorkerError(Exception):
    """Sanitized worker startup or loop failure."""


@dataclass(frozen=True)
class VerificationTrust:
    """Operator-injected verification trust, never loaded from a run."""

    phase_trust: dict[str, PhaseTrust]
    signer: Any
    gate_verifier: Any


class VerificationWorkerConfig(BaseModel):
    """Explicit worker database target and schema expectation."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    dsn: SecretStr
    expected_database: str = Field(pattern=r"^[A-Za-z0-9_-]{1,63}$")
    expected_revision: str = Field(pattern=r"^[0-9]{4}$", default="0036")
    required_role: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,63}$")

    @classmethod
    def from_env(
        cls, values: Mapping[str, str] | None = None
    ) -> VerificationWorkerConfig:
        """Load the database target explicitly; trust remains injected."""
        env = os.environ if values is None else values
        try:
            raw = env.get("REPLAYFORGE_DATABASE_URL", "")
            if not 1 <= len(raw) <= 4096:
                raise ValueError
            info = conninfo_to_dict(raw)
            allowed = {
                "host",
                "port",
                "dbname",
                "user",
                "password",
                "sslmode",
                "connect_timeout",
            }
            if (
                not all(info.get(key) for key in ("host", "dbname", "user", "password"))
                or set(info) - allowed
                or (
                    info["host"] not in {"localhost", "127.0.0.1", "::1"}
                    and info.get("sslmode") != "verify-full"
                )
            ):
                raise ValueError
            info["connect_timeout"] = "5"
            info["options"] = "-c statement_timeout=10000 -c lock_timeout=5000"
            return cls(
                dsn=SecretStr(make_conninfo("", **info)),
                expected_database=env.get("REPLAYFORGE_DATABASE_NAME", ""),
                required_role=env.get("REPLAYFORGE_VERIFICATION_WORKER_ROLE") or None,
                expected_revision=env.get("REPLAYFORGE_SCHEMA_REVISION", "0036"),
            )
        except (ValueError, TypeError, psycopg.Error):
            raise VerificationWorkerError(
                "VERIFICATION_WORKER_CONFIG_INVALID"
            ) from None


class StopSignal(Protocol):
    """threading.Event-compatible cooperative shutdown signal."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class VerificationWorkerTick(BaseModel):
    """Safe worker result; includes no source, report, signature, or secret."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["IDLE", "FINALIZED"]
    run_id: UUID | None = None
    stage: Stage | None = None


class VerificationWorkerLoop(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ticks: int = Field(ge=0)
    finalized: int = Field(ge=0)
    idle: int = Field(ge=0)
    stop_requested: bool


_TEST_ID = re.compile(
    r"(^|[-_.])(test|tests|synthetic|fixture|development)([-_.]|$)", re.I
)


class ProductionVerificationWorker:
    """Finalize only runs whose complete evidence bundle is durably retained.

    Claiming uses the existing fenced job lease and `SKIP LOCKED` semantics.
    The evidence manifest is written only after all replay receipts have been
    authenticated and retained. A crash after that commit is recovered by a
    later claim after lease expiry; this worker never dispatches or polls a
    provider operation.
    """

    def __init__(
        self,
        store: Store,
        trust: VerificationTrust,
        config: VerificationWorkerConfig,
        *,
        test_only: bool = False,
    ) -> None:
        if type(test_only) is not bool:
            raise VerificationWorkerError("VERIFICATION_WORKER_TRUST_INVALID")
        try:
            producer = ProductionGateProducer(
                store,
                trust.phase_trust,
                trust.signer,
                trust.gate_verifier,
            )
        except (StoreError, AttributeError, TypeError):
            raise VerificationWorkerError("VERIFICATION_WORKER_TRUST_INVALID") from None
        if not test_only and self._has_test_identity(producer):
            raise VerificationWorkerError("VERIFICATION_TEST_TRUST_DENIED")
        self.store = store
        self.producer = producer
        self.config = VerificationWorkerConfig.model_validate(config.model_dump())
        self._allow_test_only = test_only
        self._validate_database()

    @classmethod
    def from_env(
        cls,
        trust: VerificationTrust,
        values: Mapping[str, str] | None = None,
        *,
        test_only: bool = False,
    ) -> ProductionVerificationWorker:
        """Build from explicit database settings and injected trust objects."""
        config = VerificationWorkerConfig.from_env(values)
        return cls(
            Store(config.dsn.get_secret_value()),
            trust,
            config,
            test_only=test_only,
        )

    @staticmethod
    def _has_test_identity(producer: ProductionGateProducer) -> bool:
        signer_id = getattr(producer.signer, "key_id", None)
        if signer_id is None:
            signer_id = getattr(getattr(producer.signer, "backend", None), "key_id", "")
        ids = [signer_id, *producer.gate_verifier._keys]
        for phase in PHASES:
            trust = producer.phase_trust[phase]
            ids.extend(trust.receipt_verifier._keys)
            ids.extend(trust.capability_verifier._keys)
        return any(_TEST_ID.search(value) is not None for value in ids)

    def _validate_database(self) -> None:
        """Reject wrong DB/schema and privileged production worker roles."""
        try:
            with self.store._connection() as conn:
                identity = conn.execute(
                    "SELECT current_database() AS database,current_user AS role,"
                    "r.rolsuper,r.rolcreatedb,r.rolcreaterole,"
                    "has_table_privilege(current_user,'verification_results',"
                    "'SELECT') AS can_read_verifications,"
                    "has_table_privilege(current_user,'verification_results',"
                    "'INSERT') AS can_write_verifications,"
                    "(has_column_privilege(current_user,'runs','checkpoint',"
                    "'UPDATE') AND has_column_privilege(current_user,'runs',"
                    "'version','UPDATE')) "
                    "AS can_update_runs,"
                    "(has_column_privilege(current_user,'job_leases','status',"
                    "'UPDATE') AND has_column_privilege(current_user,'job_leases',"
                    "'worker_id','UPDATE') AND has_column_privilege(current_user,"
                    "'job_leases','token','UPDATE') AND has_column_privilege("
                    "current_user,'job_leases','leased_until','UPDATE') AND "
                    "has_column_privilege(current_user,'job_leases','attempt_count',"
                    "'UPDATE')) "
                    "AS can_update_leases,"
                    "has_table_privilege(current_user,'auth_credentials',"
                    "'SELECT') AS can_read_auth_secrets "
                    "FROM pg_roles r WHERE r.rolname=current_user"
                ).fetchone()
                revision = conn.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
        except StoreError as error:
            code = str(error)
            suffix = code.removeprefix("DB_FAILURE_")
            if code.startswith("DB_FAILURE_") and re.fullmatch(r"[A-Z0-9]+", suffix):
                raise VerificationWorkerError(
                    f"VERIFICATION_WORKER_DATABASE_FAILURE_{suffix}"
                ) from None
            raise VerificationWorkerError(
                "VERIFICATION_WORKER_DATABASE_UNAVAILABLE"
            ) from None
        except psycopg.Error as error:
            state = error.sqlstate or "UNAVAILABLE"
            if re.fullmatch(r"[A-Z0-9]+", state):
                raise VerificationWorkerError(
                    f"VERIFICATION_WORKER_DATABASE_FAILURE_{state}"
                ) from None
            raise VerificationWorkerError(
                "VERIFICATION_WORKER_DATABASE_UNAVAILABLE"
            ) from None
        if identity is None or revision is None:
            raise VerificationWorkerError("VERIFICATION_WORKER_SCHEMA_UNAVAILABLE")
        if (
            identity["database"] != self.config.expected_database
            or (
                self.config.required_role is not None
                and identity["role"] != self.config.required_role
            )
            or revision["version_num"] != self.config.expected_revision
        ):
            raise VerificationWorkerError("VERIFICATION_WORKER_DATABASE_BINDING_DENIED")
        if not all(
            identity[name]
            for name in (
                "can_read_verifications",
                "can_write_verifications",
                "can_update_runs",
                "can_update_leases",
            )
        ):
            raise VerificationWorkerError("VERIFICATION_WORKER_DATABASE_ROLE_DENIED")
        if any(identity[name] for name in ("rolsuper", "rolcreatedb", "rolcreaterole")):
            raise VerificationWorkerError("VERIFICATION_WORKER_DATABASE_ROLE_DENIED")
        if identity["can_read_auth_secrets"] and not self._test_only_database():
            raise VerificationWorkerError("VERIFICATION_WORKER_DATABASE_ROLE_DENIED")

    def _test_only_database(self) -> bool:
        """Test harness opt-in; never inferred from environment or DB name."""
        return self._allow_test_only

    def tick(
        self, worker_id: UUID, *, run_id: UUID | None = None
    ) -> VerificationWorkerTick:
        """Claim one eligible retained bundle and finalize it exactly once."""
        if not isinstance(worker_id, UUID):
            raise VerificationWorkerError("VERIFICATION_WORKER_ID_INVALID")
        candidate = self._eligible_run(run_id)
        if candidate is None:
            return VerificationWorkerTick(status="IDLE")
        lease = self.store.claim(worker_id, stages=(Stage.VERIFYING,), run_id=candidate)
        if lease is None:
            return VerificationWorkerTick(status="IDLE")
        owner, run = self.store.claimed_run(lease)
        finalized = self.producer.finalize(lease, owner, expected_version=run.version)
        return VerificationWorkerTick(
            status="FINALIZED", run_id=finalized.run_id, stage=finalized.current_stage
        )

    def _eligible_run(self, run_id: UUID | None) -> UUID | None:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT r.id FROM runs r JOIN job_leases j ON j.run_id=r.id "
                "WHERE r.checkpoint->>'current_stage'='VERIFYING' "
                "AND r.checkpoint->>'evidence_manifest_ref' IS NOT NULL "
                "AND r.checkpoint->>'evidence_manifest_ref' <> 'null' "
                "AND (j.status='queued' OR (j.status='leased' "
                "AND j.leased_until<=clock_timestamp())) "
                "AND (%s::uuid IS NULL OR r.id=%s) "
                "ORDER BY r.id FOR UPDATE OF r SKIP LOCKED LIMIT 1",
                (run_id, run_id),
            ).fetchone()
        return None if row is None else UUID(str(row["id"]))

    def serve(
        self,
        worker_id: UUID,
        stop: StopSignal,
        *,
        max_ticks: int = 100,
        idle_seconds: float = 1,
    ) -> VerificationWorkerLoop:
        """Bounded restart-safe polling with cooperative shutdown."""
        if (
            not isinstance(worker_id, UUID)
            or type(max_ticks) is not int
            or not 1 <= max_ticks <= 1000
            or type(idle_seconds) not in (int, float)
            or not math.isfinite(idle_seconds)
            or not 0 < idle_seconds <= 60
        ):
            raise VerificationWorkerError("VERIFICATION_WORKER_LIMIT_INVALID")
        ticks = finalized = idle = 0
        while ticks < max_ticks and not stop.is_set():
            result = self.tick(worker_id)
            ticks += 1
            if result.status == "FINALIZED":
                finalized += 1
            else:
                idle += 1
                if ticks < max_ticks and stop.wait(idle_seconds):
                    break
        return VerificationWorkerLoop(
            ticks=ticks,
            finalized=finalized,
            idle=idle,
            stop_requested=stop.is_set(),
        )
