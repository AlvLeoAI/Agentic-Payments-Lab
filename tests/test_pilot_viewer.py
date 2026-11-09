import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from agentic_payments_lab.pilot_viewer import snapshot
from agentic_payments_lab.pilot_viewer.launch import CONFIG, isolated_settings


def raw():
    return json.loads(snapshot.SNAPSHOT.read_bytes())


def test_historical_values_and_cumulative_charge_deltas():
    data = snapshot.load_snapshot()
    assert [len(s.requests) for s in data.scenarios] == [5, 2, 3]
    assert [len(s.tool_trace) for s in data.scenarios] == [4, 1, 2]
    assert [s.usage.input_tokens for s in data.scenarios] == [3557, 1133, 1734]
    assert [s.usage.output_tokens for s in data.scenarios] == [154, 45, 61]
    assert [s.estimated_cost_nano_usd for s in data.scenarios] == [432700, 135800, 203900]
    assert data.fresh_usage.input_tokens == 6424 and data.fresh_usage.output_tokens == 260
    assert data.fresh_estimated_cost_nano_usd == 772400
    assert data.scenarios[0].purchase_alias == data.scenarios[1].purchase_alias == "purchase-1"
    assert data.scenarios[2].purchase_alias is None
    assert [s.authority_after.charges for s in data.scenarios] == [1, 1, 1]
    assert [data.charge_delta(i) for i in range(3)] == [1, 0, 0]
    assert data.original_stopped_attempt.accepted_estimate_nano_usd is None
    assert data.original_stopped_attempt.usage_complete is False
    assert data.original_stopped_attempt.unresolved_reservation_nano_usd == 1239000
    assert snapshot.usd(772400) == "$0.0007724" and snapshot.usd(1239000) == "$0.001239"
    for scenario in data.scenarios:
        for request in scenario.requests:
            a = request.cache_allocation
            assert a.details_kind == "absent"
            assert a.observed_ttl_30m_tokens is a.observed_ttl_12h_tokens is None
            assert a.derived_ttl_30m_tokens == a.derived_ttl_12h_tokens == 0


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("schema_version",), "1"),
        (("unexpected",), "VIEWER_SECRET_SENTINEL_DO_NOT_DISPLAY"),
        (("model",), "<script>invalid</script>"),
        (("billing_verified",), True),
        (("fresh_usage", "input_tokens"), "6424"),
        (("fresh_usage", "total_tokens"), 0),
        (("fresh_estimated_cost_nano_usd",), 0),
        (("fresh_unresolved_reservation_nano_usd",), -1),
        (("original_stopped_attempt", "usage_complete"), True),
        (("original_stopped_attempt", "accepted_estimate_nano_usd"), 0),
        (("original_stopped_attempt", "unresolved_reservation_nano_usd"), 0),
        (("scenarios", 1, "purchase_alias"), None),
        (("scenarios", 1, "authority_after", "charges"), 2),
        (("scenarios", 2, "payment_state"), "settled"),
        (("scenarios", 2, "grant_spent_atomic"), 10000),
        (("scenarios", 0, "usage", "input_tokens"), 0),
        (("scenarios", 0, "requests", 0, "usage", "input_tokens"), False),
        (("scenarios", 0, "requests", 0, "usage", "output_tokens"), 513),
        (("scenarios", 0, "requests", 0, "usage", "secret"), "sentinel"),
        (("scenarios", 0, "requests", 0, "cache_allocation", "observed_ttl_30m_tokens"), 0),
        (("scenarios", 0, "requests", 0, "cache_allocation", "derived_ttl_12h_tokens"), 1),
        (("scenarios", 0, "requests", 0, "estimated_cost_nano_usd"), 0),
        (("scenarios", 0, "tool_trace", 0, "tool"), "reconcile"),
        (("scenarios", 0, "tool_trace", 0, "attempt"), 2),
        (("scenarios", 0, "tool_trace", 0, "outcome"), "secret"),
    ],
)
def test_invalid_snapshot_fails_without_echoing_values(path, value):
    data = raw()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(snapshot.SnapshotUnavailable) as caught:
        snapshot.decode_snapshot(json.dumps(data).encode())
    assert str(caught.value) == "Saved pilot data is unavailable or invalid."


@pytest.mark.parametrize("case", ["size", "duplicate", "nan", "depth", "sequence", "collection"])
def test_parser_and_collection_bounds(case):
    data = raw()
    if case == "size":
        encoded = b" " * (snapshot.MAX_BYTES + 1)
    elif case == "duplicate":
        encoded = b'{"schema_version":1,"schema_version":1}'
    elif case == "nan":
        encoded = b'{"schema_version":NaN}'
    elif case == "depth":
        encoded = b"[" * 2000 + b"]" * 2000
    else:
        if case == "sequence":
            data["scenarios"].reverse()
        else:
            data["scenarios"][0]["requests"] *= 2
        encoded = json.dumps(data).encode()
    with pytest.raises(snapshot.SnapshotUnavailable):
        snapshot.decode_snapshot(encoded)


def test_bounded_file_read_and_missing_file(tmp_path, monkeypatch):
    file = tmp_path / "oversized.json"
    file.write_bytes(b"x" * (snapshot.MAX_BYTES + 1))
    monkeypatch.setattr(snapshot, "SNAPSHOT", file)
    with pytest.raises(snapshot.SnapshotUnavailable):
        snapshot.load_snapshot()
    file.unlink()
    with pytest.raises(snapshot.SnapshotUnavailable):
        snapshot.load_snapshot()


@pytest.mark.parametrize("invalid", [False, True])
def test_app_reruns_in_isolated_config_without_execution(tmp_path, monkeypatch, invalid):
    marker = "VIEWER_SECRET_SENTINEL_DO_NOT_DISPLAY"
    fake = tmp_path / "untrusted-project"
    profile = tmp_path / "untrusted-profile"
    entrypoint = tmp_path / "original-script"
    markers = []
    for location, label in ((fake, "PROJECT"), (profile, "GLOBAL"), (entrypoint, "SCRIPT")):
        location.mkdir()
        sentinel = marker + "_" + label
        markers.append(sentinel)
        (location / ".env").write_text("OPENAI_API_KEY=" + sentinel)
        (location / ".streamlit").mkdir()
        (location / ".streamlit/secrets.toml").write_text(f'key="{sentinel}"')
        (location / ".streamlit/config.toml").write_text(
            '[server]\naddress="0.0.0.0"\n[browser]\ngatherUsageStats=true\n'
            f'[theme]\nfont="{sentinel}"\n'
        )
    monkeypatch.chdir(fake)
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("OPENAI_API_KEY", marker)
    monkeypatch.setenv("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    worker = entrypoint / "worker.py"
    worker.write_bytes(Path(__file__).with_name("viewer_worker.py").read_bytes())
    before = hashlib.sha256(snapshot.SNAPSHOT.read_bytes()).hexdigest()
    with isolated_settings() as (root, app, env, launch_command):
        assert env["HOME"] != str(profile)
        assert app.parent == root and str(app) in launch_command
        assert not any(k.startswith(("OPENAI_", "STREAMLIT_")) for k in env)
        command = [sys.executable, "-I", "-B", str(worker), str(app)]
        if invalid:
            command.append("invalid")
        result = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=45
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert all(value not in result.stdout + result.stderr for value in [marker, *markers])
        output = json.loads(result.stdout)
    assert os.environ.get("HOME") == str(profile)
    assert not root.exists()
    assert hashlib.sha256(snapshot.SNAPSHOT.read_bytes()).hexdigest() == before
    assert output["reruns"] == 3 and output["config_isolated"]
    rendered = output["rendered"]
    if invalid:
        assert rendered["error"] and not rendered["metrics"] and not rendered["tabs"]
        assert not rendered["headers"] and not rendered["markdown"]
    else:
        assert not rendered["error"]
        assert rendered["headers"] == [
            "3 scenarios passed · 1 simulated charge · 10 model requests"
        ]
        assert any(
            text.startswith("Historical results · Live model · Simulated payments")
            for text in rendered["captions"]
        )
        assert rendered["tabs"] == ["Purchase", "Replay", "Refusal"]
        assert rendered["metrics"][:2] == [
            ["Fresh estimated API cost", "$0.0007724"],
            ["Original unresolved reservation", "$0.001239"],
        ]
        assert rendered["subheaders"] == [
            "Simulated payment: settled",
            "Simulated payment: settled",
            "Simulated payment: not started",
        ]
        assert rendered["markdown"][:6] == [
            "**Purchase**",
            "Simulated payment: **settled**",
            "**Replay**",
            "Simulated payment: **settled**",
            "**Refusal**",
            "Simulated payment: **not started**",
        ]
        assert "**Fresh run totals**" in rendered["markdown"]
        assert "10 requests · 6,424 input + 260 output = 6,684 tokens." in rendered["text"]
        assert all(not expanded for _, expanded in rendered["expanders"])
        for index, detail in enumerate(rendered["tab_details"]):
            assert detail["expanders"] == ["Payment & tool evidence", "Request usage evidence"]
            expected_state = "not_started" if index == 2 else "settled"
            assert "Payment state: " + expected_state in detail["payment_evidence"]
            assert any("get_purchase_status" in text for text in detail["payment_evidence"])
        assert sum("New charges: 0" in text for text in rendered["captions"]) == 2
        for value in ("$0.0004327", "$0.0001358", "$0.0002039", "3,557", "1,133", "1,734"):
            assert value in [v for _, v in rendered["metrics"]]
        assert sum(t.startswith("Purchase alias: purchase-1") for t in rendered["text"]) == 2
        assert sum("unknown (null) / unknown (null)" in t for t in rendered["text"]) == 3
        assert sum("New simulated charges in this scenario: 0" in t for t in rendered["text"]) == 2


def test_native_theme_is_readable_without_remote_style_resources():
    theme = tomllib.loads(CONFIG)["theme"]

    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    def contrast(foreground, background):
        light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    assert theme["base"] == "dark"
    assert theme["font"] == "sans-serif" and "fontFaces" not in theme
    assert "http" not in json.dumps(theme)
    assert theme["baseFontSize"] == 16
    for background in (theme["backgroundColor"], theme["secondaryBackgroundColor"]):
        for foreground in (theme["textColor"], theme["primaryColor"], theme["linkColor"]):
            assert contrast(foreground, background) >= 4.5
        assert contrast(theme["borderColor"], background) >= 3


@pytest.mark.parametrize("port", [True, 0, 1023, 65536, "8501"])
def test_launcher_rejects_invalid_port(port):
    with pytest.raises(ValueError), isolated_settings(port):
        pytest.fail("invalid launch")
