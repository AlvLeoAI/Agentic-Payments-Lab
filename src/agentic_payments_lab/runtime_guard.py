"""Verify the selected isolated runtime, not a package label or passing stress test.

This Linux x86_64 PBS build statically embeds SQLite in the Python executable.
No installation, credential access, networking or retained database access here.
"""

import _sqlite3
import hashlib
import platform
import sqlite3
import sys
from pathlib import Path

PYTHON_VERSION = (3, 12, 16)
SQLITE_VERSION = "3.53.1"
SQLITE_SOURCE_ID = (
    "2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9"
)
EXECUTABLE_SHA256 = "23bc148d2b48964b621c5561ff2b81c1d9c7b717967a7491efbf12f4304882b5"
ARCHIVE_SHA256 = "ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20"
ARCHIVE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/"
    "cpython-3.12.16%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
RUNTIME_DIRECTORY = "cpython-3.12.16-20261026"


class RuntimeUnverified(RuntimeError):
    pass


def runtime_evidence() -> dict:
    with sqlite3.connect(":memory:") as db:
        source = db.execute("SELECT sqlite_source_id()").fetchone()[0]
        options = sorted(row[0] for row in db.execute("PRAGMA compile_options"))
    executable = Path("/proc/self/exe").resolve(strict=True)
    return {
        "python_version": list(sys.version_info[:3]),
        "python_build": sys.version,
        "platform": sys.platform,
        "machine": platform.machine(),
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_source_id": source,
        "sqlite_module_origin": _sqlite3.__spec__.origin,
        "sqlite_compile_options": options,
    }


def require_pilot_runtime() -> dict:
    try:
        evidence = runtime_evidence()
        valid = (
            evidence["python_version"] == list(PYTHON_VERSION)
            and evidence["platform"] == "linux"
            and evidence["machine"] == "x86_64"
            and evidence["executable_sha256"] == EXECUTABLE_SHA256
            and evidence["sqlite_module_origin"] == "built-in"
            and evidence["sqlite_version"] == SQLITE_VERSION
            and evidence["sqlite_source_id"] == SQLITE_SOURCE_ID
        )
    except Exception:
        raise RuntimeUnverified("isolated_pilot_runtime_unverified") from None
    if not valid:
        raise RuntimeUnverified("isolated_pilot_runtime_unverified")
    return {**evidence, "sqlite_linkage": "static_in_verified_executable", "wal_reset_fix": True}
