"""The catalog describes existing coverage; it must never select executable behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agentic_payments_lab.buyer_agent.eval_catalog import (
    ERROR_MESSAGE,
    MAX_CATALOG_BYTES,
    MAX_RECORD_BYTES,
    CatalogError,
    case_json_schema,
    decode_catalog,
    load_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "evals/cases/offline-v1.jsonl"
SCHEMA = ROOT / "evals/schemas/case-v1.schema.json"
IDS = (
    "new_unknown",
    "replay_unknown",
    "operator_delivered",
    "incident_visible",
    "wire_paid",
    "wire_replay",
    "wire_missing_usage",
)


def records():
    return [json.loads(line) for line in CATALOG.read_bytes().splitlines()]


def encoded(rows):
    return b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"


def reject(data):
    with pytest.raises(CatalogError) as caught:
        decode_catalog(data)
    assert str(caught.value) == ERROR_MESSAGE
    assert caught.value.__suppress_context__


def test_existing_catalog_and_generated_record_schema():
    raw = CATALOG.read_bytes()
    data = load_catalog(CATALOG)
    assert tuple(case.case_id for case in data) == IDS
    assert [case.sequence_order for case in data] == [1, 2, 3, 4, 1, 2, 3]
    assert [case.sequence_group for case in data] == ["x402_lifecycle"] * 4 + [
        "simulator_wire_lifecycle"
    ] * 3
    assert data == decode_catalog(raw.rstrip(b"\n"))
    assert data == decode_catalog(raw.replace(b"\n", b"\r\n"))
    assert isinstance(data, tuple) and isinstance(data[0].expected_checks, tuple)
    with pytest.raises(ValidationError):
        data[0].version = 2
    schema = json.loads(SCHEMA.read_bytes())
    assert schema == case_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for row in records():
        validator.validate(row)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["expected_checks"]["uniqueItems"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", "1"),
        ("version", True),
        ("case_id", "../run.py"),
        ("suite", "live_openai"),
        ("sequence_group", "independent_purchase"),
        ("sequence_order", 0),
        ("sequence_order", 5),
        ("sequence_order", "1"),
        ("sequence_order", True),
        ("description", ""),
        ("description", 123),
        ("description", "x" * 201),
        ("description", "hidden\ncontrol"),
        ("fixture", "os:system"),
        ("expected_checks", []),
        ("expected_checks", ["unknown_check"]),
        ("expected_checks", "actual_tool_trace"),
        ("expected_checks", None),
        ("module", "os"),
        ("path", "https://untrusted.invalid/fixture"),
    ],
)
def test_structural_rejection_in_both_validators(field, value):
    rows = records()
    rows[0][field] = value
    assert not Draft202012Validator(case_json_schema()).is_valid(rows[0])
    reject(encoded(rows))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_field",
        "wrong_suite",
        "wrong_group",
        "wrong_order",
        "wrong_fixture",
        "missing_check",
        "other_suite_check",
        "duplicate_check",
        "blank_description",
        "padded_description",
        "float_version",
        "float_order",
    ],
)
def test_python_record_semantics_and_strict_numbers(mutation):
    rows = records()
    row = rows[0]
    if mutation == "missing_field":
        del row["description"]
    elif mutation == "wrong_suite":
        row["suite"] = "responses_mock_over_simulator"
    elif mutation == "wrong_group":
        row["sequence_group"] = "simulator_wire_lifecycle"
    elif mutation == "wrong_order":
        row["sequence_order"] = 2
    elif mutation == "wrong_fixture":
        row["fixture"] = "scripted_responses_mock_simulator"
    elif mutation == "missing_check":
        row["expected_checks"].pop()
    elif mutation == "other_suite_check":
        row["expected_checks"][0] = "host_termination"
    elif mutation == "duplicate_check":
        row["expected_checks"][-1] = row["expected_checks"][0]
    elif mutation == "blank_description":
        row["description"] = " "
    elif mutation == "padded_description":
        row["description"] = " padded "
    elif mutation == "float_version":
        row["version"] = 1.0
    elif mutation == "float_order":
        row["sequence_order"] = 1.0
    # Cross-field bindings and strict numeric lexemes are deliberately stronger than JSON Schema.
    structural = Draft202012Validator(case_json_schema()).is_valid(row)
    assert structural == (mutation not in {"missing_field", "duplicate_check"})
    reject(encoded(rows))


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "duplicate", "swapped", "groups", "mixed"]
)
def test_whole_catalog_requires_both_complete_ordered_sequences(mutation):
    rows = records()
    if mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(rows[0])
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "swapped":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "groups":
        rows = rows[4:] + rows[:4]
    elif mutation == "mixed":
        rows[1], rows[4] = rows[4], rows[1]
    assert all(Draft202012Validator(case_json_schema()).is_valid(row) for row in rows)
    reject(encoded(rows))


def test_check_identifier_order_is_not_execution_order():
    rows = records()
    rows[0]["expected_checks"].reverse()
    assert set(decode_catalog(encoded(rows))[0].expected_checks) == set(
        load_catalog(CATALOG)[0].expected_checks
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "blank",
        "extra_blank",
        "broken_json",
        "duplicate_key",
        "nested_duplicate",
        "nan",
        "infinity",
        "depth",
        "not_object",
        "invalid_utf8",
        "bom",
        "oversized_record",
        "oversized_file",
    ],
)
def test_parser_and_resource_boundaries(mutation):
    lines = CATALOG.read_bytes().splitlines()
    if mutation == "empty":
        data = b""
    elif mutation == "extra_blank":
        data = CATALOG.read_bytes() + b"\n"
    elif mutation == "oversized_file":
        data = b"x" * (MAX_CATALOG_BYTES + 1)
    else:
        replacement = {
            "blank": b" ",
            "broken_json": b"{",
            "duplicate_key": lines[0].replace(b'"version":1', b'"version":1,"version":1'),
            "nested_duplicate": b'{"extra":{"secret":1,"secret":2}}',
            "nan": lines[0].replace(b'"version":1', b'"version":NaN'),
            "infinity": lines[0].replace(b'"version":1', b'"version":Infinity'),
            "depth": b"[" * 1500 + b"]" * 1500,
            "not_object": b"[]",
            "invalid_utf8": b"\xff",
            "bom": b"\xef\xbb\xbf" + lines[0],
            "oversized_record": lines[0] + b" " * MAX_RECORD_BYTES,
        }[mutation]
        lines[0] = replacement
        data = b"\n".join(lines)
    reject(data)


@pytest.mark.parametrize("value", [None, "{}", bytearray(b"{}"), memoryview(b"{}")])
def test_decode_requires_bytes(value):
    reject(value)


def test_file_reads_are_bounded_and_errors_do_not_echo_source(tmp_path, monkeypatch):
    path = tmp_path / "PRIVATE_PATH_SENTINEL.jsonl"
    path.write_bytes(b"x" * (MAX_CATALOG_BYTES + 1000))
    original = Path.open
    reads = []

    class WatchRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def read(self, size=-1):
            value = self.handle.read(size)
            reads.append((size, len(value)))
            return value

    monkeypatch.setattr(Path, "open", lambda self, *a, **kw: WatchRead(original(self, *a, **kw)))
    with pytest.raises(CatalogError, match="^" + ERROR_MESSAGE.replace(".", r"\.") + "$"):
        load_catalog(path)
    assert reads == [(MAX_CATALOG_BYTES + 1, MAX_CATALOG_BYTES + 1)]
    for target in (tmp_path, tmp_path / "MISSING_PRIVATE_PATH"):
        with pytest.raises(CatalogError) as caught:
            load_catalog(target)
        assert str(caught.value) == ERROR_MESSAGE
    rows = records()
    rows[0]["secret"] = "PRIVATE_VALUE_SENTINEL"
    reject(encoded(rows))


def test_nul_path_uses_fixed_error_without_private_path():
    with pytest.raises(CatalogError) as caught:
        load_catalog("PRIVATE_PATH_SENTINEL\x00catalog.jsonl")
    assert str(caught.value) == ERROR_MESSAGE


def test_import_and_catalog_loading_cannot_dispatch_evaluation(tmp_path):
    code = """
import importlib.abc, sys
from pathlib import Path
class BlockExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        forbidden = ('agents', 'openai', 'httpx', 'httpx2', 'sqlite3',
            'agentic_payments_lab.runtime', 'agentic_payments_lab.x402_adapter',
            'agentic_payments_lab.buyer_agent.evaluation',
            'agentic_payments_lab.buyer_agent.wire_evaluation',
            'agentic_payments_lab.buyer_agent.runner')
        if any(fullname == x or fullname.startswith(x + '.') for x in forbidden):
            raise AssertionError('Catalog imported execution code')
sys.meta_path.insert(0, BlockExecution())
from agentic_payments_lab.buyer_agent.eval_catalog import load_catalog
assert len(load_catalog(Path(sys.argv[1]))) == 7
print('catalog-only import/load passed')
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI_", "AZURE_OPENAI_"))}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(CATALOG)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip() == "catalog-only import/load passed"
    assert not list(tmp_path.iterdir())
