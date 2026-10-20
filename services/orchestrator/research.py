"""On-demand read-only research broker: durable budgets, sticky intent, citations."""

import hashlib
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts.repository import RepositoryGrant
from packages.contracts.research import DocumentationQuery, ResearchReport
from packages.contracts.run import RunState, Stage
from packages.policies.repository import admit
from packages.providers.tavily import TavilyResearchClient
from services.persistence.model_budget import debit_research, reservation_transaction
from services.persistence.store import Lease, Store, StoreError
from services.runner.receipts import canonical


class ResearchDispatch(BaseModel):
    """Closed public query and operator estimate, never raw incident/code text."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    query: DocumentationQuery
    estimate_microusd: int = Field(strict=True, gt=0, le=2000000)


class RetainedResearch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    run_id: UUID
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    query: DocumentationQuery
    report: ResearchReport


class ResearchBroker:
    """No autonomous tools, execution, inference, or retries of uncertain searches.

    The caller is trusted orchestration code. Returned documentation remains
    untrusted evidence; neither snippets nor links can request another tool.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def _locked(
        conn: psycopg.Connection[dict[str, object]],
        lease: Lease,
        owner: UUID,
        version: int,
    ) -> RunState:
        row = conn.execute(
            "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE",
            (lease.run_id, owner),
        ).fetchone()
        job = conn.execute(
            "SELECT run_id FROM job_leases WHERE run_id=%s AND worker_id=%s "
            "AND token=%s AND status='leased' "
            "AND leased_until>clock_timestamp() FOR UPDATE",
            (lease.run_id, lease.worker_id, lease.token),
        ).fetchone()
        if row is None or job is None:
            raise StoreError("LEASE_LOST")
        run = RunState.model_validate(row["checkpoint"])
        if type(version) is not int or version != run.version:
            raise StoreError("STALE_VERSION")
        if (
            run.current_stage not in {Stage.TRIAGING, Stage.REPRODUCING, Stage.PATCHING}
            or run.cancellation_requested
        ):
            raise StoreError("RESEARCH_STAGE_DENIED")
        consent = conn.execute(
            "SELECT grant_json FROM repository_grants WHERE id=%s AND owner_id=%s",
            (run.repository_id, owner),
        ).fetchone()
        if (
            consent is None
            or not admit(
                RepositoryGrant.model_validate(consent["grant_json"]),
                owner,
                run.repository_id,
                run.source_commit,
            ).allowed
        ):
            raise StoreError("RESEARCH_SCOPE_DENIED")
        return run

    def investigate(
        self,
        lease: Lease,
        owner: UUID,
        dispatch: ResearchDispatch,
        client: TavilyResearchClient,
        *,
        expected_version: int,
        allow_live: bool = False,
    ) -> RunState:
        """One explicitly authorized curated lookup on a pinned investigation.

        Reserve before submission; uncertain outcomes never automatically retry.
        Estimated USD is an operator declaration, not verified provider pricing.
        """
        if allow_live is not True:
            raise StoreError("RESEARCH_LIVE_NOT_AUTHORIZED")
        dispatch = ResearchDispatch.model_validate(dispatch.model_dump())
        lease = Lease.model_validate(lease.model_dump())
        query_digest = hashlib.sha256(canonical(dispatch.query)).hexdigest()
        with reservation_transaction(self._store) as conn:
            run = self._locked(conn, lease, owner, expected_version)
            if (
                conn.execute(
                    "SELECT 1 FROM research_intents "
                    "WHERE run_id=%s AND query_digest=%s",
                    (run.run_id, query_digest),
                ).fetchone()
                is not None
            ):
                raise StoreError("RESEARCH_SUBMISSION_UNCERTAIN")
            budget = debit_research(self._store, conn, run, dispatch.estimate_microusd)
            reserved = RunState.model_validate(
                {
                    **run.model_dump(),
                    "version": run.version + 1,
                    "remaining_budget": budget.model_dump(),
                }
            )
            conn.execute(
                "INSERT INTO research_intents(run_id,query_digest,reserved_version) "
                "VALUES (%s,%s,%s)",
                (run.run_id, query_digest, reserved.version),
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(reserved.model_dump(mode="json")), reserved.version, run.run_id),
            )
            self._store._event(conn, reserved, "BUDGET_RESERVED")
        # No untrusted response is interpreted as a tool or executable command.
        report = client.search_docs(dispatch.query, allow_live=True)
        record = RetainedResearch(
            run_id=run.run_id,
            source_commit=run.source_commit,
            query=dispatch.query,
            report=report,
        )
        body = canonical(record)
        if len(body) > 65536:
            raise StoreError("RESEARCH_LIMIT")
        digest = hashlib.sha256(body).hexdigest()
        with self._store._connection() as conn:
            current = self._locked(conn, lease, owner, reserved.version)
            if len(current.research_refs) >= 2:
                raise StoreError("RESEARCH_LIMIT")
            updated = RunState.model_validate(
                {
                    **current.model_dump(),
                    "version": current.version + 1,
                    "research_refs": (*current.research_refs, "sha256:" + digest),
                }
            )
            conn.execute(
                "INSERT INTO research_reports(run_id,query_digest,digest,payload) "
                "VALUES (%s,%s,%s,%s)",
                (run.run_id, query_digest, digest, body),
            )
            conn.execute(
                "UPDATE runs SET checkpoint=%s,version=%s WHERE id=%s",
                (Jsonb(updated.model_dump(mode="json")), updated.version, run.run_id),
            )
            self._store._event(conn, updated, "RESEARCH_RETAINED")
            return updated

    def reports(self, run_id: UUID, owner: UUID) -> tuple[RetainedResearch, ...]:
        """Private, expiring, canonical/hash checked advisory citation records."""
        with self._store._connection() as conn:
            run = conn.execute(
                "SELECT checkpoint FROM runs WHERE id=%s AND owner_id=%s",
                (run_id, owner),
            ).fetchone()
            if run is None:
                raise StoreError("NOT_FOUND")
            state = RunState.model_validate(run["checkpoint"])
            rows = conn.execute(
                "SELECT digest,payload FROM research_reports WHERE run_id=%s "
                "AND expires_at>clock_timestamp() ORDER BY query_digest LIMIT 2",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            if not isinstance(row["payload"], (bytes, bytearray, memoryview)):
                raise StoreError("RESEARCH_INTEGRITY")
            body = bytes(row["payload"])
            try:
                record = RetainedResearch.model_validate_json(body)
            except ValidationError:
                raise StoreError("RESEARCH_INTEGRITY") from None
            if (
                not 1 <= len(body) <= 65536
                or hashlib.sha256(body).hexdigest() != row["digest"]
                or canonical(record) != body
                or record.run_id != run_id
                or record.source_commit != state.source_commit
                or "sha256:" + str(row["digest"]) not in state.research_refs
            ):
                raise StoreError("RESEARCH_INTEGRITY")
            result.append(record)
        return tuple(result)
