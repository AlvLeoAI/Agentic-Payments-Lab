import asyncio
import socket
from unittest.mock import Mock

import pytest

from agentic_payments_lab.domain import Grant, PurchaseRequest
from agentic_payments_lab.runtime import Runtime


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 1 tests must not access the network")

    original = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            forbidden()
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def authorize(
    runtime,
    *,
    budget=40_000,
    cap=30_000,
    expires_in=3600,
    services=("report-free", "report-standard", "report-premium"),
):
    runtime.ledger.add_grant(
        Grant(
            grant_id="grant",
            operator_id="operator",
            service_ids=services,
            purpose="research",
            budget=budget,
            per_purchase_limit=cap,
            issued_at=runtime.clock.now(),
            expires_at=runtime.clock.now() + expires_in,
        ),
        runtime.clock.now(),
    )
    runtime.ledger.create_task("task", "grant", runtime.clock.now())


@pytest.fixture
def runtime(tmp_path):
    value = Runtime(tmp_path)
    value.payments.execute = Mock(wraps=value.payments.execute)
    return value


@pytest.fixture
def request_data():
    return PurchaseRequest(service_id="report-standard", subject="test-report", purpose="research")


@pytest.fixture
def prepared(runtime, request_data):
    authorize(runtime)
    return runtime, request_data, asyncio.run(runtime.coordinator.quote(request_data))
