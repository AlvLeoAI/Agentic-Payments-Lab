"""Exercise startup skew and failed-cohort diagnostics with real worker processes."""

import json
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest
from offline_buyer_support import counters, drive, finance, setup
from test_offline_buyer_recovery import last_run
from test_offline_buyer_recovery import (
    test_four_processes_enter_gate_together_with_one_execution as run_competing_buyers,
)


def test_slow_second_start_does_not_expire_the_first_worker(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []

    def start(*args, **kwargs):
        if len(children) == 1:
            # Exceed the original worker's ten-second gate deadline without
            # changing any production limit or the duration of gate contention.
            time.sleep(11)
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    run_competing_buyers(tmp_path)
    prepared = [float(p.read_text()) for p in tmp_path.glob("prepared-*")]
    ready = [float(p.read_text()) for p in tmp_path.glob("ready-*")]
    entered = [float(p.read_text()) for p in tmp_path.glob("entered-*")]
    events = [
        json.loads(line) for line in (tmp_path / "parent-events.jsonl").read_text().splitlines()
    ]
    released = next(event for event in events if event["stage"] == "release")
    assert max(prepared) - min(prepared) >= 11
    assert max(prepared) <= min(ready) <= max(ready) <= released["time"] <= min(entered)
    assert set(released["live_pids"]) == {child.pid for child in children}
    returned = [float(p.read_text()) for p in tmp_path.glob("returned-*")]
    finishes = [event["time"] for event in events if event["stage"] == "finish"]
    assert len(returned) == len(finishes) == 4
    assert max(returned) <= min(finishes)


def test_dead_waiter_is_not_released_and_full_stderr_is_retained(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []
    payload = "\n".join(f"worker diagnostic {index:04d}: " + "detail " * 12 for index in range(100))

    class ControlPipe:
        def __init__(self, pipe):
            self.pipe = pipe

        def write(self, data):
            # The first real process has reached the gate. Leave its marker,
            # then kill it before the parent can release any execution.
            assert (tmp_path / f"ready-{children[0].pid}").exists()
            children[0].kill()
            children[0].wait(timeout=5)
            with (tmp_path / "worker-0.stderr").open("a") as file:
                file.write(payload)
            return self.pipe.write(data)

        def flush(self):
            self.pipe.flush()

        def close(self):
            self.pipe.close()

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        if len(children) == 1:
            child.stdin = ControlPipe(child.stdin)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", start)
    with pytest.raises(pytest.fail.Exception) as failure:
        run_competing_buyers(tmp_path)
    message = str(failure.value)
    assert "Worker exited before release" in message
    assert payload in message  # The middle and end must survive, not an abbreviated repr.
    retained = Path(re.search(r"Preserved worker evidence: (\S+)", message)[1])
    assert (retained / "worker-0.stderr").read_text() == payload
    assert (retained / "database-rows.json").exists()
    assert not (retained / "release-workers").exists()
    assert len(children) == 4 and all(child.poll() is not None for child in children)
    with sqlite3.connect(f"file:{retained / 'authority.sqlite3'}?mode=ro", uri=True) as db:
        assert db.execute("SELECT count(*) FROM bridge_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM transfers").fetchone()[0] == 0
    with sqlite3.connect(f"file:{retained / 'buyer.sqlite3'}?mode=ro", uri=True) as db:
        assert db.execute("SELECT reserved,spent FROM grants").fetchone() == (0, 0)


@pytest.mark.parametrize("stage", ["event", "progress"])
def test_real_metadata_lock_after_payment_fails_closed_without_reexecution(
    tmp_path, monkeypatch, stage
):
    shop, buyer, metadata, goal = setup(tmp_path)
    event, progress = metadata.event, metadata.progress
    failures = []
    assert metadata.db.timeout == 0.1
    with closing(sqlite3.connect(metadata.db.path, isolation_level=None)) as holder:

        def observe_busy(operation, *args):
            try:
                operation(*args)
            except sqlite3.OperationalError as error:
                failures.append(error.sqlite_errorcode)
                holder.rollback()  # End the injected fault, not an application retry.
                raise

        def lock_after_purchase(run_id, value):
            # A separate connection holds a real lock at either post-payment
            # metadata boundary. Neither write may exceed its configured wait.
            if value.tool == "purchase" and stage == "event":
                holder.execute("BEGIN IMMEDIATE")
            observe_busy(event, run_id, value)
            if value.tool == "purchase" and stage == "progress":
                holder.execute("BEGIN IMMEDIATE")

        monkeypatch.setattr(metadata, "event", lock_after_purchase)
        monkeypatch.setattr(metadata, "progress", lambda report: observe_busy(progress, report))
        report = drive(buyer, metadata, goal)
    assert failures == [sqlite3.SQLITE_BUSY]
    assert report.termination == "metadata_error" and not report.metadata_persisted
    assert report.model_usage.requests_started == report.model_usage.responses_received == 4
    assert report.tool_attempts == 4  # No model continuation after the failed checkpoint.
    assert report.purchase.payment_state == "reconciliation_required"
    assert report.purchase.delivery_state == "not_requested"
    assert report.purchase.operator_reconciliation_required
    assert report.purchase.purchase_id == buyer.context.purchase_id
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    assert finance(shop) == (10_000, 0)
    saved = last_run(metadata)
    assert saved["termination"] == "metadata_error" and saved["run_complete"]
    assert not saved["metadata_persisted"]
    with metadata.db.connect() as db:
        assert db.execute("SELECT count(*) FROM tool_events").fetchone()[0] == (
            3 if stage == "event" else 4
        )
    monkeypatch.undo()
    assert drive(buyer, metadata, goal).purchase == report.purchase
    assert counters(shop) == (1, 1, 1, 1, 10_000)
    assert finance(shop) == (10_000, 0)
