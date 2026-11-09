"""Explicit host-only simulator activation. No import-time/key-file/CLI activation."""

import asyncio
import os
from pathlib import Path

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import SecretStr

from .call_budget import BudgetDenied
from .contracts import Settings
from .pilot_envelope import CEILING, open_scenario
from .pilot_transport import BASE_URL, GuardedLunaModel, PilotTransport, network_transport
from .runner import CLEANUP_TIMEOUT, disable_export, run_buyer


async def run_live_scenario(root, scenario, *, api_key: SecretStr, approved_enrollment_hash: str):
    """One explicit invocation after independent review and bounded human approval.

    The host supplies the exact preflight enrollment hash and ephemeral key. This
    function never discovers credentials, obtains approval, resumes or runs a batch.
    The hash is a binding check, not cryptographic proof of human permission.
    """
    envelope, runtime, metadata, session, goal = open_scenario(Path(root), scenario)
    preflight = envelope.report()
    if approved_enrollment_hash != preflight["enrollment_hash"]:
        raise BudgetDenied("pilot_approval_binding")
    if envelope.limits.estimate_ceiling_nano_usd > CEILING:
        raise BudgetDenied("pilot_ceiling")
    if type(api_key) is not SecretStr:
        raise BudgetDenied("pilot_ephemeral_key_required")
    key = api_key.get_secret_value()
    if not 20 <= len(key) <= 512 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in key
    ):
        raise BudgetDenied("pilot_key_format")
    # The pinned SDK reads this ambient header string even with an explicit key.
    # Reject by name, without inspecting its value or changing process configuration.
    if "OPENAI_CUSTOM_HEADERS" in os.environ:
        raise BudgetDenied("pilot_ambient_headers_rejected")
    limits = envelope.limits
    settings = Settings(
        mode="live",
        model="gpt-6-luna",
        reasoning_effort="none",
        service_tier="default",
        max_turns=limits.requests_per_invocation,
        max_output_tokens=limits.max_output_tokens,
        tool_limit=limits.tool_attempts,
        timeout_seconds=limits.timeout_seconds,
    )
    disable_export()
    envelope.begin_invocation(scenario)
    client = None
    wire = None
    outcome = "stopped"
    cancelled = False
    finished = False
    try:
        wire = PilotTransport(envelope, scenario, api_key, network_transport())
        client = AsyncOpenAI(
            api_key="pilot-wire-auth-is-transient",
            admin_api_key="",
            organization="",
            project="",
            webhook_secret="",
            base_url=BASE_URL,
            max_retries=0,
            timeout=limits.timeout_seconds,
            http_client=DefaultAsyncHttpxClient(
                transport=wire,
                trust_env=False,
                follow_redirects=False,
                timeout=limits.timeout_seconds,
            ),
        )
        model = GuardedLunaModel(client, session, goal, settings)
        report = await run_buyer(session, metadata, goal, settings, model=model)
        statistics = runtime.payments.statistics()
        counts = {
            k: statistics[k] for k in ("execution_attempts", "logical_charges", "charged_atomic")
        }
        try:
            purchase = runtime.buyer("pilot-purchase").status()
        except KeyError:
            purchase = None
        scenario_passed = (
            counts == {"execution_attempts": 1, "logical_charges": 1, "charged_atomic": 10_000}
            and purchase is not None
            and purchase.payment_state == "settled"
            and purchase.delivery_state == "delivered"
            and (
                scenario != "refusal"
                or (
                    report.decision == "no_suitable_service"
                    and report.purchase.payment_state == "not_started"
                )
            )
        )
        if (
            report.termination == "completed"
            and report.run_complete
            and report.metadata_persisted
            and scenario_passed
        ):
            outcome = "completed"
        if report.termination == "timeout":
            outcome = "timeout"
    except asyncio.CancelledError:
        cancelled = True
        outcome = "cancelled"
        raise asyncio.CancelledError("pilot_invocation_cancelled") from None
    except Exception:
        raise BudgetDenied("pilot_invocation_stopped") from None
    finally:
        if cancelled:
            try:
                envelope.finish_invocation(scenario, outcome)
                finished = True
            except Exception:
                pass
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                if client is not None:
                    await client.close()
                elif wire is not None:
                    await wire.aclose()
        except asyncio.CancelledError:
            cancelled = True
            outcome = "cancelled"
            raise asyncio.CancelledError("pilot_cleanup_cancelled") from None
        except Exception:
            outcome = "stopped"
        finally:
            if not finished:
                try:
                    envelope.finish_invocation(scenario, outcome)
                except Exception:
                    if not cancelled:
                        raise BudgetDenied("pilot_cleanup_incomplete") from None
    return {
        "mode": "guarded_live_simulator",
        "scenario": scenario,
        "agent": report.model_dump(mode="json"),
        "pilot": envelope.report(),
        "scenario_passed": scenario_passed and outcome == "completed",
        "payment_authority": counts,
    }
