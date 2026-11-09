"""Supported local launch: clean child environment and temporary configuration roots.

This is configuration isolation, not an OS filesystem or network sandbox.
The parent user's environment and configuration are never altered.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..runtime_guard import require_pilot_runtime

CONFIG = """[server]
address = "127.0.0.1"
headless = true
enableCORS = true
enableXsrfProtection = true
enableStaticServing = false
fileWatcherType = "none"
runOnSave = false

[browser]
serverAddress = "127.0.0.1"
gatherUsageStats = false

[client]
toolbarMode = "minimal"
showErrorDetails = "none"
showErrorLinks = false

[secrets]
files = []

[theme]
base = "dark"
primaryColor = "#E8C468"
backgroundColor = "#17191E"
secondaryBackgroundColor = "#24272E"
textColor = "#F2F0E9"
linkColor = "#E8C468"
borderColor = "#6D717A"
font = "sans-serif"
baseFontSize = 16
headingFontSizes = ["2rem", "1.5rem", "1.25rem"]
metricValueFontSize = "1.6rem"
"""


@contextmanager
def isolated_settings(port=8501):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Viewer port must be an integer from 1024 to 65535")
    with tempfile.TemporaryDirectory(prefix="payments-viewer-") as directory:
        root = Path(directory)
        viewer_home = root / "profile"
        viewer_home.mkdir()
        config_dir = root / ".streamlit"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(CONFIG + '\n[logger]\nlevel = "error"\n')
        app = root / "app.py"
        app.write_text("from agentic_payments_lab.pilot_viewer.app import main\nmain()\n")
        # HOME still means a home directory, but belongs only to this disposable child.
        # Do not inherit credentials, proxies, PYTHONPATH or STREAMLIT_* overrides.
        env = {
            "HOME": str(viewer_home),
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "TMPDIR": str(root),
        }
        command = [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "streamlit",
            "run",
            str(app),
            "--server.port",
            str(port),
        ]
        yield root, app, env, command


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="Read-only historical pilot viewer")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    require_pilot_runtime()
    signal.signal(signal.SIGTERM, _interrupt)
    with isolated_settings(args.port) as (root, _, env, command):
        print(f"Historical pilot viewer: http://127.0.0.1:{args.port} — Ctrl+C to stop", flush=True)
        process = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            code = 0
        finally:
            stop_process(process)
        return code
