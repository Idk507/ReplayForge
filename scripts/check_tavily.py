"""Optional fixed documentation smoke. --live may consume Tavily credits."""

import argparse
import json

from packages.contracts.research import DocumentationQuery
from packages.providers.tavily import (
    TavilyConfig,
    TavilyConfigError,
    TavilyResearchClient,
)


def main() -> int:
    """Keep incident text out of the smoke query and prevent default network I/O."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize one possibly billable public documentation search",
    )
    args = parser.parse_args()
    try:
        with TavilyResearchClient(TavilyConfig.from_env()) as client:
            result = client.search_docs(
                DocumentationQuery(
                    package="python", version="3.12", topic="transactions"
                ),
                allow_live=args.live,
            )
    except TavilyConfigError as error:
        print(json.dumps({"status": "NOT_RUN", "error_class": str(error)}))
        return 2
    print(result.model_dump_json())
    return 0 if result.status == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
