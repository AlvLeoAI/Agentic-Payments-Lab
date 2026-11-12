"""Synthetic failure classes only; none diagnoses the stopped real request."""

import asyncio
import hashlib
import json
import logging
import sqlite3
import traceback
from contextlib import contextmanager

import httpx2
import pytest
from preflight_support import KEYS, mock_factory, reply
from test_comparison_live import prepare_review
from test_comparison_preflight import assert_no_markers, run

from agentic_payments_lab.buyer_agent import comparison_preflight as module
from agentic_payments_lab.buyer_agent.call_budget import BudgetDenied
from agentic_payments_lab.buyer_agent.comparison_live import (
    initialize_live_comparison,
    validate_generation_review,
)
from agentic_payments_lab.buyer_agent.comparison_preflight import (
    Preflight,
    initialize_preflight,
    run_mock_preflight,
)
from agentic_payments_lab.buyer_agent.comparison_protocol import COUNT_ENDPOINTS
from agentic_payments_lab.domain import digest


def check_failure(preflight, calls, stage, reason, verified=0):
    report = preflight.report()
    assert report["diagnostic"]["stage"] == stage
    assert report["diagnostic"]["reason"] == reason
    assert report["diagnostic_status"] == "recorded"
    assert not report["run_complete"] and not report["generation_ready"]
    assert report["verified_responses"] == verified
    assert report["generation_attempts"] == 0 and len(calls) == verified + 1
    before = list(calls)
    with pytest.raises(BudgetDenied):
        run(Preflight(preflight.path), mock_factory(calls))
    assert calls == before
    assert_no_markers(preflight)
    return report


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("extra", "count_fields"),
        ("missing", "count_fields"),
        ("bool", "count_type"),
        ("float", "count_type"),
        ("string", "count_type"),
        ("null", "count_type"),
        ("negative", "count_bounds"),
        ("large", "count_bounds"),
        ("wrong_object", "count_object"),
    ],
)
def test_count_rejections_have_exact_safe_codes_without_relaxing_contract(
    tmp_path, mutation, reason
):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(provider, request, response):
        if str(request.url) not in COUNT_ENDPOINTS.values():
            return response
        data = {"object": "response.input_tokens", "input_tokens": 900}
        if mutation == "extra":
            data["unexpected"] = "sensitive-unretained-field"
        elif mutation == "missing":
            data.pop("input_tokens")
        elif mutation == "wrong_object":
            data["object"] = "other"
        else:
            data["input_tokens"] = {
                "bool": True,
                "float": 900.0,
                "string": "900",
                "null": None,
                "negative": -1,
                "large": 1050001,
            }[mutation]
        return httpx2.Response(200, json=data)

    run(p, mock_factory(calls, mutate=mutate))
    report = check_failure(p, calls, "count", reason, verified=2)
    assert report["diagnostic"]["slot"] == 2 and report["diagnostic"]["http_status"] == 200
    assert report["diagnostic"]["attempt_status"] == "headers_received"
    assert report["unknown_outcomes"] == 1 and report["attempts"][2]["result"] is None
    assert "sensitive-unretained-field" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("conflicts", "scope_value_conflict"),
        ("invalid", "scope_invalid"),
    ],
)
def test_scope_conflict_and_invalid_value_are_rejected(tmp_path, mutation, reason):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(provider, request, response):
        if provider != "openai":
            return response
        count = str(request.url) in COUNT_ENDPOINTS.values()
        if mutation == "conflicts":
            response.headers["openai-project"] = "SYNTHETIC-PRIVATE-PROJECT" + (
                "-OTHER" if count else ""
            )
        elif mutation == "invalid" and count:
            response.headers["openai-project"] = "x" * 257
        return response

    run(p, mock_factory(calls, mutate=mutate))
    report = check_failure(p, calls, "scope", reason, verified=2)
    assert report["unknown_outcomes"] == 1
    assert "SYNTHETIC-PRIVATE-PROJECT" not in json.dumps(report)
    assert b"SYNTHETIC-PRIVATE-PROJECT" not in p.path.read_bytes()


@pytest.mark.parametrize(
    "kind,stage,reason",
    [
        ("malformed", "json", "json_invalid"),
        ("duplicate", "json", "json_invalid"),
        ("utf8", "decode", "decoding_failed"),
        ("oversize", "body", "response_limit"),
        ("encoding", "http", "encoding_rejected"),
        ("status", "http", "http_status_rejected"),
        ("scalar", "response", "response_contract"),
        ("model", "model", "model_mismatch"),
        ("secret", "reflection", "credential_reflection"),
    ],
)
def test_body_and_response_failure_classes_are_sanitized(
    tmp_path, kind, stage, reason, capsys, caplog
):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(provider, request, response):
        if kind == "encoding":
            response.headers["content-encoding"] = "gzip"
            return response
        if kind == "status":
            return httpx2.Response(503, json={"error": KEYS["openai"].get_secret_value()})
        if kind == "secret":
            return httpx2.Response(
                200, json={"id": KEYS["openai"].get_secret_value(), "object": "model"}
            )
        if kind == "model":
            return httpx2.Response(200, json={"id": "wrong", "object": "model"})
        raw = {
            "malformed": b'{"PRIVATE":',
            "duplicate": b'{"id":1,"id":2}',
            "utf8": b"\xff",
            "oversize": b"x" * 16385,
            "scalar": b"[]",
        }[kind]
        return httpx2.Response(200, content=raw)

    run(p, mock_factory(calls, mutate=mutate))
    check_failure(p, calls, stage, reason)
    captured = capsys.readouterr()
    assert_no_markers(p, captured.out + captured.err + caplog.text)


@pytest.mark.parametrize(
    "error,reason", [(httpx2.ReadError, "body_read_failed"), (RuntimeError, "unknown_exception")]
)
def test_partial_body_and_unknown_exception_never_retain_exception_text(tmp_path, error, reason):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"id":'
            raise error(KEYS["openai"].get_secret_value())

    run(p, mock_factory(calls, mutate=lambda *_: httpx2.Response(200, stream=Stream())))
    report = check_failure(p, calls, "body", reason)
    assert report["unknown_outcomes"] == 1


@pytest.mark.parametrize(
    "point,stage",
    [
        ("reserve", "reservation"),
        ("permit", "dispatch_permission"),
        ("wire_status", "headers_persistence"),
        ("observe", "observation_persistence"),
    ],
)
@pytest.mark.parametrize("after", [False, True])
def test_persistence_failures_retain_exact_boundary_and_earlier_evidence(
    tmp_path, monkeypatch, point, stage, after
):
    p = initialize_preflight(tmp_path / "p")
    calls = []
    original = getattr(p, point)

    def fail(*args, **kwargs):
        if after:
            original(*args, **kwargs)
        raise sqlite3.OperationalError(KEYS["openai"].get_secret_value())

    if point == "wire_status":
        connect = p.connect
        fired = False

        @contextmanager
        def broken_connection(write=False):
            nonlocal fired
            if write and p._stage == "headers_persistence" and not fired:
                fired = True
                if after:
                    with connect(write) as db:
                        yield db
                raise sqlite3.OperationalError(KEYS["openai"].get_secret_value())
            with connect(write) as db:
                yield db

        monkeypatch.setattr(p, "connect", broken_connection)
    else:
        monkeypatch.setattr(p, point, fail)
    report = run(p, mock_factory(calls))
    assert report["diagnostic"]["stage"] == stage
    assert report["diagnostic"]["reason"] == "persistence_failed"
    assert not report["run_complete"]
    assert report["verified_responses"] == int(point == "observe" and after)
    assert len(calls) == int(point in ("wire_status", "observe"))
    if point in ("wire_status", "observe"):
        assert report["diagnostic"]["http_status"] == 200
    if point == "observe" and after:
        assert report["attempts"][0]["status"] == "complete" and report["unknown_outcomes"] == 0
    assert_no_markers(p)


def test_diagnostic_persistence_failure_remains_unknown_and_blocks_restart(tmp_path, monkeypatch):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def fail(*_):
        raise sqlite3.OperationalError(KEYS["openai"].get_secret_value())

    monkeypatch.setattr(p, "record_failure", fail)
    report = run(p, mock_factory(calls, mutate=lambda *_: httpx2.Response(200, content=b"{")))
    assert (
        report["diagnostic"] is None
        and report["diagnostic_status"] == "unavailable_or_not_recorded"
    )
    assert (
        report["diagnostic_write_failure_observed_in_this_process"] and not report["run_complete"]
    )
    assert report["unknown_outcomes"] == 1 and len(calls) == 1
    reopened = Preflight(p.path)
    assert reopened.report()["diagnostic_status"] == "unavailable_or_not_recorded"
    with pytest.raises(BudgetDenied):
        run(reopened, mock_factory(calls))
    assert len(calls) == 1
    assert_no_markers(p)


def test_first_failure_is_bounded_immutable_and_cannot_accept_freeform_text(tmp_path):
    p = initialize_preflight(tmp_path / "p")
    calls = []
    report = run(p, mock_factory(calls, mutate=lambda *_: httpx2.Response(200, content=b"{")))
    first = report["diagnostic"]
    for _ in range(10):
        p.fail("client_close", "close_failed")
    assert p.report()["diagnostic"] == first
    with p.connect() as db:
        assert db.execute("SELECT count(*) FROM diagnostic").fetchone()[0] == 1
    with pytest.raises(ValueError):
        p.record_failure("unknown", KEYS["openai"].get_secret_value())
    with pytest.raises(sqlite3.IntegrityError), p.connect(True) as db:
        db.execute("DELETE FROM diagnostic")
    assert_no_markers(p)


def test_close_error_retains_observation_and_separate_first_failure(tmp_path):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield reply("openai", httpx2.Request("GET", module.MODEL_ENDPOINTS["openai"])).content

        async def aclose(self):
            raise RuntimeError(KEYS["openai"].get_secret_value())

    report = run(p, mock_factory(calls, mutate=lambda *_: httpx2.Response(200, stream=Stream())))
    assert report["diagnostic"]["stage"] == "response_close"
    assert report["diagnostic"]["reason"] == "close_failed"
    assert report["diagnostic"]["attempt_status"] == "complete"
    assert report["verified_responses"] == 1 and report["unknown_outcomes"] == 0
    assert not report["run_complete"] and report["attempts"][0]["result"]
    assert_no_markers(p)


def test_cancellation_diagnostic_is_fixed_and_propagated(tmp_path, caplog):
    p = initialize_preflight(tmp_path / "p")

    async def scenario():
        reached = asyncio.Event()

        async def send(request):
            logging.getLogger("private-test-logger").error(KEYS["openai"].get_secret_value())
            reached.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_mock_preflight(
                p,
                api_keys=KEYS,
                approved_policy_hash=p.policy_hash,
                reviewed_source_hash=digest(p.policy["source"]),
                transports={provider: httpx2.MockTransport(send) for provider in KEYS},
            )
        )
        await reached.wait()
        task.cancel(KEYS["openai"].get_secret_value())
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        return "".join(traceback.format_exception(error.value))

    trace = asyncio.run(scenario())
    assert p.report()["diagnostic"]["reason"] == "cancelled"
    assert p.report()["diagnostic"]["stage"] == "transport"
    assert p.report()["unknown_outcomes"] == 1 and not p.report()["run_complete"]
    assert_no_markers(p, trace + caplog.text)


def test_completed_responses_do_not_override_finalization_failure(tmp_path, monkeypatch):
    original = Preflight.finish

    def fail(self):
        original(self)
        raise sqlite3.OperationalError("PRIVATE_FINALIZATION_SENTINEL")

    monkeypatch.setattr(Preflight, "finish", fail)
    review = prepare_review(tmp_path, monkeypatch)
    with pytest.raises(BudgetDenied):
        validate_generation_review(review)
    report = Preflight(review["preflight_path"]).report()
    assert report["verified_responses"] == 6 and report["state"]["status"] == "finished"
    assert not report["run_complete"] and report["diagnostic"]["reason"] == "persistence_failed"
    with pytest.raises(BudgetDenied):
        asyncio.run(initialize_live_comparison(tmp_path / "live", generation_review=review))
    assert not (tmp_path / "live").exists()


def test_version_one_is_rejected_without_mutation_or_diagnostic_backfill(tmp_path):
    p = initialize_preflight(tmp_path / "p")
    with sqlite3.connect(p.path) as db:
        db.execute("DROP TABLE diagnostic")
        db.execute("PRAGMA user_version=1")
    before = hashlib.sha256(p.path.read_bytes()).hexdigest()
    with pytest.raises(BudgetDenied, match="schema_rejected"):
        Preflight(p.path)
    assert hashlib.sha256(p.path.read_bytes()).hexdigest() == before


def test_existing_json_encoding_acceptance_is_not_changed_by_diagnostics(tmp_path):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(provider, request, response):
        return httpx2.Response(
            200, content=json.dumps(json.loads(response.content)).encode("utf-16")
        )

    report = run(p, mock_factory(calls, mutate=mutate))
    assert report["run_complete"] and report["verified_responses"] == 6
    assert report["diagnostic"] is None and report["diagnostic_status"] == "no_failure_recorded"
    assert report["completion_confirmed"]


def test_failure_of_both_diagnostic_and_stop_persistence_retains_owner_fence(tmp_path, monkeypatch):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError(KEYS["openai"].get_secret_value())

    monkeypatch.setattr(p, "record_failure", fail)
    monkeypatch.setattr(p, "stop", fail)
    report = run(p, mock_factory(calls, mutate=lambda *_: httpx2.Response(200, content=b"{")))
    assert report["state"]["status"] == "running" and not report["run_complete"]
    assert report["diagnostic_status"] == "unavailable_or_not_recorded"
    assert report["unknown_outcomes"] == 1
    reopened = Preflight(p.path)
    with pytest.raises(BudgetDenied):
        run(reopened, mock_factory(calls))
    assert len(calls) == 1 and not reopened.report()["run_complete"]
    assert_no_markers(p)


def test_finalization_commit_and_diagnostic_loss_cannot_become_success_on_reopen(
    tmp_path, monkeypatch
):
    finish = Preflight.finish

    def failed_finish(self):
        finish(self)
        raise sqlite3.OperationalError("PRIVATE_FINALIZATION_SENTINEL")

    def lost_diagnostic(*args):
        raise sqlite3.OperationalError("PRIVATE_DIAGNOSTIC_SENTINEL")

    monkeypatch.setattr(Preflight, "finish", failed_finish)
    monkeypatch.setattr(Preflight, "record_failure", lost_diagnostic)
    review = prepare_review(tmp_path, monkeypatch)
    reopened = Preflight(review["preflight_path"])
    report = reopened.report()
    assert report["state"]["status"] == "finished" and report["verified_responses"] == 6
    assert report["diagnostic"] is None and not report["completion_confirmed"]
    assert (
        not report["run_complete"] and report["diagnostic_status"] == "unavailable_or_not_recorded"
    )
    # Even a newly constructed review hash of the reopened state cannot bypass it.
    review["preflight_result_hash"] = digest(report)
    with pytest.raises(BudgetDenied):
        asyncio.run(initialize_live_comparison(tmp_path / "live", generation_review=review))
    with pytest.raises(BudgetDenied):
        run(reopened, mock_factory([]))
    assert not (tmp_path / "live").exists()


def test_completed_marker_is_fact_boundary_not_proof_reporting_cannot_fail(tmp_path, monkeypatch):
    p = initialize_preflight(tmp_path / "p")
    confirm = p.confirm_completion

    def failed_confirm():
        confirm()
        raise sqlite3.OperationalError("PRIVATE_AFTER_BOUNDARY")

    def lost_diagnostic(*args):
        raise sqlite3.OperationalError("PRIVATE_DIAGNOSTIC_SENTINEL")

    monkeypatch.setattr(p, "confirm_completion", failed_confirm)
    monkeypatch.setattr(p, "record_failure", lost_diagnostic)
    report = run(p, mock_factory([]))
    assert report["completion_confirmed"] and report["verified_responses"] == 6
    assert (
        not report["run_complete"] and report["diagnostic_status"] == "unavailable_or_not_recorded"
    )
    reopened = Preflight(p.path).report()
    assert (
        reopened["completion_confirmed"] and reopened["diagnostic_status"] == "no_failure_recorded"
    )
    assert not reopened["post_completion_reporting_verified"]
    assert digest(reopened) != digest(report)  # A failed caller report is not a successful review.


def test_sdk_response_validation_failure_preserves_prior_observation(tmp_path):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(provider, request, response):
        data = json.loads(response.content)
        data.pop("created")
        return httpx2.Response(200, json=data)

    report = run(p, mock_factory(calls, mutate=mutate))
    assert len(calls) == 1 and report["verified_responses"] == 1
    assert report["diagnostic"]["stage"] == "sdk_response"
    assert report["diagnostic"]["reason"] == "sdk_response_rejected"
    assert report["diagnostic"]["attempt_status"] == "complete"
    assert not report["run_complete"] and not report["completion_confirmed"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "model_names,count_names",
    [([], [0]), ([0], []), ([0], [1]), ([0], [0, 1]), ([0, 1], [0]), ([], []), ([0, 1], [0, 1])],
)
def test_scope_presence_is_endpoint_local(tmp_path, provider, model_names, count_names):
    p = initialize_preflight(tmp_path / "p")
    calls = []

    def mutate(current, request, response):
        if current == provider:
            names = count_names if str(request.url) == COUNT_ENDPOINTS[provider] else model_names
            for index in names:
                response.headers[module.SCOPES[provider][index]] = f"SYNTHETIC-SCOPE-{index}"
        return response

    report = run(p, mock_factory(calls, mutate=mutate))
    assert report["run_complete"] and len(calls) == 6
    assert not report["generation_ready"]
    for attempt in report["attempts"]:
        result = attempt["result"]
        assert not result["billing_identity_verified"]
        assert not result["full_key_scope_verified"]
        names = count_names if attempt["kind"] == "count" else model_names
        expected = (
            {
                module.SCOPES[provider][i]: hashlib.sha256(
                    f"SYNTHETIC-SCOPE-{i}".encode()
                ).hexdigest()
                for i in names
            }
            if attempt["provider"] == provider
            else {}
        )
        assert result["reported_scope_hashes"] == expected
    assert "SYNTHETIC-SCOPE" not in json.dumps(report)
    assert b"SYNTHETIC-SCOPE" not in p.path.read_bytes()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("mutation", ["appears", "disappears", "conflicts", "model_conflict"])
def test_scope_repeated_endpoint_and_cross_endpoint_conflicts_stop(tmp_path, provider, mutation):
    p = initialize_preflight(tmp_path / "p")
    calls = []
    counts = 0

    def mutate(current, request, response):
        nonlocal counts
        if current != provider:
            return response
        count = str(request.url) == COUNT_ENDPOINTS[provider]
        if count:
            counts += 1
        value = None
        if mutation == "appears" and count and counts == 2:
            value = "SYNTHETIC-SCOPE-A"
        elif mutation == "disappears" and count and counts == 1:
            value = "SYNTHETIC-SCOPE-A"
        elif mutation == "conflicts" and count:
            value = "SYNTHETIC-SCOPE-A" if counts == 1 else "SYNTHETIC-SCOPE-B"
        elif mutation == "model_conflict":
            value = "SYNTHETIC-SCOPE-A" if not count else "SYNTHETIC-SCOPE-B"
        if value is not None:
            response.headers[module.SCOPES[provider][0]] = value
        return response

    run(p, mock_factory(calls, mutate=mutate))
    verified = (2 if mutation == "model_conflict" else 4) + (provider == "anthropic")
    reason = (
        "scope_presence_changed"
        if mutation in ("appears", "disappears")
        else "scope_value_conflict"
    )
    report = check_failure(p, calls, "scope", reason, verified=verified)
    assert report["unknown_outcomes"] == 1
    assert "SYNTHETIC-SCOPE" not in json.dumps(report)
    assert b"SYNTHETIC-SCOPE" not in p.path.read_bytes()
