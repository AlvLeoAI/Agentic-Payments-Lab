"""One disposable abrupt-exit worker. No model, payment or network operations."""

import json
import os
import sys
import time
from pathlib import Path

from call_budget_support import usage

from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied, CallBudget

path, boundary = Path(sys.argv[1]), sys.argv[2]
budget = CallBudget(path / "call-budget.sqlite3")
if boundary == "claim":
    identity = sys.argv[3]
    (path / f"ready-{identity}.json").write_text(json.dumps({"pid": os.getpid()}))
    # No serial-start deadline. The parent bounds the complete cohort and reaps it.
    while not (path / "go").exists():
        time.sleep(0.01)
    try:
        budget.begin_invocation(identity)
        result = {"claimed": True}
    except BudgetDenied as error:
        result = {"claimed": False, "reason": str(error)}
    print(json.dumps(result), flush=True)
    sys.exit(0)
if boundary == "uncommitted_invocation":
    with budget.connect(write=True) as db:
        db.execute("INSERT INTO invocations VALUES('worker','owner','running','pending')")
        os._exit(79)
budget.begin_invocation("worker")
if boundary == "invocation":
    os._exit(79)
request = budget.reserve("worker", b'{"synthetic":"request"}')
if boundary == "reservation":
    os._exit(79)
# Independent marker represents the hypothetical dispatch boundary.
(path / "dispatch.txt").write_text("one mock dispatch")
if boundary == "dispatch":
    os._exit(79)
budget.observe(request, usage(), "default", "gpt-6-luna")
os._exit(79)
