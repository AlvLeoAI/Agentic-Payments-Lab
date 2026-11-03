"""Validate inside an already isolated Linux network namespace; never fall back."""

import argparse
import json
import os
import shlex
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def require_isolation(parent_namespace: str) -> None:
    namespace = os.readlink("/proc/self/ns/net")
    interfaces = [name for _, name in socket.if_nameindex()]
    if namespace == parent_namespace or interfaces != ["lo"]:
        raise SystemExit("Network isolation required: expected a new namespace with only loopback")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,os,socket; print(json.dumps([os.readlink('/proc/self/ns/net'),"
            "[name for _,name in socket.if_nameindex()]]))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if json.loads(child.stdout) != [namespace, ["lo"]]:
        raise SystemExit("Network isolation required: child process did not inherit isolation")
    print(
        "Network isolation verified: new namespace, loopback only, inherited by subprocess",
        flush=True,
    )


def validate(uv: str) -> None:
    with tempfile.TemporaryDirectory(prefix="payments-offline-validation-") as directory:
        temporary = Path(directory)
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_PROJECT_ENVIRONMENT": sys.prefix,
            "RUFF_CACHE_DIR": str(temporary / "ruff-cache"),
        }

        def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
            command = [uv, *args]
            print(shlex.join(command), flush=True)
            return subprocess.run(
                command, check=True, env=environment, text=True, capture_output=capture
            )

        run("lock", "--check", "--offline")
        run("run", "--offline", "--no-sync", "ruff", "check", ".")
        run("run", "--offline", "--no-sync", "ruff", "format", "--check", ".")
        run(
            "run",
            "--offline",
            "--no-sync",
            "pytest",
            "-q",
            "-o",
            f"cache_dir={temporary / 'pytest-cache'}",
            f"--basetemp={temporary / 'pytest-data'}",
        )
        state = temporary / "demo"
        demo = ("run", "--offline", "--no-sync", "payments-lab", "--state-dir", str(state), "demo")
        first = run(*demo, capture=True)
        second = run(*demo, capture=True)
        if first.stdout != second.stdout:
            raise SystemExit("Repeated demo changed its result")
        result = json.loads(first.stdout)
        if (
            result["paid"]["payment_state"] != "settled"
            or result["paid"]["delivery_state"] != "delivered"
            or result["denied"]["payment_state"] != "denied"
        ):
            raise SystemExit("Demo did not produce the expected paid and denied outcomes")
        # Measure actual authority records, not just buyer-visible results or
        # the simulator's hardcoded signing_attempts field.
        with sqlite3.connect(f"file:{state / 'payments.sqlite3'}?mode=ro", uri=True) as db:
            outcomes = [
                json.loads(row[0]) for row in db.execute("SELECT result FROM authorizations")
            ]
            attempts = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        charges = sum(outcome["status"] == "settled" for outcome in outcomes)
        amount = sum(
            outcome["receipt"]["intent"]["amount"]
            for outcome in outcomes
            if outcome["status"] == "settled"
        )
        if (attempts, charges, amount) != (1, 1, 10_000):
            raise SystemExit("Repeated demo violated payment-authority idempotency")
        print(
            json.dumps(
                {
                    "demo_runs": 2,
                    "identical_results": True,
                    "execution_attempts": attempts,
                    "authority_charges": charges,
                    "charged_atomic": amount,
                }
            ),
            flush=True,
        )

        scenarios = (
            ("free", "basic", 0, 0, "not_required", 0),
            ("paid", "standard", 10_000, 10_000, "settled", 10_000),
            ("unaffordable", "premium", 10_000, 10_000, "not_started", None),
        )
        for label, quality, budget, cap, expected_state, expected_amount in scenarios:
            agent_state = temporary / f"agent-{label}"
            cli = (
                "run",
                "--offline",
                "--no-sync",
                "payments-lab",
                "--state-dir",
                str(agent_state),
            )
            run(*cli, "init", "--budget", str(budget), "--cap", str(cap), capture=True)
            run(*cli, "task", "agent-task", capture=True)
            run(
                *cli,
                "agent-init",
                "agent-task",
                "--subject",
                "offline-validation",
                "--purpose",
                "research",
                "--quality",
                quality,
                capture=True,
            )
            command = (
                *cli,
                "agent",
                "agent-task",
                "--mode",
                "offline",
                "--subject",
                "offline-validation",
                "--purpose",
                "research",
                "--quality",
                quality,
            )
            reports = [json.loads(run(*command, capture=True).stdout) for _ in range(2)]
            if (
                any(report["termination"] != "completed" for report in reports)
                or reports[0]["purchase"] != reports[1]["purchase"]
                or reports[0]["purchase"]["payment_state"] != expected_state
                or reports[0]["purchase"]["amount_atomic"] != expected_amount
            ):
                raise SystemExit(f"Offline agent scenario failed: {label}")
            with sqlite3.connect(
                f"file:{agent_state / 'payments.sqlite3'}?mode=ro", uri=True
            ) as db:
                outcomes = [
                    json.loads(row[0]) for row in db.execute("SELECT result FROM authorizations")
                ]
                attempts = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
            charges = sum(outcome["status"] == "settled" for outcome in outcomes)
            amount = sum(
                outcome["receipt"]["intent"]["amount"]
                for outcome in outcomes
                if outcome["status"] == "settled"
            )
            expected_count = 1 if label == "paid" else 0
            if (attempts, charges, amount) != (
                expected_count,
                expected_count,
                expected_amount or 0,
            ):
                raise SystemExit(f"Offline agent authority invariant failed: {label}")
            print(
                json.dumps(
                    {
                        "offline_agent_scenario": label,
                        "agent_runs": 2,
                        "same_purchase": True,
                        "execution_attempts": attempts,
                        "authority_charges": charges,
                        "charged_atomic": amount,
                    }
                ),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-netns", required=True)
    parser.add_argument("--uv", required=True, help="Absolute path to the installed uv executable")
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Run with the project virtual environment's Python after uv sync --locked")
    if not Path(args.uv).is_absolute():
        raise SystemExit("--uv must be an absolute executable path")
    require_isolation(args.parent_netns)
    validate(args.uv)


if __name__ == "__main__":
    main()
