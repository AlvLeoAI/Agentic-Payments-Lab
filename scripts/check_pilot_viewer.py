"""Real loopback server smoke, permitted only inside verified OS network isolation."""

import argparse
import ctypes
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from validate_offline import require_isolation

from agentic_payments_lab.pilot_viewer.launch import stop_process


def watch_original_configuration(root):
    """Observe real file opens/accesses without ever consulting the user's real home."""
    library = ctypes.CDLL(None, use_errno=True)
    fd = library.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError("Cannot establish configuration-read probe")
    try:
        for name in ("profile", "project", "script"):
            location = root / name
            (location / ".streamlit").mkdir(parents=True)
            marker = "VIEWER_STARTUP_SENTINEL_" + name.upper()
            files = {
                location / ".env": "OPENAI_API_KEY=" + marker,
                location / ".streamlit/secrets.toml": f'key="{marker}"',
                location / ".streamlit/config.toml": (
                    '[server]\naddress="0.0.0.0"\n[browser]\ngatherUsageStats=true\n'
                    f'[theme]\nfont="{marker}"\n'
                ),
            }
            for path, content in files.items():
                path.write_text(content)
                if library.inotify_add_watch(fd, os.fsencode(path), 0x21) < 0:
                    raise OSError("Cannot watch original configuration")
        return fd
    except BaseException:
        os.close(fd)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args()
    require_isolation(args.parent_netns)
    # Only the verified, disposable namespace's loopback interface is changed.
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="viewer-startup-probe-") as directory:
        root = Path(directory)
        fd = watch_original_configuration(root)
        entrypoint = root / "script/view_pilot.py"
        entrypoint.write_bytes(Path(__file__).with_name("view_pilot.py").read_bytes())
        # Invoke the supported launcher from three hostile synthetic discovery roots.
        # Its Streamlit child must replace those roots, not merely override their values.
        env = {
            "HOME": str(root / "profile"),
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "OPENAI_API_KEY": "VIEWER_STARTUP_SENTINEL_ENV",
            "STREAMLIT_SERVER_ADDRESS": "0.0.0.0",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "true",
        }
        command = [sys.executable, "-I", "-B", str(entrypoint), "--port", str(port)]
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen(
                command,
                cwd=root / "project",
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Viewer server did not become healthy")
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    try:
                        connection.request("GET", "/_stcore/health")
                        response = connection.getresponse()
                        if response.status == 200 and response.read() == b"ok":
                            break
                    except OSError:
                        time.sleep(0.05)
                    finally:
                        connection.close()
                with open("/proc/net/tcp") as sockets:
                    bound = [
                        row.split()[1].split(":")[0]
                        for row in sockets.read().splitlines()[1:]
                        if row.split()[1].split(":")[1] == f"{port:04X}" and row.split()[3] == "0A"
                    ]
                if bound != ["0100007F"]:
                    raise RuntimeError("Viewer did not bind only to IPv4 loopback")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    connection.request("GET", "/")
                    response = connection.getresponse()
                    if response.status != 200 or b"<html" not in response.read():
                        raise RuntimeError("Viewer did not serve the application shell")
                finally:
                    connection.close()
                try:
                    events = os.read(fd, 65536)
                except BlockingIOError:
                    events = b""
                if events:
                    raise RuntimeError("Original config, secrets or environment files were read")
                log.flush()
                log.seek(0)
                if "VIEWER_STARTUP_SENTINEL" in log.read():
                    raise RuntimeError("Startup leaked a sentinel")
            finally:
                stop_process(process)
                os.close(fd)
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Viewer child survived launcher termination")
            print(
                json.dumps(
                    {
                        "viewer_server": "passed",
                        "loopback_only": True,
                        "original_configuration_files_opened": 0,
                        "sentinel_files_watched": 9,
                        "startup_route": "supported launcher",
                        "child_stopped": True,
                        "browser_rendering_tested_here": False,
                    }
                )
            )


if __name__ == "__main__":
    main()
