import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import pytest
from preflight_support import KEYS, mock_factory, reply

from agentic_payments_lab.buyer_agent import comparison_preflight as module
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison_preflight import (
    Preflight,
    PreflightExchange,
    initialize_preflight,
    run_authenticated_preflight,
    run_mock_preflight,
)
from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS, ENDPOINTS
from agentic_payments_lab.buyer_agent.comparison_wire import MODEL_ENDPOINTS, ProviderWire
from agentic_payments_lab.domain import canonical, digest


def run(preflight, factory, keys=KEYS):
    return asyncio.run(
        run_mock_preflight(
            preflight,
            api_keys=keys,
            approved_policy_hash=preflight.policy_hash,
            reviewed_source_hash=digest(preflight.policy["source"]),
            transports={p: factory(p) for p in KEYS},
        )
    )


def activate(preflight):
    preflight.activate(preflight.policy_hash, digest(preflight.policy["source"]))


def assert_no_markers(preflight, captured=""):
    evidence = (
        preflight.path.read_bytes() + canonical(preflight.report()).encode() + captured.encode()
    )
    for path in preflight.path.parent.glob("*.sqlite3-*"):
        evidence += path.read_bytes()
    assert all(key.get_secret_value().encode() not in evidence for key in KEYS.values())


def test_exact_native_preflight_requests_headers_admission_and_no_generation(tmp_path, monkeypatch):
    preflight = initialize_preflight(tmp_path / "preflight")
    calls, outbound, auxiliary_reads = [], [], []
    original_get = os.environ.get

    def guarded_get(name, *args):
        if name in (
            "OPENAI_API_KEY",
            "OPENAI_ADMIN_KEY",
            "OPENAI_WEBHOOK_SECRET",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT_ID",
            "ANTHROPIC_API_KEY",
        ):
            auxiliary_reads.append(name)
            pytest.fail("No auxiliary credential lookup")
        return original_get(name, *args)

    monkeypatch.setattr(os.environ, "get", guarded_get)

    def factory(provider):
        async def send(request):
            record = preflight.report()["attempts"][-1]
            assert record["status"] == "dispatch_authorized" and record["http_status"] is None
            assert record["payload_hash"] == hashlib.sha256(request.content).hexdigest()
            assert record["provider"] == provider
            name = "authorization" if provider == "openai" else "x-api-key"
            assert (
                request.headers[name]
                == ("Bearer " if provider == "openai" else "") + KEYS[provider].get_secret_value()
            )
            assert ("x-api-key" if provider == "openai" else "authorization") not in request.headers
            assert all(
                KEYS[p].get_secret_value() not in str(request.headers)
                for p in KEYS
                if p != provider
            )
            assert request.extensions == {
                "timeout": dict.fromkeys(("connect", "read", "write", "pool"), 30)
            }
            outbound.append(request)
            calls.append((provider, request.method, str(request.url), request.content))
            return reply(provider, request)

        return httpx2.MockTransport(send)

    report = run(preflight, factory)
    assert report["state"]["status"] == "finished" and report["verified_responses"] == 6
    assert report["observed_http_responses"] == 6 and report["unknown_outcomes"] == 0
    assert report["generation_attempts"] == 0 and report["generation_ready"] is False
    assert report["observed_billing_cost"] is None and not report["financial_databases_created"]
    assert not auxiliary_reads and len(calls) == 6
    for provider in KEYS:
        assert (
            sum(p == provider and url == MODEL_ENDPOINTS[provider] for p, _, url, _ in calls) == 1
        )
        assert (
            sum(p == provider and url == COUNT_ENDPOINTS[provider] for p, _, url, _ in calls) == 2
        )
    for call, step in zip(calls, preflight.policy["plan"], strict=True):
        assert call[:3] == (step["provider"], step["method"], step["endpoint"])
        assert (
            call[3] == b""
            if step["kind"] == "model"
            else canonical(json.loads(call[3])) == canonical(step["body"])
        )
        assert call[2] not in ENDPOINTS.values()
    assert all(
        request.headers.get("authorization", request.headers.get("x-api-key")) == "[redacted]"
        for request in outbound
    )
    assert_no_markers(preflight)
    with pytest.raises(BudgetDenied):
        run(Preflight(preflight.path), factory)
    assert len(calls) == 6


@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 429, 500])
def test_http_failure_stops_all_probes_without_retry(tmp_path, status):
    preflight = initialize_preflight(tmp_path / "p")
    calls = []
    factory = mock_factory(
        calls,
        mutate=lambda *_: httpx2.Response(
            status,
            headers={"location": ENDPOINTS["anthropic"], "retry-after": "0"},
            json={"error": KEYS["openai"].get_secret_value()},
        ),
    )
    report = run(preflight, factory)
    assert len(calls) == 1 and report["state"]["status"] == "stopped"
    assert report["attempts"][0]["http_status"] == status and report["unknown_outcomes"] == 1
    assert report["generation_attempts"] == 0
    assert_no_markers(preflight)


@pytest.mark.parametrize(
    "kind",
    [
        "bad_model",
        "malformed",
        "oversize",
        "compression",
        "bad_count",
        "scope_change",
        "secret",
        "escaped_secret",
    ],
)
def test_unverified_response_stops_and_preserves_uncertainty(tmp_path, kind):
    preflight = initialize_preflight(tmp_path / kind)
    calls = []

    def mutate(provider, request, response):
        if kind == "bad_count" and str(request.url) not in COUNT_ENDPOINTS.values():
            return response
        if kind == "scope_change":
            response.headers["openai-project"] = "first" if len(calls) == 1 else "changed"
            return response
        if kind == "bad_model":
            data = json.loads(response.content)
            data["id"] = "other-model"
            return httpx2.Response(200, json=data)
        if kind == "bad_count":
            return httpx2.Response(
                200, json={"object": "response.input_tokens", "input_tokens": True}
            )
        if kind == "compression":
            response.headers["content-encoding"] = "gzip"
            return response
        if kind in ("secret", "escaped_secret"):
            secret = KEYS[provider].get_secret_value()
            data = json.loads(response.content)
            data["extra"] = secret
            raw = json.dumps(data)
            if kind == "escaped_secret":
                raw = raw.replace(secret, "".join(f"\\u{ord(c):04x}" for c in secret))
            return httpx2.Response(200, content=raw)
        return httpx2.Response(200, content=b"{" if kind == "malformed" else b"x" * 16385)

    report = run(preflight, mock_factory(calls, mutate=mutate))
    expected = 3 if kind in ("scope_change", "bad_count") else 1
    assert len(calls) == expected and report["state"]["status"] == "stopped"
    assert report["unknown_outcomes"] == 1 and report["generation_attempts"] == 0
    assert_no_markers(preflight)


@pytest.mark.parametrize(
    "mutation", ["generation", "query", "provider", "method", "extension", "body"]
)
def test_invalid_route_never_gets_a_dispatch_claim(tmp_path, mutation):
    preflight = initialize_preflight(tmp_path / mutation)
    activate(preflight)
    method, url, body, extensions = "GET", MODEL_ENDPOINTS["openai"], b"", {}
    if mutation == "generation":
        method, url = "POST", ENDPOINTS["openai"]
    elif mutation == "query":
        url += "?redirect=untrusted"
    elif mutation == "provider":
        url = MODEL_ENDPOINTS["anthropic"]
    elif mutation == "method":
        method = "DELETE"
    elif mutation == "extension":
        extensions = {"trace": lambda *_: None}
    else:
        body = b"{}"
    wire = ProviderWire(
        "openai",
        preflight,
        KEYS["openai"],
        httpx2.MockTransport(lambda _: pytest.fail("No dispatch")),
    )
    with pytest.raises((BudgetDenied, sqlite3.IntegrityError)):
        asyncio.run(
            wire.handle_async_request(
                httpx2.Request(method, url, content=body, extensions=extensions)
            )
        )
    assert preflight.report()["attempts"] == []


def test_mock_enrollment_cannot_construct_live_wire_and_custom_headers_rejected(
    tmp_path, monkeypatch
):
    preflight = initialize_preflight(tmp_path / "p")
    monkeypatch.setattr(module, "network_transport", lambda: pytest.fail("No network construction"))
    with pytest.raises(BudgetDenied, match="transport_mode"):
        asyncio.run(
            run_authenticated_preflight(
                preflight,
                api_keys=KEYS,
                approved_policy_hash=preflight.policy_hash,
                reviewed_source_hash=digest(preflight.policy["source"]),
            )
        )
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "SYNTHETIC_UNREAD_HEADER")
    with pytest.raises(BudgetDenied, match="ambient_headers"):
        run(preflight, mock_factory([]))
    assert preflight.report()["state"]["status"] == "prepared"


def test_concurrent_owner_claims_have_one_winner(tmp_path):
    preflight = initialize_preflight(tmp_path / "p")

    def claim(_):
        other = Preflight(preflight.path)
        try:
            activate(other)
            return True
        except (BudgetDenied, sqlite3.OperationalError):
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(4))) == 1


def test_cancellation_before_response_retains_claim_and_sanitizes_errors(tmp_path, caplog):
    preflight = initialize_preflight(tmp_path / "p")
    calls = []

    async def scenario():
        started = asyncio.Event()

        async def send(request):
            calls.append(1)
            logging.getLogger("httpcore.custom").error(KEYS["openai"].get_secret_value())
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_mock_preflight(
                preflight,
                api_keys=KEYS,
                approved_policy_hash=preflight.policy_hash,
                reviewed_source_hash=digest(preflight.policy["source"]),
                transports={p: httpx2.MockTransport(send) for p in KEYS},
            )
        )
        await started.wait()
        task.cancel(KEYS["openai"].get_secret_value())
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        return "".join(traceback.format_exception(error.value))

    captured = asyncio.run(scenario())
    report = preflight.report()
    assert calls == [1] and report["state"]["status"] == "stopped"
    assert report["reserved_attempts"] == report["unknown_outcomes"] == 1
    assert report["observed_http_responses"] == 0
    assert_no_markers(preflight, captured + caplog.text)
    with pytest.raises(BudgetDenied):
        run(Preflight(preflight.path), mock_factory(calls))


def test_response_is_recorded_before_close_failure(tmp_path):
    preflight = initialize_preflight(tmp_path / "p")

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield reply("openai", httpx2.Request("GET", MODEL_ENDPOINTS["openai"])).content

        async def aclose(self):
            raise RuntimeError(KEYS["openai"].get_secret_value())

    report = run(
        preflight, lambda _: httpx2.MockTransport(lambda _: httpx2.Response(200, stream=Stream()))
    )
    assert report["verified_responses"] == 1 and report["unknown_outcomes"] == 0
    assert report["state"]["status"] == "stopped"
    assert_no_markers(preflight)


def test_mock_path_rejects_real_transports_before_activation_or_authentication(
    tmp_path, monkeypatch
):
    preflight = initialize_preflight(tmp_path / "p")
    real = httpx2.AsyncHTTPTransport(retries=0, trust_env=False)
    monkeypatch.setattr(real, "handle_async_request", lambda _: pytest.fail("No real dispatch"))
    monkeypatch.setattr(module, "validate_key", lambda _: pytest.fail("No credential construction"))
    with pytest.raises(BudgetDenied, match="mock_transport_required"):
        run(preflight, lambda _: real)
    with pytest.raises(BudgetDenied, match="mock_transport_required"):
        PreflightExchange(preflight, "openai", KEYS["openai"], real)
    report = preflight.report()
    assert report["state"]["status"] == "prepared" and report["attempts"] == []
    asyncio.run(real.aclose())


def test_synthetic_history_uses_actual_tool_contracts_and_linked_results():
    from agentic_payments_lab.buyer_agent.boundary import ARGUMENTS
    from agentic_payments_lab.buyer_agent.contracts import (
        CatalogResult,
        PurchaseSummary,
        QuoteResult,
    )

    outputs = {
        "get_purchase_status": PurchaseSummary,
        "list_services": CatalogResult,
        "get_quote": QuoteResult,
        "purchase": PurchaseSummary,
    }
    plan = module.probe_plan()
    items = plan[4]["generation"]["input"]
    results = {}
    for index, item in enumerate(items):
        if item.get("type") != "function_call":
            continue
        arguments = json.loads(item["arguments"])
        ARGUMENTS[item["name"]].model_validate(arguments)
        result = items[index + 1]
        assert result["type"] == "function_call_output" and result["call_id"] == item["call_id"]
        results[item["name"]] = outputs[item["name"]].model_validate_json(result["output"])
        if item["name"] == "purchase":
            assert arguments == {"quote_id": results["get_quote"].quote_id}
    assert set(results) == set(outputs)
    assert results["get_purchase_status"].purchase_id == results["purchase"].purchase_id
    assert results["list_services"].services[0].service_id == results["get_quote"].service_id
    assert results["purchase"].amount_atomic == results["get_quote"].amount_atomic == 10000
    for step in plan[2:]:
        assert step["body"]["model"] == module.MODELS[step["provider"]]
        assert len(step["body"]["tools"]) == 4
        assert (
            step["generation"][
                "max_output_tokens" if step["provider"] == "openai" else "max_tokens"
            ]
            == 512
        )
        if step["provider"] == "openai":
            assert step["body"]["reasoning"] == {"effort": "none"}
            assert step["body"]["parallel_tool_calls"] is False
            assert all(t["strict"] for t in step["body"]["tools"])
        else:
            assert step["body"]["thinking"] == {"type": "disabled"}
            assert step["body"]["tool_choice"]["disable_parallel_tool_use"] is True
            assert step["body"]["output_config"]["effort"] == "medium"


@pytest.mark.parametrize("point", ["reserve", "permit", "wire_status", "observe", "finish"])
@pytest.mark.parametrize("after", [False, True])
def test_persistence_failure_never_retries_preflight(tmp_path, monkeypatch, point, after):
    preflight = initialize_preflight(tmp_path / "p")
    original = getattr(preflight, point)

    def failed(*args, **kwargs):
        if after:
            original(*args, **kwargs)
        raise sqlite3.OperationalError(KEYS["openai"].get_secret_value())

    monkeypatch.setattr(preflight, point, failed)
    calls = []
    report = run(preflight, mock_factory(calls))
    assert len(calls) == (0 if point in ("reserve", "permit") else 6 if point == "finish" else 1)
    assert report["state"]["status"] == ("finished" if point == "finish" and after else "stopped")
    before = len(calls)
    with pytest.raises(BudgetDenied):
        run(Preflight(preflight.path), mock_factory(calls))
    assert len(calls) == before and report["generation_attempts"] == 0
    assert_no_markers(preflight)


def test_source_or_approval_drift_cannot_activate(tmp_path, monkeypatch):
    preflight = initialize_preflight(tmp_path / "p")
    with pytest.raises(BudgetDenied, match="review_binding"):
        preflight.activate("0" * 64, digest(preflight.policy["source"]))
    monkeypatch.setattr(module, "source_witness", lambda: {})
    with pytest.raises(BudgetDenied, match="source_binding"):
        run(preflight, mock_factory([]))
    assert preflight.report()["state"]["status"] == "prepared"


@pytest.mark.parametrize("point", ["reserve", "permit", "dispatch", "headers", "observe"])
def test_abrupt_preflight_exit_never_resends_or_generates(tmp_path, point):
    preflight = initialize_preflight(tmp_path / "p")
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("comparison_wire_worker.py")),
            str(preflight.path.parent),
            "preflight",
            point,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 89, (child.stdout, child.stderr)
    reopened = Preflight(preflight.path)
    report = reopened.report()
    assert report["state"]["status"] == "running" and report["reserved_attempts"] == 1
    assert report["verified_responses"] == int(point == "observe")
    marker = preflight.path.parent / "independent-dispatches.txt"
    assert (len(marker.read_text().splitlines()) if marker.exists() else 0) == int(
        point not in ("reserve", "permit")
    )
    with pytest.raises(BudgetDenied):
        run(reopened, mock_factory([]))
    assert reopened.report() == report and report["generation_attempts"] == 0
