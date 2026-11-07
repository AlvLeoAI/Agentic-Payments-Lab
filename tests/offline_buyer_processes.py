"""Parent-owned rendezvous and retained diagnostics for disposable buyer workers."""

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def competing_buyers(path, before_release):
    children, streams = [], []
    failure = None

    def record(stage, **data):
        with (path / "parent-events.jsonl").open("a") as file:
            file.write(json.dumps({"stage": stage, "time": time.monotonic(), **data}) + "\n")

    def wait_for(marker, deadline):
        while True:
            assert all(child.poll() is None for child in children), "Worker exited before release"
            assert time.monotonic() < deadline, f"Worker did not reach {marker.name}"
            if marker.exists():
                return
            time.sleep(0.01)

    try:
        for index in range(4):
            stdout = (path / f"worker-{index}.stdout").open("w")
            stderr = (path / f"worker-{index}.stderr").open("w")
            streams.extend((stdout, stderr))
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("offline_buyer_worker.py")),
                    str(path),
                    "concurrent",
                ],
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            children.append(child)
            record("spawn", pid=child.pid)
            # Imports/openers are outside every agent run and its timeout. EOF
            # aborts a prepared worker if the parent disappears before starting it.
            wait_for(path / f"prepared-{child.pid}", time.monotonic() + 10)

        # Keep metadata preparation serial, but all four actual gate calls must
        # overlap before ANY is released. One parent deadline covers this stage.
        deadline = time.monotonic() + 10
        for child in children:
            child.stdin.write("run\n")
            child.stdin.flush()
            wait_for(path / f"ready-{child.pid}", deadline)
        expected = {f"ready-{child.pid}" for child in children}
        assert {marker.name for marker in path.glob("ready-*")} == expected
        assert all(child.poll() is None for child in children), "Stale readiness marker"
        before_release()
        assert all(child.poll() is None for child in children), "Worker exited before release"
        record("release", live_pids=[child.pid for child in children])
        (path / "release-workers").touch()
        deadline = time.monotonic() + 20
        for child in children:
            wait_for(path / f"returned-{child.pid}", deadline)
        record("gates_returned", live_pids=[child.pid for child in children])
        # No SDK continuation starts until all original gate calls have returned.
        # A metadata lock timeout is an intentional fail-closed run outcome, not
        # an execution-ownership failure. Exercise that contract separately.
        for child in children:
            record("finish", pid=child.pid)
            (path / f"finish-{child.pid}").touch()
            child.wait(timeout=max(0, deadline - time.monotonic()))
        outputs = [
            ((path / f"worker-{i}.stdout").read_text(), (path / f"worker-{i}.stderr").read_text())
            for i in range(4)
        ]
        assert {marker.name for marker in path.glob("entered-*")} == {
            f"entered-{child.pid}" for child in children
        }
        yield children, outputs
    except Exception as error:
        failure = error
    finally:
        # Never release a failed cohort to make cleanup progress. Stop and reap
        # every child; there is no claim retry, cancellation or accounting repair.
        for child in children:
            if child.poll() is None:
                child.kill()
        for child in children:
            child.wait(timeout=5)
            child.stdin.close()
        for stream in streams:
            stream.close()
        record("exited", workers=[{"pid": p.pid, "returncode": p.returncode} for p in children])

        # These are fresh, synthetic fixtures only. Keep their logical rows and
        # full worker streams before pytest/validator removes its temporary tree.
        rows = {}
        for name in ("authority.sqlite3", "buyer.sqlite3", "agent.sqlite3", "merchant.sqlite3"):
            with sqlite3.connect(f"file:{path / name}?mode=ro", uri=True) as db:
                rows[name] = list(db.iterdump())
        (path / "database-rows.json").write_text(json.dumps(rows, indent=2) + "\n")
        if failure is not None:
            retained = Path(tempfile.mkdtemp(prefix="payments-buyer-worker-failure-"))
            shutil.copytree(path, retained, dirs_exist_ok=True)
            details = [
                f"{type(failure).__name__}: {failure}",
                f"Preserved worker evidence: {retained}",
            ]
            for name in ["parent-events.jsonl", *sorted(p.name for p in path.glob("worker-*"))]:
                details.append(f"\n--- {name} ---\n{(path / name).read_text()}")
            details.append("\n--- database rows ---\n" + json.dumps(rows, indent=2))
            # A literal multiline failure avoids pytest's abbreviated list repr.
            pytest.fail("\n".join(details), pytrace=False)
