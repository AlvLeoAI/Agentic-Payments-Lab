"""Per-call model duration, total task time and their spread (NFR-L3).

The clock is scripted: only the doubles below advance it. Every expected span is
therefore exact, and a span that a wrapped host step would add is visible.
"""

import asyncio
import json

import httpx2
import pytest
from comparison_support import responders
from test_comparison import comparison as comparison
from test_comparison_live import assert_no_keys, run, scripted_wires
from test_comparison_live import enrolled as enrolled

from agentic_payments_lab.buyer_agent import comparison as module
from agentic_payments_lab.buyer_agent import comparison_runner as runner
from agentic_payments_lab.buyer_agent import comparison_wire as wire_module
from agentic_payments_lab.buyer_agent.boundary import Boundary
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison import Comparison, initialize_comparison
from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS

COUNT, BODY = 0.125, 0.25
HEADERS = {"openai": 0.5, "anthropic": 0.75}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    # Replaces the name inside these modules only: asyncio keeps the real clock.
    monkeypatch.setattr(runner, "time", value)
    monkeypatch.setattr(wire_module, "time", value)
    return value


def slow(monkeypatch, clock, owner, name, seconds):
    """Host step that takes scripted time before it runs unchanged."""
    original = getattr(owner, name)

    def wrapped(*args, **kwargs):
        clock.advance(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapped)


class SlowBody(httpx2.AsyncByteStream):
    def __init__(self, clock, content):
        self.clock, self.content = clock, content

    async def __aiter__(self):
        self.clock.advance(BODY)
        yield self.content

    async def aclose(self):
        pass


def call(kind, seconds, body_read=True):
    return {"kind": kind, "seconds": seconds, "body_read": body_read}


def spread(count, median, maximum):
    return {"count": count, "median": median, "maximum": maximum}


def test_call_durations_exclude_host_time_and_are_summarised(comparison, monkeypatch, clock):
    slow(monkeypatch, clock, Comparison, "reserve", 2.0)  # Journal, before the send.
    slow(monkeypatch, clock, Comparison, "observe", 1.0)  # Journal, after the body.
    slow(monkeypatch, clock, Boundary, "invoke", 4.0)  # Host tool.
    scripted = responders([])

    def factory(entry):
        normal = scripted(entry)

        async def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                clock.advance(COUNT)
                # The third episode never gets a count: the run stops there.
                return await normal(request) if entry["slot"] < 2 else httpx2.Response(500)
            clock.advance(HEADERS[entry["provider"]])
            response = await normal(request)
            return httpx2.Response(
                200,
                stream=SlowBody(clock, response.content),
                headers={"Content-Type": "application/json"},
            )

        return respond

    report = asyncio.run(runner.run_mock_comparison(comparison, factory))
    assert report["campaign"]["status"] == "stopped" and len(report["episodes"]) == 3
    openai = report["episodes"]["openai.trial-1.free"]["result"]
    anthropic = report["episodes"]["anthropic.trial-1.free"]["result"]
    stopped = report["episodes"]["openai.trial-1.paid"]["result"]

    # Five sequential requests each: a count, then a generation with its body.
    assert openai["model_calls"] == [call("count", COUNT), call("generation", 0.75)] * 5
    assert anthropic["model_calls"] == [call("count", COUNT), call("generation", 1.0)] * 5
    assert openai["model_call_scope"] == "send_to_response_body_read_excluding_journal_and_tools"
    # Task time also holds five reserves, five observes and four tool calls.
    host = 5 * 2.0 + 5 * 1.0 + 4 * 4.0
    assert openai["latency_seconds"] == 5 * (COUNT + 0.75) + host == 35.375
    assert anthropic["latency_seconds"] == 5 * (COUNT + 1.0) + host == 36.625
    assert openai["latency_scope"] == "offline_mock_including_host_tools_and_persistence"

    # A call without a complete body is kept as a lower bound, not dropped.
    assert stopped["termination"] == "interrupted"
    assert stopped["model_calls"] == [call("count", COUNT, body_read=False)]
    assert stopped["latency_seconds"] == COUNT

    assert report["latency"] == {
        "basis": "descriptive_spread_no_threshold_unread_calls_are_lower_bounds",
        "episodes_measured": 3,
        "episodes_not_measured": 0,
        "task_seconds": spread(3, 35.375, 36.625),
        "generation_calls_per_episode": spread(3, 5, 5),
        "generation_seconds": spread(10, 0.875, 1.0),
        "count_seconds": spread(11, COUNT, COUNT),
        "unread_calls": 1,
    }
    assert report["providers"]["openai"]["latency"] == {
        **report["latency"],
        "episodes_measured": 2,
        "task_seconds": spread(2, 17.75, 35.375),
        "generation_calls_per_episode": spread(2, 2.5, 5),
        "generation_seconds": spread(5, 0.75, 0.75),
        "count_seconds": spread(6, COUNT, COUNT),
    }
    assert report["providers"]["anthropic"]["latency"] == {
        **report["latency"],
        "episodes_measured": 1,
        "task_seconds": spread(1, 36.625, 36.625),
        "generation_calls_per_episode": spread(1, 5, 5),
        "generation_seconds": spread(5, 1.0, 1.0),
        "count_seconds": spread(5, COUNT, COUNT),
        "unread_calls": 0,
    }
    # The stored results are the only source: a reopened journal reports the same.
    assert Comparison(comparison.path).report()["latency"] == report["latency"]


def test_call_cut_off_by_the_deadline_is_recorded_as_a_lower_bound(tmp_path, clock):
    limited = asyncio.run(initialize_comparison(tmp_path / "timeout", timeout_seconds=1))

    def factory(entry):
        async def respond(request):
            if str(request.url) in COUNT_ENDPOINTS.values():
                clock.advance(COUNT)
                return await responders([])(entry)(request)
            clock.advance(2.0)
            await asyncio.Event().wait()

        return respond

    report = asyncio.run(runner.run_mock_comparison(limited, factory))
    result = report["episodes"]["openai.trial-1.free"]["result"]
    assert result["termination"] == "timeout"
    assert result["model_calls"] == [
        call("count", COUNT),
        call("generation", 2.0, body_read=False),
    ]
    assert result["latency_seconds"] == COUNT + 2.0
    assert report["latency"]["generation_seconds"] == spread(1, 2.0, 2.0)
    assert report["latency"]["unread_calls"] == 1


def test_live_call_duration_excludes_the_dispatch_claim_and_status_commits(
    enrolled, monkeypatch, clock
):
    claims = []
    claim_wire = Comparison.claim_wire

    def claim(self, provider, request):
        clock.advance(8.0)
        claims.append(provider)
        if len(claims) == 12:  # Second episode: its count was sent, its generation is refused.
            raise BudgetDenied("comparison_unreserved_dispatch")
        return claim_wire(self, provider, request)

    monkeypatch.setattr(Comparison, "claim_wire", claim)
    slow(monkeypatch, clock, Comparison, "wire_status", 16.0)

    async def network(entry, request, normal):
        clock.advance(COUNT if str(request.url) in COUNT_ENDPOINTS.values() else 0.5)
        return await normal(request)

    _, _, requests = scripted_wires(enrolled, monkeypatch, network)
    report = run(enrolled)
    assert report["campaign"]["status"] == "stopped" and len(requests) == 11
    first = report["episodes"]["openai.trial-1.free"]["result"]
    second = report["episodes"]["anthropic.trial-1.free"]["result"]

    # Only the network span: ten claims and ten status commits are outside it.
    assert first["model_calls"] == [call("count", COUNT), call("generation", 0.5)] * 5
    assert first["latency_seconds"] == 5 * (COUNT + 0.5) + 10 * (8.0 + 16.0) == 243.125
    assert first["latency_scope"] == "live_simulator_including_host_tools_and_persistence"
    # A refused dispatch was never sent, so it is not a model call. The earlier
    # count's span is not carried over to it.
    assert second["model_calls"] == [call("count", COUNT)]
    assert second["latency_seconds"] == 8.0 + COUNT + 16.0 + 8.0
    assert report["providers"]["openai"]["latency"]["generation_seconds"] == spread(5, 0.5, 0.5)
    assert report["providers"]["anthropic"]["latency"]["generation_seconds"] == spread(
        0, None, None
    )
    assert report["latency"]["unread_calls"] == 0
    assert_no_keys(enrolled, json.dumps(report))


def test_results_without_call_timing_are_counted_not_guessed():
    # Results written before call timing existed, and episodes with no result yet.
    older = {"latency_seconds": 1.5, "termination": "completed"}
    no_model = {"latency_seconds": 0.0, "model_calls": []}
    assert module.latency([None, older, no_model]) == {
        "basis": "descriptive_spread_no_threshold_unread_calls_are_lower_bounds",
        "episodes_measured": 0,
        "episodes_not_measured": 3,
        "task_seconds": spread(0, None, None),
        "generation_calls_per_episode": spread(0, None, None),
        "generation_seconds": spread(0, None, None),
        "count_seconds": spread(0, None, None),
        "unread_calls": 0,
    }
