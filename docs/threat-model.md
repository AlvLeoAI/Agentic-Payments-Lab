# Phase 1 threat model

## Scope and trusted boundary

Assets are the approved budget, durable authorization, purchase identity, delivered
resource, and auditable history. A future LLM, merchant metadata, quote, response,
and user-supplied subject may contain erroneous or malicious text. Such text is data;
it never modifies authorization rules.

Trusted components are the operator/host, approved catalog, domain/policy code,
coordinator, ledger, simulator authority, and local filesystem. Phase 1 is one local
operator and one simulation instance. An attacker with arbitrary Python execution,
filesystem writes, or administrator access is outside the enforced boundary. The
FastAPI app is deliberately reached only via in-process HTTPX ASGI transport. It
must not be exposed as a public HTTP service without a new security design.

## Threats, defenses, and remaining risk

| Threat | Implemented defense | Remaining limitation |
| --- | --- | --- |
| Prompt injection or invented justification | Typed request; exact grant purpose/service checks; no text-derived authority; fixed task binding; narrow Phase 2 tools | Scripted adversarial tests validate boundaries, not a real model's resistance |
| Model invents a new purchase ID or grant | Host registers task and computes ID; buyer has no task/grant management methods | Host tool registration must maintain this boundary; Python is not sandboxed |
| Malicious merchant, recipient, network, or token | Every quote term is compared with operator catalog before reservation and again before dispatch | Catalog governance is trusted; there is no remote merchant identity system |
| Price substitution / arithmetic errors | Fixed catalog price, strict bounded integer atomic units, six decimals, no floats | Real fees, exchange rates, slippage, and token contracts are out of scope |
| Modified or replayed quote | Stored canonical quote, full request/quote/grant fingerprint, quote TTL and request hash | Hashes are not signatures; merchant storage is part of the trusted local simulation |
| Duplicate purchase or timeout retry | One immutable purchase per task; unique authorization/purchase rows; only purchase creator dispatches | At-most-one local charge relies on retained consistent state; no distributed exactly-once claim |
| Concurrent budget violations | Atomic conditional reservation, writer serialization, SQL balance constraints | SQLite availability under heavy load is bounded; no multi-host deployment |
| Unauthorized adapter call | Exact intent must match durable dispatch approval; expiry and simulation identifiers checked again | Adapter and ledger interfaces are trusted process internals, not externally exposed RPCs |
| Grant revoked between reserve and dispatch | Revalidation in the transaction that claims dispatch | Revocation after dispatch does not revoke an in-flight authorization |
| Receipt forgery or reuse for another resource | Look up authoritative simulated receipt; compare complete intent, quote, request, purchase and amount | Receipts are plain deterministic records, not blockchain settlement evidence |
| Arbitrary URL / SSRF / redirect | Fixed client routes, fixed `.invalid` origin, ASGI-only transport, redirects disabled, environment proxies ignored | A future real HTTP adapter needs new egress, DNS, redirect, size, and timeout controls |
| Payment succeeded, delivery failed | Separate states; keep spent balance; retry delivery only with existing receipt | No refund/dispute system; merchant response quality is not independently proven |
| Process crashes or lost response | Durable dispatch marker, authoritative lookup, retained reservation, explicit reconciliation | Unknown can persist indefinitely; local cancellation proof has no automatic blockchain equivalent |
| Releasing unknown exposure while executor is delayed | Atomic simulator cancellation tombstone fences late execution | Future adapters must prove equivalent fencing or retain exposure |
| Malformed/corrupt resource | Typed schema plus request, service, quality, receipt and digest validation | A digest cannot establish truthfulness of merchant prose; content must stay untrusted for an LLM |
| Secret/proof leakage in logs | No payment credentials; no raw exception logging; allowlisted audit export; CLI uses receipt IDs; agent tracing disabled | Live OpenAI credentials stay in environment/client memory; raw database records contain simulated receipts and request content; no encryption at rest |
| Audit rewriting | Append-only SQL triggers; policy/quote/request snapshots | Not tamper-evident against privileged database/file modification |
| Simulation silently becomes real payments | No real payment network/token configuration, no signing path or wallet keys | The Phase 2 SDK calls only OpenAI in explicit live mode; real payments require new authorization and review |

## Failure policy

An exception from execution is ambiguous. The coordinator stores a fixed diagnostic
code, keeps the reservation, and waits for explicit reconciliation. Quote or grant
expiry does not prove an already dispatched payment failed. A missing authority
record also does not prove non-execution: an executor may be delayed.

Only authoritative failure/cancellation releases an in-flight reservation. A reserved
purchase that has never been claimed can be canceled safely under the same database
write lock as dispatch. Settled payment remains spent when merchant delivery fails.
Repeated purchase requests return current state and never automatically retry execution.

Reconciliation can be invoked concurrently. Terminal financial updates are conditional
and stale results cannot account twice. Delivered state is monotonic; provider caching
allows safe repeated retrieval. Recovery does not authorize a different purchase.

## Verification and assumptions

Tests exercise all quote payment fields, invalid grants, purpose, unknown services,
strict money, missing authorization, reserved-but-unclaimed adapter calls, intent
mutation, receipt forgery, replay, concurrency, delivery failures, timeout before/after
charge, cancellation fencing, and abrupt process exits at five boundaries.

The financial properties are demonstrated for this deterministic simulator with
intact local SQLite stores. Tests do not establish blockchain finality, cryptographic
correctness, merchant honesty, host security, backup safety, model robustness, or
compliance. No wallets, private keys, real payment proofs, or network transactions
are involved.

Phase 2 adds a bounded model interface without extending payment authority. Merchant
descriptions and delivered prose are omitted from model context. Tool arguments
cannot change trusted context or payment terms; scoped handles resolve stored quotes.
The application enforces eligible lowest-cost selection again before purchase.
Unavailable administration tools and parallel batches terminate the run. See
[Phase 2 limits and residual risks](phase2.md), including live API cost and the
difference between scripted tests and real-model security evaluation.
