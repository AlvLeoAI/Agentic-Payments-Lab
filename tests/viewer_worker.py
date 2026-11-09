"""AppTest in the same clean configuration context as the supported launcher."""

import hashlib
import importlib.abc
import json
import os
import sqlite3
import sys
from pathlib import Path

MARKER = "VIEWER_SECRET_SENTINEL_DO_NOT_DISPLAY"
FORBIDDEN = (
    "agentic_payments_lab.runtime",
    "agentic_payments_lab.payments",
    "agentic_payments_lab.coordinator",
    "agentic_payments_lab.buyer_agent",
    "agentic_payments_lab.x402_adapter",
    "agentic_payments_lab.ledger",
)


class NoExecutionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN):
            raise AssertionError("Viewer imported execution code")


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = Path(os.fsdecode(args[0])).name
        if name.startswith(".env") or name == "secrets.toml" or ".sqlite" in name:
            raise AssertionError("Viewer attempted to read protected state")
        if name == "config.toml" and not Path(os.fsdecode(args[0])).resolve().is_relative_to(
            Path.cwd()
        ):
            raise AssertionError("Viewer attempted to read external configuration")


def forbid_sqlite(*args, **kwargs):
    raise AssertionError("Viewer attempted database access")


sys.meta_path.insert(0, NoExecutionImports())
sys.addaudithook(audit)
sqlite3.connect = forbid_sqlite

from streamlit import config  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from agentic_payments_lab.pilot_viewer import snapshot  # noqa: E402

before = hashlib.sha256(snapshot.SNAPSHOT.read_bytes()).hexdigest()
if len(sys.argv) > 2:
    bad = Path.cwd() / "invalid.json"
    bad.write_text(json.dumps({"schema_version": 1, "secret": MARKER}))
    snapshot.SNAPSHOT = bad

app = AppTest.from_file(sys.argv[1], default_timeout=15)
observations = []
for _ in range(3):
    app.run()
    assert not app.exception
    assert not any(
        len(app.get(kind))
        for kind in ("button", "file_uploader", "chat_input", "text_input", "download_button")
    )
    rendered = {
        "metrics": [[m.label, m.value] for m in app.metric],
        "text": [t.value for t in app.text],
        "captions": [t.value for t in app.caption],
        "info": [t.value for t in app.info],
        "warning": [t.value for t in app.warning],
        "error": [t.value for t in app.error],
        "headers": [t.value for t in app.header],
        "subheaders": [t.value for t in app.subheader],
        "markdown": [t.value for t in app.markdown],
        "expanders": [[e.label, e.proto.expanded] for e in app.expander],
        "tabs": [t.label for t in app.tabs],
        "tab_details": [
            {
                "label": tab.label,
                "subheaders": [t.value for t in tab.subheader],
                "expanders": [e.label for e in tab.expander],
                "payment_evidence": [t.value for t in tab.expander[0].text],
            }
            for tab in app.tabs
        ],
    }
    assert not app.get("html")
    assert all(not t.proto.allow_html for t in app.markdown)
    assert MARKER not in json.dumps(rendered)
    observations.append(rendered)
assert observations[0] == observations[1] == observations[2]
assert all(not any(n == f or n.startswith(f + ".") for f in FORBIDDEN) for n in sys.modules)
assert config.get_option("browser.gatherUsageStats") is False
assert config.get_option("server.address") == "127.0.0.1"
assert config.get_option("server.enableCORS") is True
assert config.get_option("server.enableXsrfProtection") is True
assert config.get_option("secrets.files") == []
assert config.get_option("theme.base") == "dark"
assert config.get_option("theme.primaryColor") == "#E8C468"
assert config.get_option("theme.backgroundColor") == "#17191E"
assert config.get_option("theme.secondaryBackgroundColor") == "#24272E"
assert config.get_option("theme.textColor") == "#F2F0E9"
root = Path.cwd()
assert all(Path(p).is_relative_to(root) for p in config.get_config_files("config.toml"))
if len(sys.argv) == 2:
    assert hashlib.sha256(snapshot.SNAPSHOT.read_bytes()).hexdigest() == before
print(json.dumps({"reruns": 3, "rendered": observations[0], "config_isolated": True}))
