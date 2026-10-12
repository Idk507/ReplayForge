"""Run with python -m scripts.check_nebius; network requires explicit --live."""

import argparse
import json

from packages.providers.nebius import NebiusConfig, ProbeError, probe


def main() -> int:
    """Validate configuration offline by default; never print credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="May incur inference charges"
    )
    args = parser.parse_args()
    try:
        config = NebiusConfig.from_env()
        if not args.live:
            print(
                json.dumps(
                    {"status": "NOT_RUN", "reason": "Live probe requires --live"}
                )
            )
            return 2
        print(probe(config).model_dump_json())
        return 0
    except ProbeError as error:
        print(json.dumps({"status": "NOT_VERIFIED", "error_class": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
