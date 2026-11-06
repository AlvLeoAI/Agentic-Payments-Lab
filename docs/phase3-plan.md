# Phase 3A: x402 architecture and implementation plan

Status: research and proposal for human review, 2026-11-03. Nothing in this
document enables a payment integration or authorizes a live transaction.

## Baseline and scope

Inspected checkout: `main`, HEAD
`9a2a49fd8eb4ac1fe55d9bac657f2e2f48301beb`, matching the accepted Phase 2
baseline. The working tree was clean before this pass; origin is
`https://github.com/AlvLeoAI/Agentic-Payments-Lab.git`. Read `AGENTS.md`,
[architecture](architecture.md), [threat model](threat-model.md),
[Phase 2](phase2.md), and the actual catalog, policy, storage, ledger, adapter,
coordinator, provider, runtime, buyer boundary/store/runner, and isolated validator.

The accepted 151-test milestone and bounded live model checks concern simulated
payments. They do not establish x402 correctness. This pass did not rerun them,
load credentials, sign data, contact RPC/facilitator endpoints, or execute a live
model. The only repository changes are this document and
[data access and indexing](data-access-and-indexing.md). Disposable package/source
inspection and SQLite analysis live outside the repository.

Use these evidence labels throughout:

- **Verified:** observed in this checkout, published package, or linked official
  source; documentation claims are identified as such.
- **Proposed:** architecture and acceptance requirements, not implemented behavior.
- **Unverified:** dependency resolution, deployed contracts, provider availability,
  integration interoperability, and testnet execution that this pass did not test.

## Recommendation

Build an opt-in x402 backend alongside the simulator. Start with an application-owned
FastAPI merchant reached through HTTPX ASGI, SQLite accounting, one EOA signer
behind a durable authorization gate, and one hosted testnet facilitator. Use x402
**protocol v2 / HTTP / exact / EIP-3009**, Base Sepolia, and Circle test USDC.
Keep the four existing agent tools and deterministic service eligibility.

Phase 3B should implement and test the wire protocol, policy, persistence and
recovery entirely offline, using a recording signer double, HTTP mocks and chain
evidence fixtures. Phase 3C requires a separate authorization for actual testnet
signing/settlement and read-only chain observation. Its first smoke should use
deterministic orchestration, without an OpenAI request. Model-driven testnet use is
a later, separately scoped check.

Do not advertise the new backend as a drop-in implementation of the simulator's
`execute/lookup/cancel` guarantees. In particular, x402 core has neither the
simulator's cancellation fence nor a universal authorization-status lookup.

## External contract and version decision

All external sources below were retrieved on **2026-11-03**. Public documentation
was read; no operational `/supported`, `/verify`, `/settle`, wallet or RPC call was
made. The recommended dependency is **`x402[evm]==2.26.0`**, subject to the Phase 3B
dependency gate. Protocol version `2` and package version `2.26.0` are distinct.

| Item | Verified basis | Decision / remaining validation |
| --- | --- | --- |
| Published Python SDK | [PyPI 2.26.0 metadata](https://pypi.org/pypi/x402/2.26.0/json): uploaded 2026-10-28; Python `>=3.10`, Python 3.12 classifier; development status **Alpha** | Pin exact release and lock transitive dependencies in 3B; no dependency was installed in 3A |
| Artifact identity | Downloaded wheel `x402-2.26.0-py3-none-any.whl`; verified SHA-256 `6cdcdfe553a9b698dca81357101fc6e98de35c5c6f4ea2b3cce344ea54486c1b` against PyPI | Inspected archive text without importing or executing it; wheel is the implementation evidence |
| Source snapshot | [Foundation repository](https://github.com/x402-foundation/x402/tree/58e667ff091cfb9138c24a06c8f1ddd5546fc438), commit `58e667ff091cfb9138c24a06c8f1ddd5546fc438`; [Python changelog](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/CHANGELOG.md) labels 2.26.0 on 2026-10-28 | GitHub releases listing was empty; use PyPI artifact and changelog, not an assumed GitHub release tag. Do not install from moving `main` |
| Existing environment | Python `>=3.12,<3.13`; locked FastAPI 0.162.0, HTTPX 0.28.1, Pydantic 2.14.1; OpenAI 3.42.1 and Agents SDK 0.24.0 | Retain these versions and uv workflow; no OpenAI SDK changes |
| Declared compatibility | Core requires Pydantic `>=2`, typing-extensions, nest-asyncio. EVM extra adds eth-account `>=0.13`, eth-abi `>=5`, eth-keys `>=0.5`, eth-utils `>=4`, web3 `>=7.15` | Top-level constraints permit the existing Python/Pydantic/HTTPX stack. Full resolution and import/runtime compatibility are **not verified** |
| Minimal extras | HTTPX and FastAPI already installed | Select `evm` only. Avoid `all`, MCP, other chains and `fastapi[standard]` convenience extras. Keep project-owned FastAPI routes |
| Network | [Base network details](https://docs.base.org/get-started/connect-to-base): chain ID `84532`; v2 [network notation](https://docs.x402.org/core-concepts/network-and-token-support) uses CAIP-2 | Require exactly `eip155:84532`; reject mainnet `8453`, aliases, alternative networks and fallback |
| Token | [Circle testnet contracts](https://developers.circle.com/stablecoins/usdc-contract-addresses#testnet) lists `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | Pin network + address, six atomic decimals. SDK registry says EIP-712 name `USDC`, version `2`. Verify deployed domain/contract in 3C before signing. Test tokens are not dollar-backed funds |

The published package and source snapshot are not identical: for example, current
`main` changes SVM dependencies while still declaring 2.26.0. Its project-level uv
requirement is not a consumer runtime requirement. Do not infer that this project's
uv or unrelated packages need upgrading. The initial 3B dependency task must stop
and report conflicts rather than silently upgrade the accepted environment.

One runtime prerequisite emerged during SQLite source review: local Python 3.12.3
reports SQLite 3.45.1. Upstream documents a rare concurrent
[WAL-reset corruption bug](https://www.sqlite.org/wal.html#walresetbug), fixed in
3.51.3 and later, with specific older backports. The reported version lies in the
affected upstream range; a vendor patch has **not** been verified. Establish a
fixed Python-linked SQLite build or documented backport before testnet execution.
This is not evidence of existing corruption, and passing application tests cannot
certify the race absent. See the [runtime evidence](data-access-and-indexing.md#runtime-prerequisite).

### Wire and signing profile

The [v2 specification](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/specs/x402-specification-v2.md),
[HTTP binding](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/specs/transports-v2/http.md)
and [exact EVM scheme](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/specs/schemes/exact/scheme_exact_evm.md)
define the selected profile. Implement the following narrower application contract:

1. An unpaid request gets HTTP 402 with base64 JSON `PAYMENT-REQUIRED` containing
   `x402Version: 2`, `resource` and `accepts`. Parse bounded header bytes first;
   reject v1 headers, conflicting duplicate headers, malformed encodings, ambiguous
   JSON keys and unsupported fields that affect payment. Never follow `resource.url`.
2. Select exactly one operator-approved requirement. Wire `amount` is a decimal
   string of atomic units; convert once into the existing strict bounded integer
   domain. No floats, decimal price shorthand, exchange rates or symbol matching.
3. EIP-712 signs `TransferWithAuthorization`: payer, payee, value, `validAfter`,
   `validBefore`, and a 32-byte nonce, under chain ID and token-contract domain.
   EIP-3009 needs no ERC-20 allowance transaction. Reject Permit2, gas-sponsoring,
   delegated smart-account and other transfer methods in this initial backend.
4. Send the signed payload in base64 JSON `PAYMENT-SIGNATURE` on one paid request
   for the same method/path/body. The merchant submits it to the facilitator;
   the facilitator broadcasts `transferWithAuthorization` and pays transaction gas.
5. `PAYMENT-RESPONSE` transports settlement information. Parse it even on a resource
   error; neither HTTP status nor `success: true` alone proves final settlement.

The application deliberately requires `extra.assetTransferMethod=eip3009`,
`name=USDC`, `version=2`, and `paymentFlow=upfront` for its own merchant. The 2.26.0
EVM server supports `upfront`, but defaults to `authorization` (verify, resource,
settle). Upfront settles before resource execution and better matches our existing
payment/delivery separation. In this flow `/settle` validates as well as submits;
it does not require a separate `/verify`. Verification, when used for diagnostics,
does not reserve onchain funds and cannot establish non-execution after dispatch.

**Binding limitation:** the EIP-3009 signature does not sign the HTTP method, body,
service, task, purchase ID or payment-identifier extension. An application
fingerprint and merchant enforcement bind those values. A signature holder can
submit the authorized transfer before delivery, but cannot alter its signed amount
or recipient. This protocol does not guarantee fair exchange, delivery, refund,
or exactly-once business execution.

### Wallet/signing options

| Option | Keys, networks and developer fit | Limits, trust and operations | Recommendation |
| --- | --- | --- | --- |
| Dedicated local test EOA through `ClientEvmSigner` and `EthAccountSigner` | Published Python SDK supports eth-account signing; EIP-712 works for the selected EVM profile. Private key lives in trusted host memory, never the agent | No independent custody-policy enforcement. Application reservation/gate and a minimally funded test account bound exposure. Host compromise can bypass application policy. Requires secure operator provisioning and careful logging | Smallest 3C choice, **subject to operator approval**. 3B uses a signer double and no key. Expose only typed-data signing, not transaction signing or an RPC-capable signer |
| Circle Agent Wallets | Official [wallet overview](https://developers.circle.com/agent-stack/agent-wallets) documents user custody with 2-of-2 MPC; [networks](https://developers.circle.com/agent-stack/agent-wallets/supported-blockchains) include Base/Base Sepolia; [typed signing](https://developers.circle.com/agent-stack/agent-wallets/wallet-operations/sign) is available for EVM | Managed session/OTP and Circle availability replace local-key management. Documented interface uses Node CLI, adding a runtime/adapter. [Policy details](https://developers.circle.com/agent-stack/agent-wallets/wallet-operations/custom-policies) say policies are mainnet-only and exclude allowance transfers. Generic typed-signature spend coverage cannot be assumed | Viable later custody alternative. Not selected: Python signer interoperability and EIP-3009 policy coverage need proof; testnet policy support cannot be claimed |

Circle's broader [Agent Stack](https://developers.circle.com/agent-stack) includes
Gateway nanopayments and discovery. Those are documented capabilities, not a reason
to substitute deposits, batching or wallet CLI tools for the exact EIP-3009 scope.
No roadmap announcement is a dependency or acceptance guarantee here. Retain our
ledger limits regardless of custody choice; wallet balance and vendor spend controls
are not replacements for transactional purchase reservations.

### Facilitator options and conflicts

| Option | Official support / operational cost | Limits and decision |
| --- | --- | --- |
| Public `https://x402.org/facilitator` | [Network docs](https://docs.x402.org/core-concepts/network-and-token-support#x402org-facilitator) advertise Base Sepolia and testnet-only use without setup | Recommend for first bounded testnet smoke. No availability, retention, idempotency, finality or status-lookup SLA was verified. No automatic provider failover |
| CDP hosted facilitator | [CDP documentation](https://docs.cdp.coinbase.com/x402/seller/facilitator) explicitly lists v2 exact on Base Sepolia and Base; Python `cdp.x402` can configure authenticated requests | Adds CDP API credentials, service screening and fee/account dependencies. Current docs list 1,000 monthly onchain transactions free, then $0.001 each; recheck before adopting. Do not infer a universal cancellation or lookup API |
| Circle Facilitator Service | [Quickstart](https://developers.circle.com/facilitator-service/quickstart) documents exact, signed seller proofs and a provider-specific `/status` flow; trial example is Arc testnet | Do not infer Base Sepolia support from a Base mainnet/Agent Wallets listing. Seller-proof signing and status integration add scope; defer pending explicit network/SDK verification |
| Self-hosted facilitator | [Official deployment overview](https://docs.x402.org/core-concepts/facilitator) and SDK support verification/settlement | Adds funded gas payer, RPC, nonce management, durable relayer state, maintenance and uptime. Defer; unnecessary infrastructure for this milestone |

Core v2 specifies verify/settle/supported, not a portable status/cancel service.
Provider-specific extensions need their own evidence contract. In particular,
current v2 requires a transaction hash with `settlement_pending`, while Circle's
quickstart documents an empty hash plus `settlement-status.paymentId`. These sources
conflict. Preserve uncertainty for either form; do not translate an empty hash into
failure or claim portable status support. Circle is not the selected integration.

The EVM spec also describes ERC-7710 whereas the inspected Python exact client
branches between EIP-3009 and Permit2. General protocol capability is not Python
feature parity. Explicitly reject unsupported methods instead of accepting the
client's fallback. Likewise, Circle wallet marketing describes spend controls more
broadly than the detailed testnet policy documentation allows.

## Architecture and authority

| Component | Authoritative responsibility | Must not do |
| --- | --- | --- |
| Operator config/catalog | Merchant identity, fixed route/method/origin, network, token, recipient, exact price, signer identity, deadline ceiling and state mode | Accept replacements from model arguments or merchant metadata |
| Buyer boundary | Existing `list_services {}`, `get_quote {service_id}`, `purchase {quote_id}`, `get_purchase_status {}`; host-bound task/goal; cheapest eligible service | Add signer, URL, grant, nonce, cancel or reconcile tools |
| Ledger/policy | Purchase identity, grant accounting, reservation, immutable approved terms, one-time signing/dispatch claims, accepted evidence and audit | Treat a model claim, SDK exception or merchant receipt as settlement authority |
| x402 adapter | Normalize protocol to vendor-independent contracts; create one authorized payload; perform one authorized exchange | Autopay on arbitrary 402, select another requirement, re-sign or resubmit unknown payment |
| Restricted signer | Verify exact typed-data fields and durable claim before delegating signing | Generic message/transaction signing; repair or mint a missing authorization |
| Merchant store/routes | Bind payment ID and nonce to request; fence settlement submission; persist outcomes and delivery/cache identity | Charge again because its result cache expired or a client retried |
| Facilitator | Verification and transaction submission | Authorize buyer budget, guarantee resource content, or decide buyer accounting |
| Chain observer | Read-only evidence tied to configured chain/token, nonce, transaction and finality policy | Submit transactions or manufacture a conclusive answer from missing records |
| Agent metadata | Quote handles and run/usage/termination history, backed by existing ledger binding witness | Repair lost financial state or replace purchase identity |

Keep `payments.sqlite3` as the simulator's independent payment authority. For x402,
the **token contract on the selected canonical chain** is payment authority; local
rows are approval records and observations. Add x402 intent/observation rows to the
buyer ledger, not another financial database. Merchant persistence stays in its
own database, and agent metadata stays separate. No transaction spans those files
or the chain. See [persistence plan](data-access-and-indexing.md#proposed-phase-3-persistence).

### Proposed interfaces and lifecycle

Introduce vendor-independent typed contracts for approved payment, payment evidence
and exchange result. Keep existing simulator types/serialization valid. Add a
discriminated testnet evidence type; never put blockchain evidence in
`SimulatedReceipt`, nor return `mode=simulated` for testnet execution.

| Existing seam | Proposed evolution |
| --- | --- |
| `execute(intent) -> Resolution` | Asynchronous `submit_once(approved) -> ExchangeObservation`: payment evidence plus optional untrusted delivery candidate. x402 payment and resource response occur in one HTTP exchange; they cannot be modeled as two independent executions |
| `lookup(intent)` | `observe(existing_authorization)`: read-only merchant status and chain observations. Missing record, unsupported lookup, pending inclusion or unavailable RPC returns unknown. Never call settle to implement lookup |
| `cancel(intent)` | Explicit capability. Simulator keeps its tombstone. Initial x402 backend returns `unsupported` and retains exposure; no cancellation signature or transaction is implemented |
| `merchant.deliver(receipt)` | Validate initial response or fetch cached result for the existing request through an application-owned, non-charging retrieval operation. Never repeat the paid endpoint as a delivery retry |
| `Resolution(status="failed")` | Require typed non-execution evidence for x402 before the ledger can release a claimed reservation. Generic HTTP/SDK failure is not enough |

Adapt the simulator behind the shared coordinator seam without changing its states,
receipt JSON, cancellation behavior or existing CLI defaults. Async network work
must remain outside SQLite transactions. Keep OpenAI imports in `buyer_agent/` and
x402 imports in a new adapter package, out of policy/accounting code.

Proposed flow:

1. Operator explicitly initializes a separate `x402-testnet` state directory with
   mode/config identity. Normal commands require initialized files and refuse missing
   or inconsistent financial history; never silently create replacement state.
2. `get_quote` performs an unpaid ASGI request. Parse and validate the challenge,
   bind canonical terms to task/request and a local quote deadline, then return the
   existing opaque quote handle. x402 does not provide our task or quote identity.
3. In one ledger write transaction, authorize and reserve integer atomic funds.
   Store backend, profile, immutable fingerprint, purchase ID and intended signer.
   The existing policy and budget checks still decide eligibility.
4. Claim execution with a compare-and-set write before invoking the SDK. Recheck
   grant, quote and configuration. Only this claimant may create a payload. Operator
   cancellation while still reserved competes with claim under the same ledger lock.
5. The SDK creates a nonce and validity window. Its **public `ClientEvmSigner`
   implementation supplied by us** validates the complete domain/types/message,
   checks the execution claim, and durably records the nonce, typed-data digest,
   `validAfter/validBefore` and `signing_started` **before** calling the actual signer.
   A unique `(network, asset, payer, nonce)` binds it permanently to one purchase.
   Failure to persist means no delegation. Another signing call for that purchase
   is refused even after a timeout or restart.
6. Validate the returned payload against the persisted tuple and accepted requirement;
   persist payload digest and a dispatch marker before the sole paid HTTP request.
   Signatures stay ephemeral in trusted memory. Do not persist raw signed payloads
   in this first design; recovery uses nonce/evidence, never re-signing/retransmission.
   A crash here may strand a purchase until explicit resolution. This deliberately
   trades liveness for a small secret-storage surface.
7. Merchant atomically inserts a pending operation keyed by application payment ID,
   full request fingerprint and unique authorization tuple. Persist `settle_started`
   before one facilitator settlement call. Concurrent/repeated submissions observe
   existing state; same ID with changed request or different ID reusing a nonce fails.
8. Merchant stores settlement observation and result/cache state. Buyer independently
   validates evidence. Until finality, retain `unknown` payment and reservation;
   an early resource can be buffered privately but is not marked delivered yet.
9. Explicit operator reconciliation observes the **same** nonce. Verified final
   settlement atomically moves reserved to spent; then validate and record delivery
   or retrieve the cached resource. Payment can be settled while delivery is failed.
   Agent cancellation changes run evidence only; it does not cancel/reconcile money.

The EVM SDK sets `validAfter=0` and `validBefore=wall_clock+maxTimeoutSeconds`; it does
not accept our durable nonce/deadline as public creation parameters. Do not monkey
patch its clock or private methods. Before creation, require enough remaining local
quote/grant lifetime for the approved window. The signer gate checks the actual
window against the absolute operator/quote/grant deadline and rejects overruns or
clock drift. Time passing between those checks fails closed. A live runtime needs
an explicit wall clock; it cannot inherit the simulator's `FixedClock`.
Propose a 30-second authorization window inside the 60-second local quote lifetime,
with grant validity extending through it; a full 60-second SDK window would overrun
the quote as soon as signing occurs later than quote creation. Exact values remain
operator-approved and must satisfy facilitator preflight checks.

### SDK integration controls

Verified against published 2.26.0 source (corresponding source links below):

- Use `x402Client.create_payment_payload` only inside the claimed adapter, registering
  **only** `eip155:84532` with `ExactEvmScheme` and the restricted signer. Supply a
  validated single-accept challenge; use explicit selector/policy rejection as a
  second check. Default selection is otherwise the first supported requirement.
- Use `x402.http.utils` header codecs behind stricter application size/type checks.
  Do not install `x402AsyncTransport`/automatic HTTP payment wrappers on any shared
  client. They create payloads and retry 402 requests automatically.
- Do not use `x402ResourceServer.settle_payment` for our durable settlement path:
  2.26.0 contains one automatic retry on `settlement_pending`. Our merchant calls
  low-level `HTTPFacilitatorClient.settle` once, after a durable claim. Disable
  HTTP transport retries; no failover and no settlement retry on restart.
- Inject a bounded `httpx.AsyncClient` into `FacilitatorConfig`, with TLS validation,
  `trust_env=False`, redirects disabled and approved host/method/path restrictions.
  The SDK defaults enable redirects. Its synchronous `get_supported()` constructs
  another client rather than using that async injection: fetch capabilities through
  our hardened transport and validate `SupportedResponse` instead of invoking it
  implicitly at startup. Offline tests supply fixtures; live preflight is operator-only.
- Disable SDK debug/body logging, including signer debug output of typed-data
  messages and HTTP exception excerpts. Public audit contains hashes, sanitized
  codes and finality evidence identifiers, not raw headers, signatures, wallet/RPC
  credentials or merchant bodies. Test hostile bodies and exceptions with sentinels.

Source references: [client](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/client.py),
[EVM client](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/mechanisms/evm/exact/client.py),
[signer protocol](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/mechanisms/evm/signer.py),
[HTTP wrapper](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/http/clients/httpx.py),
[server retry](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/server_base.py),
[facilitator client](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/python/x402/http/facilitator_client.py).

### Merchant idempotency and delivery contract

Require the [payment-identifier extension](https://github.com/x402-foundation/x402/blob/58e667ff091cfb9138c24a06c8f1ddd5546fc438/specs/extensions/payment_identifier.md)
on our merchant. The identifier is application-owned, stable, and distinct from the
EIP-3009 nonce. Bind it to merchant scope, method/path, canonical body hash, purchase,
selected requirements and authorization tuple. The SDK's declaration helper does
not implement our durable cache, locking, retention or identity checks.

Identical requests return existing pending/terminal state or a retained result;
changed fingerprints return conflict. Keep non-sensitive identity/tombstone/evidence
rows for the lifetime of the testnet state. Proposed resource-cache retention is
at least seven days; expiry deletes content only, never makes the ID reusable.
Missing content after settlement returns `delivery_unavailable`/gone. A service
may regenerate the same deterministic result under the same ID without settlement;
otherwise delivery remains failed. There is no automatic refund or replacement buy.

Recovery/status retrieval is an application extension, not an x402 guarantee.
For 3B and the initial 3C smoke it remains inside the trusted ASGI/host boundary.
Do not expose an unauthenticated public retrieval endpoint or treat a guessable
purchase ID as authentication. External HTTPS merchant deployment, access control,
DNS/SSRF defenses and retention contracts require a separate review. Existing
`.invalid` routes remain ASGI-only; only the explicitly enabled facilitator and
read-only chain observer gain network egress in 3C.

## Conclusive evidence and finality

Proposed policy is **finalized canonical L2 evidence** for terminal buyer accounting,
not an arbitrary confirmation count or fixed sleep. [Base finality documentation](https://docs.base.org/specifications/transactions/transaction-finality)
distinguishes sequencer inclusion, L1 data inclusion and L1-backed finality. Mainnet
timings are not promises for Sepolia. A live RPC must support the required finalized
view and historical state/log reads; otherwise reconciliation remains unknown.

| Conclusion | Required evidence / authority |
| --- | --- |
| Settled | Correct chain ID and token contract; successful transaction receipt; canonical block hash at/below the verified finalized head; matching `AuthorizationUsed(payer,nonce)` and token `Transfer(payer,payee,amount)` in that transaction, with calldata/authorization correspondence checked. Store transaction, block/hash, log positions, observation time, evidence digest and finality policy |
| Safely canceled before signing | Intact ledger proves the execution/signing claim was never granted, and cancellation atomically prevents a later claimant. Existing reserved cancellation may retain this meaning |
| Expired without execution | At a canonical finalized block whose timestamp is at/after `validBefore`, `authorizationState(payer,nonce)` is false for the pinned token. Persist the exact block/state observation and contract assumptions. This combines expiry with authoritative non-use; local wall-clock expiry is insufficient |
| Onchain canceled | Matching finalized `AuthorizationCanceled(payer,nonce)` under the pinned contract, rather than merely `authorizationState=true`. Initial backend cannot initiate this operation; support for observing such evidence can be tested offline |
| Unknown | HTTP success/error, transaction hash alone, facilitator rejection/pending, reverted transaction alone, missing local/cache row, nonce used without distinguishing use from cancellation, incomplete logs, RPC outage or unfinalized evidence |

[EIP-3009](https://eips.ethereum.org/EIPS/eip-3009) is marked Draft on its standards
page; implemented token behavior must still be checked. [Circle's contract source](https://github.com/circlefin/stablecoin-evm/blob/master/contracts/v2/EIP3009.sol)
shows nonce state becomes true for both use and cancellation. A failed transaction
does not invalidate the signed authorization: another submitter may still use it.
Nor does a facilitator's rejection fence a merchant that already has the signature.

Retain the reservation until one of the conclusive outcomes above exists. A reorg
before finality invalidates observations and leaves payment unknown. Do not release
funds, renew authorization or resubmit. A contradiction after accepted finality is
an operator incident: freeze the affected testnet state and preserve spent/reserved
accounting and evidence; no automatic rollback or new payment. Trust assumptions
include the configured RPC, chain finality, token implementation/upgrade behavior,
honest local storage and a dedicated signer not used by other applications. This
is not a trustless light client or a mainnet safety claim.

## Failure matrix

Each row becomes at least one deterministic Phase 3B test. Crash tests must use
fresh temporary state and actual process termination for critical persistence gaps.

| Boundary / failure | Durable state and financial exposure | Allowed recovery; prohibited shortcut |
| --- | --- | --- |
| Malformed/oversized 402; substituted endpoint, recipient, chain, asset, amount, domain, flow or extension | No signing; denied before reservation or atomically canceled before claim | Reject. Do not choose a merchant-provided fallback or follow redirects |
| Requirements change after quote/reservation | Original fingerprint and reservation remain | Reject before signing; release only if no execution claim could have signed. After claim, conservatively unknown until evidence resolves |
| Crash after quote persistence, before reservation | Unused quote/handle; no exposure | Existing quote may expire; never reconstruct a task from missing agent metadata |
| Crash after reservation, before claim | Reserved; no signer authority | Explicit cancellation competes atomically with claim and releases if it wins |
| Crash after claim, before signer journal | Executing with ambiguous progress | No second payload. Missing journal alone does not prove no signing; hold for operator recovery or intact conclusive gate evidence |
| Signer timeout/crash after nonce journal, before result | One immutable nonce, reservation and signing-start evidence | No re-sign. Resolve that nonce; usage of signing/requests can be incomplete |
| Signed payload lost before dispatch | Payload digest may be absent; nonce retained, reservation held | No payload regeneration. Expiry plus finalized non-use can resolve it; local absence cannot |
| Crash after dispatch marker, before HTTP | May have been sent | Observe same nonce. Never turn missing merchant record into release or retry |
| Merchant records pending, crashes before/during settle | Durable request/nonce binding, possibly submitted payment | No automatic settle takeover. Explicit read-only reconciliation; empty facilitator/merchant result is inconclusive |
| Settlement succeeds, response lost before merchant or buyer commit | Chain transfer may exist; buyer remains unknown/reserved | Recover transaction from token nonce logs/merchant observation, validate finality, account once |
| Receipt/header/body malformed or HTTP failure after payment | Original authorization may have settled | Record safe error code and unknown; inspect chain, not a new authorization |
| Merchant resource succeeds but settlement pending | Buffered result is not buyer delivery success | Wait for explicit conclusive payment observation before delivered state |
| Buyer settled; resource failed/corrupt; delivery acknowledgment lost | Spent stays spent; merchant may have cached result | Retrieve only non-charging existing result; verify identity/digest/schema before delivered |
| Concurrent purchase, settle, or reconciliation calls | Unique identities and conditional transitions | 100 retries produce at most one signer delegation, paid dispatch and merchant settle call; duplicate chain evidence accounts once |
| Restart/replay; different payment ID with same nonce | Existing purchase and merchant authorization binding | Read current state/conflict. Neither fresh ID nor process restart grants another signature |
| Agent metadata/binding missing or changed goal | Ledger witness and financial state survive | Existing explicit agent recovery contract; no new task/purchase, automatic repair or payment |
| Ledger/merchant financial metadata missing, partial restore or mode mismatch | Financial exposure may be unobservable | Fail closed; preserve surviving records. Explicit operator restore/review, not empty-store initialization |
| Facilitator/RPC unavailable; incomplete log range or inconsistent providers | No conclusive financial answer | Retain exposure; report unavailable/unknown with last observation. No failover settlement |
| Quote/grant/auth expires; allowance/balance changes; token paused | Local expiry or rejection does not prove prior non-execution | Finalized non-use after auth deadline or other conclusive evidence required |
| Merchant lacks durable idempotency/status support | Local and chain duplicate-transfer defenses cannot guarantee business delivery | Refuse initial merchant admission for this milestone; a declaration alone is insufficient |
| Cache unavailable or retention elapsed after payment | Settlement and identity tombstone remain | Delivery pending/failed/gone; no charge or replacement operation |
| Reorg before finality; finality unavailable | Observed inclusion is provisional | Unknown/reserved; inspect canonical chain on explicit reconciliation |
| Model timeout/cancellation, including after settlement | Agent termination independent of ledger/merchant state | Existing best-effort partial usage persistence; propagate cancellation; no financial cleanup side effects |

Cross-system atomicity remains unavailable: ledger reservation/signing journal,
merchant settlement journal, chain transfer, buyer accounting and agent report have
separate commit points. Idempotent evidence application and conservative unknown
states bridge them. No lease expiry authorizes a second settlement worker. Recovery
may remain incomplete indefinitely when authoritative evidence or original history
is lost. Cooperative timeouts cannot bound uncooperative signing/I/O or undo dispatch.

## Phase 3B work plan and acceptance gates

All paths below are proposed; no application file was changed in Phase 3A.

| Order | Modules / deliverable | Measurable gate |
| --- | --- | --- |
| 1 | Dependency/profile and SQLite-runtime review; `pyproject.toml`, `uv.lock` only after 3B authorization | Resolve exact x402 2.26.0 EVM extra with accepted Python/OpenAI/FastAPI/HTTPX/Pydantic versions; inspect lock diff; no unexplained upgrades; confirm artifact identity and imports offline; identify a fixed SQLite build/backport before live use |
| 2 | `domain.py`, `payments.py`; new `x402_adapter/contracts.py`, `codec.py` | Typed backend/evidence discrimination; raw v2 header round trips and malformed/changed-field cases; simulation JSON and all four tool input schemas unchanged |
| 3 | `ledger.py`, `storage.py`, proposed `migrations/`; state initialization in `runtime.py`/CLI | Atomic reserve/claim/journal constraints; one nonce per purchase; state-mode mismatch/missing-store refusal; migration on copied fixtures preserves identities, balances, audit and simulator receipts |
| 4 | `x402_adapter/signer.py`, `client.py`, `transport.py` | Public signer protocol intercepts every request; reservation and persisted nonce observed before fake delegation; one paid request; zero automatic retries, redirections, alternate networks or generic signing; no secrets needed |
| 5 | New `x402_provider.py` and merchant persistence | Real FastAPI/HTTPX ASGI v2 exchange; durable fingerprint/nonce uniqueness; upfront flow; one mocked settle; pending responses never cause a retry; non-charging cache retrieval and tombstones survive restart/retention expiry |
| 6 | `x402_adapter/observer.py`, coordinator and operator reconciliation | Mocked finalized receipt/events/state proofs drive accounting exactly once; failures/reorgs stay unknown; cancel unsupported after claim; reconciliation never signs or settles |
| 7 | `buyer_agent/contracts.py`, boundary/report integration, CLI audit | Four tools and deterministic recommendation preserved; testnet evidence labeled distinctly; agent exit after payment does not overwrite finance; sentinel headers/keys/signatures absent from prompts, logs, tools and public exports |
| 8 | `tests/test_x402_contract.py`, policy, concurrency, recovery and evidence tests; docs | Cover every failure-matrix row; full Phase 1/2 suite protected; isolated validator passes with no credentials or network; demo replay checks still use payment-authority records |

Additional required assertions:

- 100 concurrent attempts for one purchase: at most one signer delegation, one paid
  request and one mocked facilitator settle; one authoritative transfer at most.
- 20 competing purchases under a three-purchase budget: at most three authorized
  amounts in spent plus reserved. Unknown amounts remain reserved. Query the mock
  payment authority directly rather than counting buyer success reports.
- Kill processes before/after signing journal, dispatch, merchant settle claim,
  authority transfer, buyer accounting and cache commit; restart never signs/settles
  again. Explicit observation can complete proven outcomes without duplicate spend.
- Test two usage-bearing model responses then interruption after mocked payment,
  plus missing/cross-task quote handles, to preserve Phase 2 authority/durability.
- Demonstrate a pending facilitator response reaches the caller with exactly one
  recorded HTTP settle attempt, including SDK and subprocess paths.
- Keep `validate_offline.py` fail-closed network namespace and subprocess checks.
  Install locked dependencies before isolation; new tests use ASGI/MockTransport
  and fixtures. No public RPC, faucet, credential, model or facilitator prerequisite.
  Future live validation is manually authorized and separate from hosted offline CI.

Recommended first implementation milestone: **the offline contract plus durable
signer/dispatch gate (steps 1–4)**, reviewed before merchant settlement wiring. Its
demonstration is a valid challenge producing a recorded fake signature only after
reservation; substituted terms and restart attempts produce zero further signer
calls. Do not start with an automatic-pay SDK example or a funded wallet.

## Phase 3C proposed testnet smoke and operator decisions

Before 3B, approve the narrow EIP-3009 profile, proposed interface/evidence changes,
ledger migration and index/query changes, signature non-retention/liveness tradeoff,
and local ASGI merchant scope. Any required SQLite runtime correction needs its own
reviewed environment change. Approval to implement 3B still does **not** authorize
key loading or a live payment.

Before 3C, the operator must explicitly select and approve:

1. Dedicated test signer/custody and secure provisioning; recipient address; public
   facilitator versus managed alternative; exact allowlisted read-only RPC endpoint.
2. Fixed service/method/path, exact atomic price, total budget, validity window and
   minimal test token funding. Suggested smoke: one 10,000-atomic-unit purchase
   (0.01 test USDC), equal cap and budget, no new authorization on failure.
3. Finalized-evidence accounting and bounded observation schedule. Suggested limit:
   one unpaid challenge, one signing delegation, one paid dispatch and one settle;
   zero execution retries. At most ten explicitly requested read-only observation
   batches within 30 minutes; fixed RPC call ceilings per batch/log range. Exhaustion
   is INCOMPLETE with reserved exposure, never permission to resubmit.
4. Merchant result-retention policy, absence of automatic refund, and operator
   procedure for stranded signatures, missing history and unavailable finality.
5. A separately written smoke authorization covering the selected endpoints,
   credentials, transaction and read-only observations. No mainnet, smart accounts,
   approvals, bridge, onchain cancellation or extra scenarios are implied.

Preflight should verify facilitator-advertised v2/exact/network support, chain ID,
token code/domain, read-only evidence methods/finalized tag, dedicated signer and
recipient, budget and state initialization. Record configuration without secrets.
Abort on incompatibility; do not switch providers/networks automatically.

Smoke PASS requires one matching finalized token transfer, one buyer spend of the
approved integer amount, zero remaining reservation, and one validated delivery.
Repeating the application purchase must return its state with zero additional
signer/dispatch/settle calls. Read-only authoritative token evidence, not a local
success flag or the simulator's hardcoded `signing_attempts`, establishes transfer.
Loss of response/finality is INCOMPLETE, with evidence and reservation retained.

This would establish interoperability for one controlled testnet transaction and
local merchant. It would not establish production custody, public merchant security,
general model decision quality, provider reliability, fair exchange or mainnet
safety. A subsequent model-driven test requires its own named model, request/token
limits, API cost estimate and authorization, in addition to payment limits.

## Research completion and remaining blockers

Completed: checkout inspection; official specification/docs and published wheel
inspection; source/release conflict analysis; disposable SQL query-plan comparison;
these two planning documents. Package resolution, signing gate tests, wire
integration, persistence migrations, live facilitator support, deployed-contract
checks and chain reconciliation remain proposed and untested.

Primary blockers for execution are human approval of 3B, unresolved full dependency
compatibility and SQLite patch status, then 3C custody/endpoints/finality/retention
approval plus live preflight. The design deliberately permits unresolved exposure rather than inventing
the simulator's cancellation or lookup guarantees. No exclusive OpenAI/blockchain
partnership is assumed or needed.
