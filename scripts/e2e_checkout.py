"""Opt-in fixed checkout benchmark; at most two model calls and three VM runs."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from packages.contracts.repair import TriageProposal
from packages.contracts.repository import RepositoryGrant
from packages.contracts.run import RunState, Stage
from packages.providers.nebius import NebiusConfig, ProbeError
from packages.providers.patch import request_patch
from packages.providers.triage import request_triage
from services.runner.checkout_benchmark import fixture_source, run_checkout
from services.runner.remote import SandboxConfig


def main() -> int:
    """Never execute model code locally, retry a cloud call or overwrite evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--resume-baseline", action="store_true")
    args = parser.parse_args()
    if not args.live or args.env_file is None:
        print(json.dumps({"status": "NOT_RUN", "reason": "LIVE_OPT_IN_REQUIRED"}))
        return 2
    output = Path("docs/evidence/m2-checkout-e2e")
    if args.resume_baseline:
        prior = json.loads((output / "report.json").read_text())
        with (output / "report-before-resume.json").open(
            "x", encoding="utf-8"
        ) as saved:
            json.dump(prior, saved, indent=2)
    else:
        output.mkdir(exist_ok=False)
    values: dict[str, str] = {}
    allowed = {
        "NEBIUS_API_KEY",
        "NEBIUS_PROJECT_ID",
        "NEBIUS_AI_PROJECT",
        "NEBIUS_MODEL_PRIMARY",
        "NEBIUS_BASE_URL",
    }
    for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        name, value = line.strip().removeprefix("export ").split("=", 1)
        if name.strip() in allowed:
            values[name.strip()] = value.strip().strip("\"'")
    values["NEBIUS_PROJECT_ID"] = values.get("NEBIUS_PROJECT_ID") or values.get(
        "NEBIUS_AI_PROJECT", ""
    )
    # Full Python image includes git; immutable provider image, not an OCI digest.
    values["SANDBOX_IMAGE_UUID"] = "db458f4b-a505-310b-8565-b7bb7919a528"
    report: dict[str, object] = {
        "status": "IN_PROGRESS",
        "observed_at": datetime.now(UTC).isoformat(),
        "trace_id": str(uuid4()),
        "scope": "FIXED_SYNTHETIC_CHECKOUT_ONLY",
        "limitations": [
            "Not general repository admission or a VERIFIED verdict.",
            "Provider UUID is not verified OCI SHA256.",
            "Provider retention and hard resource controls remain unverified.",
            "Standalone acceptance path, not durable worker graph integration.",
        ],
    }

    def save() -> None:
        (output / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )

    save()
    try:
        model = NebiusConfig.from_env(values)
        sandbox = SandboxConfig.from_env(values)
        source = fixture_source()
        if args.resume_baseline:
            proposal = TriageProposal.model_validate_json(
                (output / "triage.json").read_bytes()
            )
            baseline = prior["baseline"]
            report["baseline"] = baseline
            if (
                baseline["test_sha256"] != proposal.reproduction.test_source_sha256()
                or baseline["image_uuid"] != str(sandbox.image_uuid)
                or not baseline["compute_terminal_confirmed"]
            ):
                raise ValueError("RESUME_BINDING_DENIED")
        else:
            proposal = request_triage(
                model,
                {
                    "source_app_checkout_py": source,
                    "incident": (
                        "CheckoutService.complete called twice with the same ID must "
                        "charge once; currently charges twice."
                    ),
                    "test_contract": (
                        "One unittest.TestCase method only, using app.checkout."
                        "CheckoutGateway and CheckoutService. Assert one charge after "
                        "two complete calls. Assertion message must be "
                        "RF_DUPLICATE_TRANSACTION. No pytest, external deps, setup "
                        "code or unrelated tests."
                    ),
                },
            )
        test = proposal.reproduction
        if "RF_DUPLICATE_TRANSACTION" not in test.test_source:
            raise ValueError("MISSING_BEHAVIOR_MARKER")
        (output / "triage.json").write_text(
            proposal.model_dump_json(indent=2), encoding="utf-8"
        )
        report["model"] = model.model
        report["test_sha256"] = test.test_source_sha256()
        if not args.resume_baseline:
            baseline = run_checkout(sandbox, test)
        report["baseline"] = baseline
        save()
        if (
            baseline["exit_code"] != 1
            or not baseline["behavioral_failure"]
            or baseline["setup_error"]
            or not baseline["tests_ran"]
        ):
            raise ValueError("BASELINE_NOT_BEHAVIORAL")
        owner, repo, run_id = uuid4(), uuid4(), uuid4()
        baseline_ref = (
            "sha256:"
            + hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest()
        )
        state = RunState(
            run_id=run_id,
            incident_id=uuid4(),
            repository_id=repo,
            source_commit=str(baseline["source_commit"]),
            policy_version="synthetic-checkout-v1",
            trace_id=uuid4(),
            current_stage=Stage.PATCHING,
            source_manifest_ref="sha256:" + hashlib.sha256(source.encode()).hexdigest(),
            reproduction_test_ref="sha256:" + test.test_source_sha256(),
            baseline_result_ref=baseline_ref,
        )
        grant = RepositoryGrant(
            repository_id=repo,
            owner_id=owner,
            remote_url="https://github.com/replayforge/synthetic-checkout",
            commits=(state.source_commit,),
            approved_paths=("app/", "tests/"),
            consent=True,
        )
        patch, admission = request_patch(
            model,
            {"app/checkout.py": source},
            "RF_DUPLICATE_TRANSACTION: retry produces two charges; expected one.",
            state,
            grant,
            owner,
        )
        (output / "patch.json").write_text(
            patch.model_dump_json(indent=2), encoding="utf-8"
        )
        report["patch_ref"] = admission.patch_ref
        for phase in ("candidate", "clean_replay"):
            observed = run_checkout(sandbox, test, patch.unified_diff)
            report[phase] = observed
            save()
            if (
                observed["exit_code"] != 0
                or observed["setup_error"]
                or not observed["tests_ran"]
                or observed["source_commit"] != baseline["source_commit"]
                or observed["test_sha256"] != baseline["test_sha256"]
            ):
                raise ValueError("PATCH_REPLAY_FAILED")
        candidate = report["candidate"]
        replay = report["clean_replay"]
        if (
            not isinstance(candidate, dict)
            or not isinstance(replay, dict)
            or candidate["tree"] != replay["tree"]
        ):
            raise ValueError("TREE_MISMATCH")
        report["status"] = "FIXTURE_REPAIR_PASSED"
    except Exception as error:
        # Stable class only. Provider payload/source/credential text is excluded.
        report.update(status="INCONCLUSIVE", error_class=type(error).__name__)
        if isinstance(error, ProbeError):
            report["error_code"] = str(error)
        save()
        print(
            json.dumps(
                {"status": report["status"], "error_class": report["error_class"]}
            )
        )
        return 2
    save()
    print(
        json.dumps({"status": report["status"], "report": str(output / "report.json")})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
