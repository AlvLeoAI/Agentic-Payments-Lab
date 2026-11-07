"""Independent process boundaries for delivery/reopen; execution is forbidden."""

import asyncio
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from merchant_support import AuthorityDouble
from test_x402_gate import NOW, SigningDouble, policy_for_test

from agentic_payments_lab.domain import digest
from agentic_payments_lab.ledger import Ledger
from agentic_payments_lab.storage import Database
from agentic_payments_lab.x402_adapter.protocol import required_data
from agentic_payments_lab.x402_delivery import OfflineDeliveryAcceptor
from agentic_payments_lab.x402_merchant_store import MerchantStore
from agentic_payments_lab.x402_provider import OfflineMerchant, OfflineMerchantClient
from agentic_payments_lab.x402_store import OfflineGateStore

path, boundary = Path(sys.argv[1]), sys.argv[2]


def mark(name):
    with path.with_name(name).open("ab", buffering=0) as file:
        file.write(b"call\n")
        os.fsync(file.fileno())


def forbidden(name):
    async def call(*args, **kwargs):
        mark("forbidden-" + name)
        raise AssertionError("Delivery called an execution component")

    return call


SigningDouble.sign = forbidden("signer")
OfflineMerchantClient.dispatch = forbidden("dispatcher")
AuthorityDouble.settle = forbidden("settler")

if boundary.startswith("migrate_"):
    original_migration = Database.transaction

    @contextmanager
    def migration(self):
        if boundary == "migrate_concurrent":
            path.with_name(f"ready-{os.getpid()}").touch()
            deadline = time.monotonic() + 10
            while not path.with_name("release-workers").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Migration barrier timed out")
                time.sleep(0.01)
        with original_migration(self) as db:
            yield db
            if boundary == "migrate_before_commit":
                os._exit(96)
        if boundary == "migrate_after_commit":
            os._exit(96)

    Database.transaction = migration

policy = policy_for_test()
store = OfflineGateStore(path, policy)
if boundary.startswith("migrate_"):
    raise SystemExit(0)
merchant = OfflineMerchant(
    MerchantStore(
        path.with_name("merchant.sqlite3"), policy, digest(required_data(policy, identified=True))
    ),
    AuthorityDouble(path.with_name("authority.sqlite3")),
    lambda: NOW + 100,
)
original_retrieve = OfflineMerchantClient.retrieve


async def retrieve(self, purchase_id):
    if boundary == "before_retrieval":
        os._exit(96)
    mark("retrieval-calls")
    result = await original_retrieve(self, purchase_id)
    if boundary == "after_response":
        os._exit(96)
    if boundary == "concurrent":
        path.with_name(f"ready-{os.getpid()}").touch()
        deadline = time.monotonic() + 10
        while not path.with_name("release-workers").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Delivery barrier timed out")
            await asyncio.sleep(0.01)
    return result


OfflineMerchantClient.retrieve = retrieve
if boundary == "before_record":
    store.record_delivery = lambda *args: os._exit(96)
elif boundary == "after_insert":
    original_event = Ledger._event

    def event(db, purchase_id, event_type, data, now):
        original_event(db, purchase_id, event_type, data, now)
        if event_type == "offline_delivery_accepted":
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

asyncio.run(
    OfflineDeliveryAcceptor(store, OfflineMerchantClient(merchant), lambda: NOW + 100).accept(
        "task"
    )
)
if boundary != "concurrent":
    raise SystemExit("Expected process exit")
