# Phase 3B.2 — offline merchant integration

This implements the merchant portion of the [Phase 3 plan](phase3-plan.md) on
accepted Phase 3B.1 commit `934887352698b6438faca0548be7eda6ae078a96`.
It is an offline integration milestone, **not complete Phase 3B or live x402
acceptance**. No credentials, real signatures, wallets, OpenAI, RPC, facilitator
traffic or deployment are used. All new runtime tests use fresh disposable state.
The four buyer-agent tools, deterministic eligibility, simulator, CLI defaults,
buyer schema version 2 and explicit fixture reconciliation remain unchanged.

## Implemented boundary and flow

1. The trusted host explicitly initializes a fresh `OfflineGateStore`, creates its
   immutable task, and initializes a separate `MerchantStore`. Merchant schema
   version 1 belongs only to this new merchant file; it is not a buyer upgrade.
2. The host calls `merchant.store.enroll(MerchantOrder(context=context,
   request=request))` with the original buyer context and request. This is not
   exposed through HTTP or agent tools. Grant, task, purchase, request, exact URL,
   method, policy and requirement hashes are bound durably. Changed enrollment,
   simulator purchase identities and unknown HTTP orders are rejected.
3. `OfflineMerchantClient.challenge(purchase_id, request)` makes an unpaid POST
   through HTTPX ASGI to the approved `.invalid` HTTPS resource. The 402's
   `PAYMENT-REQUIRED` header requires the `payment-identifier` extension.
4. Compose `OfflineGate(buyer, signer_double, client, clock, identified=True)`.
   It strictly validates this profile, commits the original budget/ownership claim,
   delegates once to the injected marker signer, and commits dispatch permission.
   The existing default `identified=False` Phase 3B.1 profile remains closed and
   continues to reject extensions. Switching profile after a claim conflicts with
   its immutable requirements fingerprint; it does not grant a new attempt.
5. The gate supplies its persisted purchase ID to the ASGI dispatcher. The
   payment identifier is `pay_` plus a SHA-256 digest of that purchase ID, distinct
   from the EIP-3009 nonce. No random identifier is generated on retry. The signed
   marker header exists only in memory. The merchant checks raw JSON types, bounds,
   duplicate keys, all accepted terms, authorization fields and the exact extension
   before invoking a settlement double.
6. One `BEGIN IMMEDIATE` transaction commits an immutable settlement owner,
   request/payload fingerprint and unique `(network, asset, payer, nonce)` binding.
   Only its caller can invoke `SettlementDouble.settle` once. No SQLite transaction
   spans this await. A restart, pending/failure response, timeout, cancellation,
   missing observation or lost acknowledgement never authorizes another caller.
7. A typed `SettlementObservation` is recorded separately. Only `reported_success`
   permits caching the deterministic local resource. Publication, resource digest
   and cache identity commit together. HTTP 200, `PAYMENT-RESPONSE`, transaction
   identifiers and cached content remain unverified observations for the buyer.
   Its reservation stays held until explicit `apply_fixture` with conclusive
   offline test evidence; this merchant never invokes it.

The pinned `x402==2.27.0` SDK supplies v2 schemas/header codecs and the
payment-identifier declaration/append helpers. No dependency or extra was added.
Those helpers do not implement durable idempotency. There is no SDK auto-paying
HTTP wrapper, facilitator client, resource-server settlement retry or cryptographic
verification. The signature accepts only `offline-test-signature:` markers, which
are deliberately invalid EVM signatures.

EIP-3009 signs transfer fields, not a request body or purchase identity. Here the
host supplies the trusted enrollment and transport context; the first validated
paid request binds its public authorization tuple permanently. This does not
establish remote merchant authentication or cryptographic request authentication.
Local objects, the injected doubles and database administrators remain trusted.

## Ownership and persistence

| State | Authority | Persistence and retry rule |
| --- | --- | --- |
| Grant, reservation, purchase, signing and dispatch ownership | Existing buyer file | Unchanged Phase 3B.1 transactions/cancellation fence; no re-sign or automatic resend |
| Approved order and request | Merchant `orders` plus immutable profile | Explicit host enrollment; purchase/task uniqueness and immutable triggers |
| One settlement attempt and nonce binding | Merchant `settlements` | Owner, authorization, canonical payload hash and nonce tuple commit together; no deletion, reset, lease or takeover |
| Double's result | Merchant `settlements.observation` | Typed, bounded observation, written once by the owner; not chain proof |
| Cached resource and retention tombstone | Merchant `deliveries` | One immutable result/digest/expiry per payment ID; content may only become null |
| Buyer terminal accounting | Existing explicit offline fixture evidence | Merchant HTTP and its separate SQLite file cannot mark funds spent/released |
| Test instrumentation | Independent temporary authority-double database | Records invocation and synthetic transfer counts directly; these are not blockchain charges |

WAL and `synchronous=FULL` remain configured. Each operation uses its own connection;
reports use one connection and an explicit read transaction for a consistent order,
settlement and delivery snapshot. There are **no attached databases or cross-file
atomic commits**. Enrollment may commit in one file without the other. The host may
explicitly repeat identical enrollment after a setup interruption; it must not
replace an identity or infer a missing financial history from caller arguments.

Fresh initialization uses exclusive creation and one schema/profile/version
transaction. Reopen requires the exact schema and profile; missing, partial,
simulator, buyer, future-version or mismatched files fail closed. Unsupported schemas
are checked read-only before persistent journal changes, and checked again under
the write lock. An interrupted initialization is not repaired automatically. WAL
configuration happens after the schema commit; explicit reopen can finish it.
No retained operator database was migrated by this milestone.

## Retrieval, expiry and conservative recovery

`OfflineMerchantClient.retrieve(purchase_id)` issues an uncharged GET and never
recreates an authorization, settles or regenerates content. The ASGI client injects
an in-process capability checked by both routes; the naked FastAPI app refuses
access. This is a trusted host fixture boundary, not public HTTP authentication.
There is no public retrieval deployment or use of a purchase ID as a bearer secret.
Redirects and environment proxy use are disabled; the transport is constructed as
`httpx.ASGITransport` internally and cannot be replaced with a network transport.

Identical paid replays return the existing pending/terminal observation or cache,
even after the authorization expires. Changed request bodies, signatures, selected
terms, nonce or validity fields conflict or reject. A nonce already assigned to a
different purchase conflicts even if its cache has expired. No automatic paid replay
exists in the client or gate; HTTP replay tests deliberately act as duplicate callers.

Content retention is seven days from its first publication using the injected clock.
Retrieval at/after expiry returns 410 with `delivery_state=gone` and no content.
Explicit `store.expire_cache(now)` clears only expired content. Identity, original
content digest, observations, expiry and nonce tombstones remain indefinitely within
the retained fixture state. Expiry does not authorize regeneration or another charge.

If a reported success committed but resource publication did not, the host may
explicitly call `store.prepare_delivery(purchase_id, now)` to construct the same
deterministic local resource and cache it atomically. Repeated/concurrent calls have
one cache row. This operation has no signer/settler and never regenerates a gone
cache. Generic remote resource generation, refunds and background recovery are absent.

For an unknown/pending settlement, preserve all buyer/merchant files and WAL state,
stop affected execution, and inspect the original identity/nonce. Retrieval is allowed;
settlement takeover, signature reconstruction, replacement purchases and automatic
reservation release are not. No merchant observation-repair/chain-observer workflow is
provided here. A missing merchant file requires explicit operator investigation,
not initialization of an empty replacement for the old tasks. Restoring an older
complete database or deliberately reenrolling identities into a separate new instance
can defeat local deduplication; coordinated backup/restore recovery remains unproven.

## Crash and cancellation matrix

| Boundary | Durable result | Allowed next action |
| --- | --- | --- |
| Rejected input or failed claim commit | No merchant settlement authority granted | Inspect state; buyer dispatch may already be recorded, so no automatic resend |
| Claim committed; acknowledgement lost or exit before settle | Unknown with immutable owner/nonce | Read-only status; no takeover even if the double was never reached |
| Settle entered or synthetic transfer occurred; response lost | Unknown; buyer reservation held | Inspect independent evidence; no settlement retry |
| Observation commit fails | Existing claim remains unknown | Preserve state, inspect; no settlement retry |
| Reported success committed; cache missing | Reported success, delivery pending; buyer reserved | Explicit deterministic cache preparation and non-charging retrieval |
| Cache committed; response/acknowledgement lost | One retained cache | Retrieve existing result; do not settle or publish again |
| Cancellation wins before buyer claim/dispatch | Existing Phase 3B.1 durable cancellation fence | No merchant invocation from a late signer |
| Dispatch wins, then cancellation | May already have reached merchant; reservation held | No compensating cancel/release/resend |
| External cancellation during settle | Claim remains; completed observations may or may not be present | Propagate a fresh sanitized cancellation; inspect the same identity |
| Double swallows cancellation and returns late | Its typed observation can persist; no automatic resource publication | Propagate cancellation; explicit non-charging delivery recovery if success recorded |
| Cache expires | Identity/nonce and original cache tombstone retained | Gone response, no replacement authorization or charge |

Cancellation after cache commit does not undo delivery. Cancellation arguments,
exception messages, notes, raw bodies, headers and signatures are not persisted or
included in public errors. The tests scan temporary databases/WAL and captured
logs/reports using distinctive signature/header markers, including exception paths.
Public responses use fixed codes. Python tracebacks/debuggers can retain locals;
dropping references is not secure memory erasure. Cooperative cancellation cannot
undo dispatch, stop an uncooperative double or guarantee persistence under abrupt
exit/storage failure. Durable claims preserve uncertainty when cleanup is impossible.

## Validation and remaining scope

The tests exercise actual 402 / PAYMENT-SIGNATURE / PAYMENT-RESPONSE ASGI exchanges,
strict malformed/coercion cases, immutable enrollment, nonce reuse across purchases,
100 duplicate HTTP requests with the first settlement held behind an event barrier,
100 gate retries, competing budget reservations,
separate-process replays, eight abrupt-exit boundaries, failed commits/lost
acknowledgements, both cancellation/dispatch orderings, late results, cache snapshots,
expiry/tombstones and signature non-retention. Settlement invocation/transfer counts
come from the independent double's database, not response success or signing statistics.
The 100-request test records callback entry before suspension, requires the other
99 requests to return pending while the owner remains blocked, and verifies one
settlement/transfer and one cached delivery after releasing that owner.
The entire accepted Phase 1/2/3B.1 suite and repeated simulator/agent demos remain
part of the full validator.

Install dependencies before runtime isolation, then run the existing fail-closed
validator, which checks that subprocesses inherit the isolated network namespace:

```bash
uv sync --locked
parent_netns=$(readlink /proc/self/ns/net)
env -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_PROJECT_ID \
  PYTHONDONTWRITEBYTECODE=1 \
  unshare --user --map-root-user --net -- .venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

Failure to establish a namespace is a failure, not permission to run unrestricted.
Local validation does not establish hosted CI success; inspect the workflow for
the exact published commit separately.

The [SQLite WAL-reset prerequisite](phase3b1.md) remains
unresolved for the operator's local SQLite build. Passing tests or a different
hosted library do not establish its remediation. **Live use remains blocked.**
Real signer interoperability, facilitator integration, authoritative finalized chain
observation, agent/backend integration, public merchant security and older complete
database restoration remain future Phase 3B/3C work requiring separate authorization.
