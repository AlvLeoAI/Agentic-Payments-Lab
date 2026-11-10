"""Independent-process campaign failure probes, exclusively in OS-isolated tests."""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from campaign_support import responders

from agentic_payments_lab.buyer_agent.campaign import Campaign
from agentic_payments_lab.buyer_agent.campaign_runner import run_mock_campaign

root = Path(sys.argv[1])
mode = sys.argv[2]
campaign = Campaign(root / "campaign.sqlite3")


def activate():
    campaign.activate(
        approved_enrollment_hash=campaign.enrollment_hash, execution_mode="responses_mock"
    )


if mode == "compete":
    (root / ("ready-" + sys.argv[3])).touch()
    deadline = time.monotonic() + 20
    while not (root / "go").exists():
        if time.monotonic() >= deadline:
            raise SystemExit(2)
        time.sleep(0.01)
    try:
        activate()
        claimed = True
    except (RuntimeError, sqlite3.Error):
        claimed = False
    print(json.dumps({"claimed": claimed}))
elif mode in ("activation", "entry", "reservation"):
    activate()
    if mode != "activation":
        campaign.begin_invocation("trial-1.free")
    if mode == "reservation":
        campaign.reserve("trial-1.free", b"{}")
    os._exit(87)
else:
    original_observe = campaign.observe
    original_record = campaign.record_entry
    if mode == "usage":

        def observe(*args):
            original_observe(*args)
            os._exit(87)

        campaign.observe = observe
    if mode == "result":

        def record(*args, **kwargs):
            original_record(*args, **kwargs)
            os._exit(87)

        campaign.record_entry = record
    factory = responders([])

    def respond_for(entry):
        normal = factory(entry)
        ordinal = 0

        async def respond(req):
            nonlocal ordinal
            ordinal += 1
            if mode == "dispatch" or (
                mode == "post_payment" and entry["case"] == "paid" and ordinal == 5
            ):
                os._exit(87)
            return await normal(req)

        return respond

    assert mode in ("usage", "result", "dispatch", "post_payment")
    asyncio.run(run_mock_campaign(campaign, respond_for))
