"""Crash/parallel worker: existing fixture authority only, no execution components."""

import asyncio
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from evidence_support import reconciler
from merchant_support import AuthorityDouble
from test_x402_gate import SigningDouble, policy_for_test

from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_provider import OfflineMerchantClient
from agentic_payments_lab.x402_store import OfflineGateStore

state, boundary = Path(sys.argv[1]), sys.argv[2]


def forbid_execution(name):
    async def invoked(*args, **kwargs):
        with state.with_name("forbidden-execution.txt").open("ab", buffering=0) as file:
            file.write(name.encode() + b"\n")
            os.fsync(file.fileno())
        raise AssertionError("Reconciliation invoked an execution component")

    return invoked


SigningDouble.sign = forbid_execution("signer")
OfflineMerchantClient.dispatch = forbid_execution("dispatcher")
AuthorityDouble.settle = forbid_execution("settler")

if boundary in ("migrate_before_commit", "migrate_after_commit"):
    original_migration = Database.transaction

    @contextmanager
    def migration(self):
        with original_migration(self) as db:
            yield db
            if boundary == "migrate_before_commit":
                os._exit(96)
        os._exit(96)

    Database.transaction = migration

store = OfflineGateStore(state, policy_for_test())
if boundary == "migrate_only":
    raise SystemExit(0)
if boundary == "before_record":
    store.record_observation = lambda *args: os._exit(96)
elif boundary in ("after_observation", "after_accounting"):
    original_event = Ledger._event

    def event(db, purchase_id, event_type, data, now):
        original_event(db, purchase_id, event_type, data, now)
        if (
            boundary == "after_observation" and event_type == "offline_reconciliation_observed"
        ) or (boundary == "after_accounting" and event_type == "offline_fixture_applied"):
            os._exit(96)

    Ledger._event = event
elif boundary in ("before_commit", "after_commit"):
    original_transaction = store.db.transaction

    @contextmanager
    def transaction():
        with original_transaction() as db:
            yield db
            if boundary == "before_commit":
                os._exit(96)
        os._exit(96)

    store.db.transaction = transaction
elif boundary == "report_lost":
    store.report = lambda *args: os._exit(96)
elif boundary == "concurrent":
    original_record = store.record_observation

    def ready(*args):
        state.with_name(f"ready-{os.getpid()}").write_text("ready")
        deadline = time.monotonic() + 10
        while not state.with_name("release-observers").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Test barrier timed out")
            time.sleep(0.01)
        return original_record(*args)

    store.record_observation = ready

asyncio.run(reconciler(store).reconcile("task"))
if boundary != "concurrent":
    raise SystemExit("Expected forced exit")
