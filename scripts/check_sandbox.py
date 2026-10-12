"""M0 synthetic remote probe; --live can incur charges. No repository execution."""

import argparse
import json

from services.runner.remote import SandboxConfig, SandboxConfigError
from services.runner.smoke import run_smoke


def main() -> int:
    """No network by default; report safe configuration/provider errors only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize one potentially billable synthetic remote operation",
    )
    args = parser.parse_args()
    try:
        config = SandboxConfig.from_env()
        report = run_smoke(config, allow_live=args.live)
    except SandboxConfigError as error:
        print(json.dumps({"status": "NOT_VERIFIED", "error_class": str(error)}))
        return 2
    print(report.model_dump_json())
    return 0 if report.status == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
