"""Operator CLI. Grant/task commands must never become agent tools."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from pydantic import ValidationError

from .clock import FixedClock
from .domain import (
    Grant,
    PolicyDenied,
    PurchaseConflict,
    PurchaseOutcome,
    PurchaseRequest,
    format_usdc,
)
from .runtime import Runtime


def summary(outcome: PurchaseOutcome) -> dict:
    return {
        "mode": "simulated",
        "purchase_id": outcome.purchase_id,
        "payment_state": outcome.payment_state,
        "delivery_state": outcome.delivery_state,
        "amount_atomic": outcome.amount,
        "amount_sim_usdc": format_usdc(outcome.amount),
        "payment_error": outcome.payment_error,
        "delivery_error": outcome.delivery_error,
        "simulated_receipt_id": outcome.receipt.receipt_id if outcome.receipt else None,
        "resource": outcome.resource.resource.model_dump() if outcome.resource else None,
    }


def grant(runtime: Runtime, grant_id: str, budget: int, cap: int) -> None:
    runtime.ledger.add_grant(
        Grant(
            grant_id=grant_id,
            operator_id="local-operator",
            service_ids=tuple(s.service_id for s in runtime.catalog.list()),
            purpose="research",
            budget=budget,
            per_purchase_limit=cap,
            issued_at=runtime.clock.now(),
            expires_at=runtime.clock.now() + 3600,
        ),
        runtime.clock.now(),
    )


async def demo(runtime: Runtime) -> dict:
    grant(runtime, "demo-grant", 40_000, 30_000)
    for name in ("demo-paid", "demo-denied"):
        runtime.ledger.create_task(name, "demo-grant", runtime.clock.now())
    request = PurchaseRequest(
        service_id="report-standard", subject="offline-demo", purpose="research"
    )
    quote = await runtime.coordinator.quote(request)
    paid = await runtime.buyer("demo-paid").purchase(request, quote)
    denied = await runtime.buyer("demo-denied").purchase(
        request,
        quote.model_copy(update={"merchant_id": "unauthorized-merchant"}),
    )
    return {
        "mode": "simulated",
        "paid": summary(paid),
        "denied": summary(denied),
        "budget": runtime.ledger.budget("demo-grant").model_dump(),
        "adapter": runtime.payments.statistics(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Offline payment simulation; no wallets or network"
    )
    result.add_argument("--state-dir", type=Path, default=Path(".lab"))
    result.add_argument(
        "--now",
        type=int,
        default=1_767_225_600,
        help="Explicit simulation Unix time (default: 2026-01-01 UTC)",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Repeatable paid and unauthorized examples")
    commands.add_parser("list", help="Approved catalog and schemas")
    init = commands.add_parser("init", help="Operator: create an immutable spending grant")
    init.add_argument("--grant-id", default="local-grant")
    init.add_argument("--budget", type=int, default=40_000)
    init.add_argument("--cap", type=int, default=30_000)
    task = commands.add_parser("task", help="Operator: bind a logical task to a grant")
    task.add_argument("task_id")
    task.add_argument("--grant-id", default="local-grant")
    for name in ("quote", "purchase"):
        command = commands.add_parser(name)
        command.add_argument("--service", default="report-standard")
        command.add_argument("--subject", default="offline-demo")
        command.add_argument("--purpose", default="research")
        if name == "purchase":
            command.add_argument("task_id")
            command.add_argument("--quote-id", required=True)
    for name in ("status", "audit", "reconcile"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
        if name == "reconcile":
            command.add_argument(
                "--cancel",
                action="store_true",
                help="Operator: request authoritative simulation cancellation",
            )
    return result


async def run(args: argparse.Namespace) -> dict | list:
    runtime = Runtime(args.state_dir, clock=FixedClock(args.now))
    if args.command == "demo":
        return await demo(runtime)
    if args.command == "list":
        return runtime.catalog.describe()
    if args.command == "init":
        grant(runtime, args.grant_id, args.budget, args.cap)
        return runtime.ledger.budget(args.grant_id).model_dump()
    if args.command == "task":
        return runtime.ledger.create_task(args.task_id, args.grant_id, args.now).model_dump()
    if args.command in ("quote", "purchase"):
        request = PurchaseRequest(
            service_id=args.service, subject=args.subject, purpose=args.purpose
        )
        if args.command == "quote":
            return (await runtime.coordinator.quote(request)).model_dump()
        quote = runtime.ledger.quote(args.quote_id)
        return summary(await runtime.buyer(args.task_id).purchase(request, quote))
    if args.command == "reconcile":
        return summary(await runtime.coordinator.reconcile(args.task_id, cancel=args.cancel))
    purchase_id = runtime.ledger.task(args.task_id).purchase_id
    if args.command == "audit":
        return runtime.ledger.export_audit(purchase_id)
    return summary(runtime.ledger.outcome(purchase_id))


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except (KeyError, ValidationError, PolicyDenied, PurchaseConflict, httpx.HTTPError):
        # Do not print raw exceptions, request bodies, credentials, or receipts.
        print(json.dumps({"mode": "simulated", "error": "invalid_or_conflicting_operation"}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
