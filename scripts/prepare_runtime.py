"""Explicitly install one checksum-pinned vendor runtime; preserve .venv and system Python."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agentic_payments_lab.runtime_guard import (  # noqa: E402
    ARCHIVE_SHA256,
    ARCHIVE_URL,
    EXECUTABLE_SHA256,
    RUNTIME_DIRECTORY,
)


def verify_installation(destination: Path) -> dict:
    executable = destination / "bin/python3.12"
    if hashlib.sha256(executable.read_bytes()).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError("runtime_executable_checksum_mismatch")
    # Verify the actual linked SQLite before allowing dependency setup/validation.
    result = subprocess.run(
        [
            str(executable),
            "-I",
            "-B",
            "-c",
            f"import sys,json; sys.path.insert(0,{str(ROOT / 'src')!r}); "
            "from agentic_payments_lab.runtime_guard import require_pilot_runtime; "
            "print(json.dumps(require_pilot_runtime()))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def install(archive: Path, destination: Path) -> dict:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("runtime_archive_checksum_mismatch")
    if destination.exists():
        return verify_installation(destination)  # Never overwrite an existing installation.
    with tempfile.TemporaryDirectory(prefix="extract-", dir=destination.parent) as temporary:
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(temporary, filter="data")
        extracted = Path(temporary) / "python"
        verify_installation(extracted)
        if destination.exists():
            raise FileExistsError("runtime_destination_appeared")
        extracted.rename(destination)
    return verify_installation(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already downloaded pinned archive")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise SystemExit("This reviewed runtime is limited to Linux x86_64")
    directory = ROOT / ".runtime"
    directory.mkdir(exist_ok=True)
    destination = directory / RUNTIME_DIRECTORY
    if destination.exists():
        result = verify_installation(destination)
    elif args.archive:
        result = install(args.archive, destination)
    else:
        # Installation is explicit and precedes network-isolated runtime validation.
        with tempfile.TemporaryDirectory(prefix="download-", dir=directory) as temporary:
            archive = Path(temporary) / "python.tar.gz"
            with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response:
                archive.write_bytes(response.read())
            result = install(archive, destination)
    print(json.dumps({"archive_url": ARCHIVE_URL, "archive_sha256": ARCHIVE_SHA256, **result}))


if __name__ == "__main__":
    main()
