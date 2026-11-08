import hashlib
import importlib.util
from pathlib import Path

import pytest

from agentic_payments_lab import runtime_guard


def test_selected_runtime_has_verified_static_sqlite_and_fix_provenance():
    result = runtime_guard.require_pilot_runtime()
    assert result["sqlite_source_id"] == runtime_guard.SQLITE_SOURCE_ID
    assert result["sqlite_version"] == "3.53.1"
    assert result["sqlite_linkage"] == "static_in_verified_executable"
    assert result["wal_reset_fix"]
    assert (
        hashlib.sha256(Path(result["executable"]).read_bytes()).hexdigest()
        == runtime_guard.EXECUTABLE_SHA256
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("python_version", [3, 12, 3]),
        ("sqlite_version", "3.45.1"),
        ("sqlite_source_id", "unverified-backport"),
        ("executable_sha256", "0" * 64),
        ("sqlite_module_origin", "/usr/lib/unverified-sqlite.so"),
    ],
)
def test_unverified_runtime_cannot_be_substituted(monkeypatch, field, value):
    evidence = runtime_guard.runtime_evidence()
    evidence[field] = value
    monkeypatch.setattr(runtime_guard, "runtime_evidence", lambda: evidence)
    with pytest.raises(runtime_guard.RuntimeUnverified, match="runtime_unverified"):
        runtime_guard.require_pilot_runtime()


def test_bad_archive_is_rejected_before_extraction_or_execution(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_runtime.py"
    spec = importlib.util.spec_from_file_location("prepare_runtime", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = tmp_path / "forged.tar.gz"
    archive.write_bytes(b"untrusted-not-executable")
    destination = tmp_path / "runtime"
    with pytest.raises(ValueError, match="archive_checksum"):
        module.install(archive, destination)
    assert not destination.exists()
    destination.mkdir()
    (destination / "marker").write_text("preserve existing installation")
    with pytest.raises(ValueError, match="archive_checksum"):
        module.install(archive, destination)
    assert (destination / "marker").read_text() == "preserve existing installation"
