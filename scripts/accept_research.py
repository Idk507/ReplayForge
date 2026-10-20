"""One optional paid public-doc lookup in a disposable tiny investigation fixture."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from packages.contracts.research import DocumentationQuery
from packages.providers.tavily import TavilyConfig, TavilyResearchClient
from services.orchestrator.research import ResearchBroker, ResearchDispatch
from services.runner.receipts import canonical
from tests.integration.test_postgres import prepare_store
from tests.integration.test_triage_worker import handoff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live or not os.environ.get("REPLAYFORGE_TEST_DATABASE_URL"):
        print("RESEARCH_ACCEPTANCE_NOT_AUTHORIZED_OR_NO_DISPOSABLE_DB")
        return 2
    destination = Path("docs/evidence/m3-live-research.json")
    if destination.exists():
        print("RESEARCH_ACCEPTANCE_ALREADY_RECORDED_NO_RESUBMISSION")
        return 2
    store = prepare_store()
    owner, state, run = handoff(store)
    lease = store.claim(uuid4(), triage_only=True)
    if lease is None or lease.run_id != state.run_id:
        print("RESEARCH_ACCEPTANCE_LEASE_UNAVAILABLE")
        return 1
    config = TavilyConfig.from_env()
    dispatch = ResearchDispatch(
        query=DocumentationQuery(
            package="python", version="3.12", topic="transactions", max_results=2
        ),
        estimate_microusd=50000,
    )
    with TavilyResearchClient(config) as client:
        ResearchBroker(store).investigate(
            lease,
            owner,
            dispatch,
            client,
            expected_version=run.version,
            allow_live=True,
        )
    record = ResearchBroker(store).reports(run.run_id, owner)[0]
    body = canonical(record)
    destination.write_bytes(body)
    print(
        json.dumps(
            {
                "status": record.report.status,
                "evidence_origin": record.report.evidence_origin,
                "artifact_ref": "sha256:" + hashlib.sha256(body).hexdigest(),
                "sources": len(record.report.sources),
                "usage_credits": record.report.usage_credits,
                "limitations": [
                    "Tiny local fixture; no model or sandbox execution.",
                    "USD reservation is declared, not verified billing.",
                ],
            }
        )
    )
    return (
        0
        if record.report.status == "SUCCESS"
        and record.report.evidence_origin == "REMOTE_SERVICE"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
