"""Run the fixture evaluator inside a verified isolated namespace, on fresh state."""

import argparse
import asyncio
import json
from pathlib import Path

from validate_offline import require_isolation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args()
    require_isolation(args.parent_netns)
    from agentic_payments_lab.buyer_agent.evaluation import evaluate

    result = asyncio.run(evaluate(args.state_dir))
    print(json.dumps(result), flush=True)
    if not result["passed"]:
        raise SystemExit("Offline evaluation failed")


if __name__ == "__main__":
    main()
