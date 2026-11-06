"""Independent durable double instrumentation; never record transient headers."""

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from test_x402_gate import MARKER, NOW, SigningDouble, policy_for_test

from agentic_payments_lab.domain import PurchaseRequest, digest
from agentic_payments_lab.x402_adapter.gate import OfflineGate
from agentic_payments_lab.x402_adapter.protocol import (
    OfflineSignature,
    required_data,
    transient_header,
)
from agentic_payments_lab.x402_merchant_contracts import MerchantOrder, SettlementObservation
from agentic_payments_lab.x402_provider import OfflineMerchant, OfflineMerchantClient
from agentic_payments_lab.x402_store import OfflineGateStore


class AuthorityDouble:
    mode = "offline_double"

    def __init__(self, path, outcome="reported_success"):
        self.path, self.outcome = Path(path), outcome
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS calls (nonce TEXT, amount INTEGER);"
                "CREATE TABLE IF NOT EXISTS transfers (nonce TEXT PRIMARY KEY, amount INTEGER);"
            )

    async def settle(self, *, payment_header, authorization):
        # Count every invocation, independently of merchant/buyer-visible results.
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO calls VALUES(?,?)", (authorization.nonce, authorization.amount))
        if self.outcome == "reported_success":
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT INTO transfers VALUES(?,?)", (authorization.nonce, authorization.amount)
                )
        return SettlementObservation(
            outcome=self.outcome,
            transaction="0x" + "ab" * 32 if self.outcome == "reported_success" else None,
        )

    def counts(self):
        with sqlite3.connect(self.path) as db:
            return (
                db.execute("SELECT count(*) FROM calls").fetchone()[0],
                db.execute("SELECT count(*) FROM transfers").fetchone()[0],
                db.execute("SELECT coalesce(sum(amount),0) FROM transfers").fetchone()[0],
            )


@dataclass
class Scenario:
    buyer: OfflineGateStore
    merchant: OfflineMerchant
    authority: AuthorityDouble
    signer: SigningDouble
    request: PurchaseRequest
    now: list

    @property
    def client(self):
        return OfflineMerchantClient(self.merchant)

    def context(self, task="task"):
        return self.buyer.create_task(task, self.request, self.now[0])

    def enroll(self, task="task"):
        context = self.context(task)
        self.merchant.store.enroll(MerchantOrder(context=context, request=self.request))
        return context

    async def run(self, task="task"):
        context = self.context(task)
        header = await self.client.challenge(context.purchase_id, self.request)
        gate = OfflineGate(
            self.buyer, self.signer, self.client, lambda: self.now[0], identified=True
        )
        return await gate.run(task, header, resource=self.buyer.policy.resource, method="POST")

    def payment(self, task="task"):
        context = self.context(task)
        _, terms = self.buyer.claim(
            task, digest(required_data(self.buyer.policy, identified=True)), self.now[0]
        )
        header = transient_header(
            self.buyer.policy, terms, OfflineSignature(MARKER), self.now[0], identified=True
        )
        return context.purchase_id, header, terms

    def send(self, purchase_id, header, request=None):
        return asyncio.run(
            self.client._request(
                purchase_id=purchase_id, request=request or self.request, payment_header=header
            )
        )


def scenario(path):
    policy = policy_for_test()
    now = [NOW]
    buyer = OfflineGateStore.initialize(path / "buyer.sqlite3", policy)
    authority = AuthorityDouble(path / "authority.sqlite3")
    merchant = OfflineMerchant.initialize(
        path / "merchant.sqlite3", policy, authority, lambda: now[0]
    )
    result = Scenario(
        buyer,
        merchant,
        authority,
        SigningDouble(),
        PurchaseRequest(service_id="test-report", subject="synthetic", purpose="research"),
        now,
    )
    result.enroll()
    return result
