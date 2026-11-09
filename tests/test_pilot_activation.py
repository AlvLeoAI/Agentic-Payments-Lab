import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx2
import pytest
from activation_support import KEY, authority, body, request, scripted_wire
from pilot_support import response, usage

from agentic_payments_lab.buyer_agent import live_pilot, runner
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.pilot_envelope import (
    PilotEnvelope,
    initialize_activation,
)
from agentic_payments_lab.buyer_agent.pilot_transport import (
    PilotTransport,
    network_transport,
    validate_body,
)
from agentic_payments_lab.buyer_agent.scripted import tool_response
from agentic_payments_lab.domain import AgentMetadataError


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "pilot"
    return root, initialize_activation(root)


def run(pilot, scenario="purchase"):
    root, envelope = pilot
    return live_pilot.run_live_scenario(
        root, scenario, api_key=KEY, approved_enrollment_hash=envelope.report()["enrollment_hash"]
    )


def quiescent_files(root):
    # Read-only WAL connections may create/change SHM coordination state. On these
    # disposable fixtures, checkpoint and close before comparing every file/byte.
    # This does not erase changed rows, schema, accounting or extra files.
    for path in root.rglob("*.sqlite3"):
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_actual_sdk_three_scenarios_share_one_envelope(pilot, monkeypatch):
    root, envelope = pilot
    calls = []
    monkeypatch.setattr(live_pilot, "network_transport", lambda: scripted_wire(calls))
    monkeypatch.setattr(runner, "live_client", lambda _: pytest.fail("Legacy route forbidden"))
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://override.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-not-used")
    for scenario in ("purchase", "replay", "refusal"):
        result = asyncio.run(run(pilot, scenario))
        assert result["scenario_passed"], result
        assert result["agent"]["mode"] == "live"
        assert result["agent"]["model"] == "gpt-6-luna"
        assert result["agent"]["model_usage"]["source"] == "openai"
        assert authority(root) == (1, 1, 10000)
    record = PilotEnvelope(envelope.path).report()
    assert len(record["invocations"]) == 3 and len(record["requests"]) == len(calls)
    assert len(calls) <= 18 and record["known_estimate_nano_usd"] == 3645 * len(calls)
    assert record["usage_complete"] and record["reserved_estimate_nano_usd"] == 0
    assert record["policy"]["estimate_ceiling_nano_usd"] == 250_000_000
    for data in calls:
        validate_body(json.dumps(data).encode(), envelope.limits)
        call_ids = {i["call_id"] for i in data["input"] if i.get("type") == "function_call"}
        outputs = [i for i in data["input"] if i.get("type") == "function_call_output"]
        assert all(i["call_id"] in call_ids for i in outputs)
    continuations = [
        item for data in calls for item in data["input"] if item.get("type") == "function_call"
    ]
    assert continuations
    assert all(
        all(key in item and item[key] is None for key in ("async_", "caller", "namespace"))
        for item in continuations
    )
    with pytest.raises(BudgetDenied):
        asyncio.run(run(pilot, "purchase"))
    with pytest.raises(BudgetDenied):
        asyncio.run(run(pilot, "new-batch"))
    assert authority(root) == (1, 1, 10000)


def test_pinned_sdk_null_history_preserves_bytes_linkage_and_reservation(pilot):
    root, envelope = pilot
    item = tool_response("get_purchase_status", {}, "sdk-null").output[0].model_dump(mode="json")
    assert all(key in item and item[key] is None for key in ("async_", "caller", "namespace"))
    data = body()
    data["input"].extend(
        [item, {"type": "function_call_output", "call_id": item["call_id"], "output": "{}"}]
    )
    outbound = request(data)
    original_bytes = outbound.content
    seen = []

    def respond(req):
        seen.append(req.content)
        return httpx2.Response(200, json=response(1, []))

    envelope.begin_invocation("purchase")
    transport = PilotTransport(envelope, "purchase", KEY, httpx2.MockTransport(respond))
    asyncio.run(transport.handle_async_request(outbound))
    assert seen == [original_bytes]
    record = envelope.report()["requests"]
    assert len(record) == 1 and record[0]["status"] == "complete"
    assert record[0]["body_bytes"] == len(original_bytes)
    assert record[0]["body_hash"] == hashlib.sha256(original_bytes).hexdigest()
    assert authority(root) == (0, 0, 0)


@pytest.mark.parametrize("field", ["async_", "caller", "namespace"])
@pytest.mark.parametrize("value", [True, False, 0, "nonempty", {}, []])
def test_non_null_sdk_history_fields_reject_before_reservation(pilot, field, value):
    root, envelope = pilot
    item = tool_response("get_purchase_status", {}, "sdk-null").output[0].model_dump(mode="json")
    item[field] = value
    data = body()
    data["input"].append(item)
    envelope.begin_invocation("purchase")
    wire = httpx2.MockTransport(lambda _: pytest.fail("Non-null history must not dispatch"))
    transport = PilotTransport(envelope, "purchase", KEY, wire)
    with pytest.raises(BudgetDenied, match="pilot_request_rejected"):
        asyncio.run(transport.handle_async_request(request(data)))
    assert envelope.report()["requests"] == []
    assert authority(root) == (0, 0, 0)


@pytest.mark.parametrize("field", ["async", "configuration_update", "additional_tools"])
def test_unknown_history_fields_reject_even_when_null(pilot, field):
    _, envelope = pilot
    data = body()
    item = tool_response("get_purchase_status", {}, "sdk-null").output[0].model_dump(mode="json")
    item[field] = None
    data["input"].append(item)
    envelope.begin_invocation("purchase")
    wire = httpx2.MockTransport(lambda _: pytest.fail("Unknown history must not dispatch"))
    with pytest.raises(BudgetDenied, match="pilot_request_rejected"):
        asyncio.run(
            PilotTransport(envelope, "purchase", KEY, wire).handle_async_request(request(data))
        )
    assert envelope.report()["requests"] == []


@pytest.mark.parametrize("change", ["hash", "missing", "schema", "goal", "envelope"])
def test_wrong_or_missing_history_cannot_rebind_or_initialize(pilot, monkeypatch, change):
    root, envelope = pilot
    monkeypatch.setattr(live_pilot, "network_transport", lambda: pytest.fail("must not construct"))
    if change == "hash":
        with pytest.raises(BudgetDenied):
            asyncio.run(
                live_pilot.run_live_scenario(
                    root, "purchase", api_key=KEY, approved_enrollment_hash="forged"
                )
            )
        return
    if change in ("missing", "envelope"):
        (root / ("state/agent.sqlite3" if change == "missing" else "envelope.sqlite3")).unlink()
    elif change == "schema":
        with closing(sqlite3.connect(root / "state/payments.sqlite3")) as db, db:
            db.execute("DROP TABLE attempts")
    else:
        with closing(sqlite3.connect(root / "state/agent.sqlite3")) as db, db:
            db.execute("DELETE FROM bindings")
    before = quiescent_files(root)
    with pytest.raises((BudgetDenied, AgentMetadataError)):
        asyncio.run(run(pilot))
    after = quiescent_files(root)
    assert before == after
    with pytest.raises(FileExistsError):
        initialize_activation(root)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/responses",
        "https://evil.invalid/v1/responses",
        "https://api.openai.com/v1/responses?x=1",
        "https://api.openai.com/v1/responses/",
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com:444/v1/responses",
        "https://user@api.openai.com/v1/responses",
    ],
)
def test_exact_endpoint_before_reservation(pilot, url):
    _, envelope = pilot
    envelope.begin_invocation("purchase")
    wire = httpx2.MockTransport(lambda _: pytest.fail("dispatch forbidden"))
    transport = PilotTransport(envelope, "purchase", KEY, wire)
    with pytest.raises(BudgetDenied):
        asyncio.run(transport.handle_async_request(request(url=url)))
    assert envelope.report()["requests"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "background",
        "previous_response_id",
        "conversation",
        "prompt",
        "multi_agent",
        "prompt_cache_options",
        "tool_choice",
        "stream",
        "parallel",
        "reasoning",
        "model",
        "tier",
        "configuration_update",
        "image",
        "additional_tools",
        "tool_schema",
        "format",
    ],
)
def test_full_serialized_policy_is_fail_closed(pilot, mutation):
    _, envelope = pilot
    data = body()
    if mutation in ("configuration_update", "additional_tools"):
        data["input"].append({"type": mutation, "reasoning": {"effort": "max"}})
    elif mutation == "image":
        data["input"][0]["content"] = [{"type": "input_image", "image_url": "https://evil.invalid"}]
    elif mutation == "tool_schema":
        data["tools"][0]["parameters"] = {}
    elif mutation == "format":
        data["text"]["format"]["strict"] = False
    elif mutation == "parallel":
        data["parallel_tool_calls"] = True
    elif mutation == "reasoning":
        data["reasoning"]["mode"] = "pro"
    elif mutation == "model":
        data["model"] = "another-model"
    elif mutation == "tier":
        data["service_tier"] = "priority"
    else:
        data[mutation] = True
    with pytest.raises(BudgetDenied):
        validate_body(json.dumps(data).encode(), envelope.limits)
    assert envelope.report()["requests"] == []


@pytest.mark.parametrize(
    "bad",
    ["missing_usage", "unknown_category", "cache_lifetime", "model", "tier", "status", "exception"],
)
def test_bad_response_stops_whole_pilot_without_tool_execution(pilot, monkeypatch, bad):
    root, envelope = pilot
    calls = []

    def respond(req):
        calls.append(1)
        if bad == "exception":
            raise RuntimeError(KEY.get_secret_value())
        raw = response(1, tool_response("get_purchase_status", {}, "s").output)
        if bad == "missing_usage":
            del raw["usage"]
        elif bad == "unknown_category":
            raw["usage"]["audio_tokens"] = 1
        elif bad == "cache_lifetime":
            del raw["usage"]["input_tokens_details"]["cache_write_tokens_details"]
        elif bad == "model":
            raw["model"] = "unverified"
        elif bad == "tier":
            raw["service_tier"] = "priority"
        return httpx2.Response(429 if bad == "status" else 200, json=raw)

    monkeypatch.setattr(live_pilot, "network_transport", lambda: httpx2.MockTransport(respond))
    result = asyncio.run(run(pilot))
    assert not result["scenario_passed"] and result["agent"]["tool_attempts"] == 0
    assert result["pilot"]["blocked"] and result["pilot"]["reserved_estimate_nano_usd"] > 0
    assert result["pilot"]["estimated_total_nano_usd"] is None and calls == [1]
    assert authority(root) == (0, 0, 0)
    with pytest.raises(BudgetDenied):
        asyncio.run(run(pilot, "replay"))
    assert calls == [1]
    for f in root.rglob("*"):
        if f.is_file():
            assert KEY.get_secret_value().encode() not in f.read_bytes()


def test_stream_overflow_is_bounded_and_closed(pilot):
    _, envelope = pilot
    envelope.begin_invocation("purchase")

    class Stream(httpx2.AsyncByteStream):
        reads = 0
        closed = False

        async def __aiter__(self):
            while True:
                self.reads += 1
                yield b"x" * 4096

        async def aclose(self):
            self.closed = True

    stream = Stream()
    wire = httpx2.MockTransport(lambda _: httpx2.Response(200, stream=stream))
    with pytest.raises(BudgetDenied):
        asyncio.run(PilotTransport(envelope, "purchase", KEY, wire).handle_async_request(request()))
    assert stream.reads == 17 and stream.closed
    assert envelope.report()["blocked"] and envelope.report()["estimated_total_nano_usd"] is None


def test_network_factory_disables_proxy_retry_and_keeps_tls(monkeypatch):
    seen = []
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda **kw: seen.append(kw))
    network_transport()
    assert seen[0]["verify"] is True and seen[0]["trust_env"] is False
    assert seen[0]["proxy"] is None and seen[0]["retries"] == 0
    assert seen[0]["limits"].max_connections == 1


@pytest.mark.parametrize("after_payment,late", [(False, False), (False, True), (True, False)])
def test_external_cancellation_partial_usage_and_no_resume(pilot, monkeypatch, after_payment, late):
    root, envelope = pilot
    calls = []

    async def exercise():
        entered = asyncio.Event()

        async def respond(req):
            calls.append(json.loads(req.content))
            if not after_payment or len(calls) == 3:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not late:
                        raise
                    return httpx2.Response(200, json=response(1, []))
            if len(calls) == 1:
                result = tool_response("get_quote", {"service_id": "report-standard"}, "q")
            else:
                output = [i for i in calls[-1]["input"] if i.get("type") == "function_call_output"][
                    -1
                ]
                result = tool_response(
                    "purchase", {"quote_id": json.loads(output["output"])["quote_id"]}, "p"
                )
            return httpx2.Response(200, json=response(len(calls), result.output))

        monkeypatch.setattr(live_pilot, "network_transport", lambda: httpx2.MockTransport(respond))
        task = asyncio.create_task(run(pilot))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel(KEY.get_secret_value())
        with pytest.raises(asyncio.CancelledError, match="pilot_") as caught:
            await task
        assert KEY.get_secret_value() not in str(caught.value)

    asyncio.run(exercise())
    report = PilotEnvelope(envelope.path).report()
    assert len(report["requests"]) == (3 if after_payment else 1)
    assert report["known_estimate_nano_usd"] == (7290 if after_payment else None)
    assert report["estimated_total_nano_usd"] is None and not report["usage_complete"]
    assert report["blocked"] and report["invocations"][0]["outcome"] == "cancelled"
    assert authority(root) == ((1, 1, 10000) if after_payment else (0, 0, 0))
    with pytest.raises(BudgetDenied):
        asyncio.run(run(pilot, "replay"))


def test_one_cap_across_scenarios_and_reopens(tmp_path):
    root = tmp_path / "pilot"
    budget = initialize_activation(root, estimate_ceiling_nano_usd=515_000)
    budget.begin_invocation("purchase")
    token = budget.reserve("purchase", b"{}")
    assert budget.observe(token, usage(), "default", "gpt-6-luna")
    budget.finish_invocation("purchase", "completed")
    budget = PilotEnvelope(budget.path)
    budget.begin_invocation("replay")
    # The first actual estimate is charged to the SAME cap, never a per-task grant.
    with pytest.raises(BudgetDenied, match="planning_budget_exhausted"):
        budget.reserve("replay", b"{}")


def test_claim_and_request_limits_apply_across_all_scenarios(pilot):
    _, budget = pilot
    for scenario in ("purchase", "replay", "refusal"):
        budget.begin_invocation(scenario)
        for _ in range(6):
            token = budget.reserve(scenario, b"{}")
            assert budget.observe(token, usage(), "default", "gpt-6-luna")
        with pytest.raises(BudgetDenied, match="request_limit"):
            budget.reserve(scenario, b"{}")
        budget.finish_invocation(scenario, "completed")
        budget = PilotEnvelope(budget.path)
    assert len(budget.report()["requests"]) == 18
    with pytest.raises(BudgetDenied):
        budget.begin_invocation("different-goal-or-batch")


@pytest.mark.parametrize(
    "boundary", ["invocation", "reservation", "dispatch", "usage", "post_payment"]
)
def test_process_exit_fences_the_whole_pilot(pilot, tmp_path, boundary):
    root, envelope = pilot
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("activation_worker.py")),
            str(root),
            boundary,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 87, (completed.stdout, completed.stderr)
    record = PilotEnvelope(envelope.path).report()
    assert record["blocked"]
    with pytest.raises(BudgetDenied):
        PilotEnvelope(envelope.path).begin_invocation("replay")
    assert authority(root) == ((1, 1, 10000) if boundary == "post_payment" else (0, 0, 0))
    if boundary == "post_payment":
        assert len(record["requests"]) == 3 and record["known_estimate_nano_usd"] == 7290
    if boundary in ("reservation", "dispatch", "post_payment"):
        assert record["estimated_total_nano_usd"] is None


def test_separate_processes_can_claim_only_one_whole_pilot_invocation(pilot):
    root, envelope = pilot
    workers = [
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("activation_worker.py")),
                str(root),
                "claim",
                str(i),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    try:
        deadline = time.monotonic() + 30
        while not all((root / f"ready-{i}").exists() for i in range(4)):
            assert time.monotonic() < deadline and all(p.poll() is None for p in workers)
            time.sleep(0.02)
        (root / "go").touch()
        results = [p.communicate(timeout=10) for p in workers]
        assert all(p.returncode == 0 for p in workers), results
        assert sum(json.loads(out)["claimed"] for out, _ in results) == 1, results
        assert len(PilotEnvelope(envelope.path).report()["invocations"]) == 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
