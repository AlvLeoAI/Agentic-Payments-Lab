"""Gap 12: the three timeouts that were configured but never seen to expire.

Synthetic keys and in-memory wires only. A deadline is expired while its request is
in flight by rescheduling it, so no test waits for 30 seconds. The pilot transport's
per-phase timeout belongs to the HTTP layer; its expiry is the exception that layer
raises, not a real socket timeout.
"""

import asyncio
import json

import httpx2
import pytest
import test_generation_probe as mock_probe
import test_generation_probe_live as live_probe
from activation_support import KEY, request
from comparison_support import count_payload
from test_comparison_preflight import run as run_preflight
from test_preflight_diagnostics import check_failure

from agentic_payments_lab.buyer_agent import generation_probe_live as live
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison_preflight import initialize_preflight
from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS, counted_estimate
from agentic_payments_lab.buyer_agent.generation_probe import LIMITS, Probe, run_mock_probe
from agentic_payments_lab.buyer_agent.pilot_envelope import initialize_activation
from agentic_payments_lab.buyer_agent.pilot_transport import PilotTransport


class Deadlines:
    """Every asyncio.timeout the code under test creates, with its configured delay."""

    def __init__(self, monkeypatch):
        self.made = []
        real = asyncio.timeout

        def timeout(delay):
            scope = real(delay)
            self.made.append((delay, scope))
            return scope

        monkeypatch.setattr(asyncio, "timeout", timeout)

    def delays(self):
        return [delay for delay, _ in self.made]

    async def expire_and_stall(self, delay, index=-1):
        scope = [made for configured, made in self.made if configured == delay][index]
        scope.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()


def stalled_generation(deadlines, calls):
    async def respond(wire_request):
        provider = "openai" if wire_request.url.host == "api.openai.com" else "anthropic"
        kind = "count" if str(wire_request.url) == COUNT_ENDPOINTS[provider] else "generation"
        calls.append((provider, kind))
        if kind == "generation":
            await deadlines.expire_and_stall(LIMITS.timeout_seconds)
        return httpx2.Response(200, json=count_payload(provider))

    return respond


def test_mock_probe_request_deadline_expires_and_keeps_the_hold(tmp_path, monkeypatch):
    probe = mock_probe.prepare(tmp_path)
    deadlines, calls = Deadlines(monkeypatch), []
    respond = stalled_generation(deadlines, calls)
    result = asyncio.run(
        run_mock_probe(
            probe,
            transports={p: httpx2.MockTransport(respond) for p in ("openai", "anthropic")},
            approved_policy_hash=probe.policy_hash,
        )
    )
    assert calls == [("openai", "count"), ("openai", "generation")]
    assert result["caller"] == {"outcome": "incomplete", "reason": "failed", "report": None}
    report = result["later_report"]
    assert report["state"]["status"] == "stopped" and report["tool_executions"] == 0
    assert report["retained_probe_allocation_nano"] == {
        "openai": counted_estimate("openai", 10),
        "anthropic": 0,
    }
    assert deadlines.delays().count(LIMITS.timeout_seconds) == 1
    with pytest.raises(BudgetDenied):
        mock_probe.run(Probe(probe.path), [])
    assert len(calls) == 2


def test_live_probe_request_deadline_expires_without_retry(tmp_path, monkeypatch):
    probe = live_probe.prepare(tmp_path)
    deadlines, calls = Deadlines(monkeypatch), []
    respond = stalled_generation(deadlines, calls)
    monkeypatch.setattr(live, "network_transport", lambda: httpx2.MockTransport(respond))
    result = asyncio.run(
        live.run_live_probe(probe, api_keys=live_probe.KEYS, approved_policy_hash=probe.policy_hash)
    )
    assert calls == [("openai", "count"), ("openai", "generation")]
    assert not result["overall_complete"] and result["caller"]["reason"] == "failed"
    report = result["later_report"]
    assert report["state"]["status"] == "stopped" and report["tool_executions"] == 0
    assert report["retained_probe_allocation_nano"]["openai"] >= 359400
    assert deadlines.delays().count(LIMITS.timeout_seconds) == 1
    assert all(k.get_secret_value() not in json.dumps(result) for k in live_probe.KEYS.values())


@pytest.mark.xfail(
    strict=True,
    reason="Known defect: the shared provider wire reports an expired deadline as "
    "'cancelled' first, and the preflight keeps only its first failure.",
)
@pytest.mark.parametrize("index", [0, 1], ids=["step_deadline", "exchange_deadline"])
def test_preflight_deadline_expiry_is_recorded_as_timeout(tmp_path, monkeypatch, index):
    preflight = initialize_preflight(tmp_path / "p")
    deadlines, calls = Deadlines(monkeypatch), []

    async def send(wire_request):
        calls.append(1)
        await deadlines.expire_and_stall(30, index)

    run_preflight(preflight, lambda _: httpx2.MockTransport(send))
    assert deadlines.delays().count(30) == 2  # The step, then its exchange.
    report = check_failure(preflight, calls, "transport", "timeout")
    assert report["state"]["status"] == "stopped"
    assert report["reserved_attempts"] == report["unknown_outcomes"] == 1
    assert report["observed_http_responses"] == 0


def test_pilot_wire_timeout_keeps_the_reservation_and_blocks_the_pilot(tmp_path):
    envelope = initialize_activation(tmp_path / "pilot")
    envelope.begin_invocation("purchase")
    seen = []

    def timed_out(wire_request):
        seen.append(wire_request.extensions["timeout"])
        raise httpx2.ReadTimeout("synthetic wire timeout")

    def send():
        wire = httpx2.MockTransport(timed_out)
        return asyncio.run(
            PilotTransport(envelope, "purchase", KEY, wire).handle_async_request(request())
        )

    with pytest.raises(BudgetDenied, match="pilot_response_unavailable"):
        send()
    limit = envelope.limits.timeout_seconds
    assert seen == [dict.fromkeys(("connect", "read", "write", "pool"), limit)]
    report = envelope.report()
    assert report["blocked"] and report["estimated_total_nano_usd"] is None
    assert report["reserved_estimate_nano_usd"] > 0 and len(report["requests"]) == 1
    with pytest.raises(BudgetDenied):
        send()  # No second request reaches the wire.
    assert len(seen) == 1
