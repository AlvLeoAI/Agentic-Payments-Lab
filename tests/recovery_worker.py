"""Test subprocess: exit without cleanup, then recover using separate OS memory."""

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from agentic_payments_lab.domain import PurchaseRequest
from agentic_payments_lab.runtime import Runtime


def forbidden(*args, **kwargs):
    raise AssertionError("Recovery worker must remain offline")


socket.socket.connect = forbidden
socket.getaddrinfo = forbidden
socket.create_connection = forbidden


def crash(point):
    if point == sys.argv[3]:
        os._exit(73)


async def main():
    runtime = Runtime(Path(sys.argv[1]), checkpoint=crash)
    if sys.argv[3] == "reconcile":
        outcome = await runtime.coordinator.reconcile("task")
        print(
            json.dumps(
                {
                    "payment_state": outcome.payment_state,
                    "delivery_state": outcome.delivery_state,
                    "stats": runtime.payments.statistics(),
                }
            )
        )
    else:
        request = PurchaseRequest(
            service_id="report-standard", subject="test-report", purpose="research"
        )
        await runtime.coordinator.purchase("task", request, runtime.ledger.quote(sys.argv[2]))
        raise AssertionError("Requested crash checkpoint was never reached")


if __name__ == "__main__":
    asyncio.run(main())
