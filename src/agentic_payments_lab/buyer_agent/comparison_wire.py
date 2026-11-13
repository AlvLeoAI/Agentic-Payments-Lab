"""Transient provider authentication AFTER a durable, single-send admission.

Imports construct no clients, inspect no keys and enable no transport. Only an
explicit live host supplies SecretStr and an enrolled authority. Tests inject
in-memory wires; ordinary comparison paths still construct MockTransport only.
"""

import asyncio
import functools
import json
import logging
import os
import threading

import httpx2
from pydantic import SecretStr

from ..domain import canonical
from .call_budget import BudgetDenied
from .comparison_protocol import COUNT_ENDPOINTS, ENDPOINTS, MODELS
from .runner import disable_export

MODEL_ENDPOINTS = {
    provider: endpoint.split("/v1/")[0] + "/v1/models/" + MODELS[provider]
    for provider, endpoint in ENDPOINTS.items()
}
ANTHROPIC_VERSION = "2023-06-01"
_logging_lock = threading.Lock()
_quiet_users = 0
_previous_disable = 0


def quiet_wire(function):
    """No standard logging while transient authentication/remote data is handled."""

    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        global _quiet_users, _previous_disable
        with _logging_lock:
            if not _quiet_users:
                _previous_disable = logging.root.manager.disable
                logging.disable(max(logging.CRITICAL, _previous_disable))
            _quiet_users += 1
        try:
            return await function(*args, **kwargs)
        finally:
            with _logging_lock:
                _quiet_users -= 1
                if not _quiet_users:
                    logging.disable(_previous_disable)

    return wrapped


def validate_key(key):
    if type(key) is not SecretStr:
        raise BudgetDenied("comparison_ephemeral_key_required")
    value = key.get_secret_value()
    if not 20 <= len(value) <= 512 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value
    ):
        raise BudgetDenied("comparison_key_format")


def reject_ambient_headers():
    # Check presence only. Never read or forward the ambient header value.
    if "OPENAI_CUSTOM_HEADERS" in os.environ:
        raise BudgetDenied("comparison_ambient_headers_rejected")


def network_transport():
    return httpx2.AsyncHTTPTransport(
        verify=True,
        trust_env=False,
        proxy=None,
        retries=0,
        http1=True,
        http2=False,
        limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1),
    )


class ProviderWire(httpx2.AsyncBaseTransport):
    """Exact first-party routes; authority independently binds method/body/owner.

    SDK request headers are discarded. The real key never enters the SDK client,
    its exceptions, a response handed back to it, or durable evidence. This does
    not promise erasure from Python memory or safety against a malicious host.
    """

    def __init__(self, provider, authority, key, wire):
        if provider not in MODELS:
            raise BudgetDenied("comparison_provider_rejected")
        if (
            authority.policy.get("mode") == "http_mock_only"
            and type(wire) is not httpx2.MockTransport
        ):
            raise BudgetDenied("preflight_mock_transport_required")
        validate_key(key)
        reject_ambient_headers()
        disable_export()
        self.provider, self.authority = provider, authority
        self._key, self._wire = key, wire
        self.claim = None

    def check_payload(self, raw, payload):
        secret = self._key.get_secret_value()
        if secret.encode() in raw or (payload is not None and secret in canonical(payload)):
            raise BudgetDenied("comparison_credential_reflection")

    def check_decoded(self, *records):
        """Exact key in any string, after decoding JSON that a string itself carries.

        check_payload sees the raw bytes and one decode. Final-output text and
        OpenAI arguments are decoded again, where an escape can spell the key.
        Other encodings (base64, URL escapes) are not decoded anywhere and stay
        outside this check.
        """
        secret = self._key.get_secret_value()
        pending = list(records)
        while pending:
            value = pending.pop()
            if type(value) is dict:
                pending.extend(value)
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
            elif type(value) is str:
                if secret in value:
                    raise BudgetDenied("comparison_credential_reflection")
                try:
                    pending.append(json.loads(value))  # Strictly shorter: terminates.
                except ValueError:
                    pass  # Not JSON, so no later decode can reveal more.
                except RecursionError:
                    # Possibly JSON, nested too deeply to decode here. Whatever
                    # parse_response does decode is in the evidence record,
                    # which is checked as well.
                    pass

    @quiet_wire
    async def handle_async_request(self, request):
        reject_ambient_headers()
        provider = self.provider
        allowed = {
            ("GET", MODEL_ENDPOINTS[provider]),
            ("POST", COUNT_ENDPOINTS[provider]),
            ("POST", ENDPOINTS[provider]),
        }
        if (request.method, str(request.url)) not in allowed:
            raise BudgetDenied("comparison_endpoint_rejected")
        if set(request.extensions) - {"timeout"}:
            raise BudgetDenied("comparison_extensions_rejected")
        body = await request.aread()
        if len(body) > 32768:
            raise BudgetDenied("comparison_serialized_request_limit")
        self.check_payload(body, None)
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError("comparison_wire_cancelled") from None
        # Exclusive permission commits before authentication is constructed or sent.
        self.claim = self.authority.claim_wire(provider, request)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "payments-lab/paired-simulator",
        }
        auth_header = "Authorization" if provider == "openai" else "x-api-key"
        headers[auth_header] = (
            "Bearer " if provider == "openai" else ""
        ) + self._key.get_secret_value()
        if provider == "anthropic":
            headers["anthropic-version"] = ANTHROPIC_VERSION
        outbound = httpx2.Request(
            request.method,
            str(request.url),
            content=body,
            headers=headers,
            extensions={"timeout": dict.fromkeys(("connect", "read", "write", "pool"), 30)},
        )
        response = None
        try:
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            response = await self._wire.handle_async_request(outbound)
            self.authority.wire_status(self.claim, response.status_code)
            return response  # Caller consumes bounded raw stream, records evidence, then closes.
        except asyncio.CancelledError:
            self._failed("cancelled")
            if response is not None:
                try:
                    async with asyncio.timeout(0.5):
                        await response.aclose()
                except BaseException:
                    pass
            raise asyncio.CancelledError("comparison_wire_cancelled") from None
        except Exception:
            self._failed("transport_failed")
            if response is not None:
                try:
                    async with asyncio.timeout(0.5):
                        await response.aclose()
                except BaseException:
                    pass  # Durable claim remains; never retry after failed cleanup.
            raise BudgetDenied("comparison_wire_failed") from None
        finally:
            headers[auth_header] = "[redacted]"
            outbound.headers[auth_header] = "[redacted]"

    def _failed(self, code):
        try:
            self.authority.wire_failure(self.claim, code)
        except Exception:
            pass  # Pending owner/claim persists even if cleanup cannot write.

    @quiet_wire
    async def aclose(self):
        try:
            async with asyncio.timeout(0.5):
                await self._wire.aclose()
        except asyncio.CancelledError:
            self._failed("cancelled")
            raise asyncio.CancelledError("comparison_wire_close_cancelled") from None
        except Exception:
            self._failed("close_failed")
            raise BudgetDenied("comparison_wire_close_failed") from None
        finally:
            self._key = None
