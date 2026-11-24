# Requirements and test traceability

This document lists what the lab is required to do, who owns each requirement, and
which tests assert it. It describes the simulator and the bounded buyer agent. All
payments are simulated; see the [README](../README.md) for scope and limits.

## How to read this

- **Owner** is the party that is allowed to perform the action. The split is the
  point of the design: the operator grants authority, the model chooses, and the
  application decides and pays.
- **Model-dependent** means the outcome depends on what a language model does. Tests
  for these requirements use a scripted or sequenced model. They assert what the
  application allows and records, not that a real model behaves well. Evidence from
  real-model runs is in [luna-baseline.md](luna-baseline.md) and
  [phase3b8.md](phase3b8.md), and its explanation review is still pending.
- **Direct tests** are tests whose assertions state the requirement. A test that
  only exercises the code on the way to asserting something else is not listed.
  Where no test asserts a requirement, the entry says "No direct test".
- Test references are `file` and test name. Paths are relative to `tests/`.

Status as of 2026-11-24, including the tests added in `da43583`, the comparison
call timing added in `de0c8d0`, the deadline-bounded lock waits added in
`f05a74d` and the preflight timeout diagnostic fixed in `fe54a55`. The latency requirements were added on 2026-11-16 and have not been
measured.

## Functional requirements

### Operator

#### FR-1 Establish spending authority

The operator creates a grant with a budget, a per-purchase cap and an expiry, and
binds a task to it. The agent can never do this.

Direct tests:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_policy_denials` (cases `cap`, `grant_expired`, `grant_revoked`,
  `grant_scope`): a purchase over the cap, after expiry, after revocation or outside
  the granted services is denied with no payment attempt and nothing reserved.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_missing_grant_and_unregistered_task`: a task that was never registered
  cannot reserve, and a task bound to a missing grant is refused before payment.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_conflicting_request_and_grant_binding`: a task cannot be re-bound to another
  grant.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_amount_formatting_and_contracts`: a grant whose cap exceeds its budget is
  rejected at construction.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_administrative_tools_are_unavailable`: a model call to `grant` or
  `create_task` ends the run as an unavailable tool, with no authority activity.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_existing_grant_cannot_be_replaced_with_different_contents`: a grant with a
  different budget, cap, expiry, service list, purpose or operator is rejected,
  and the stored grant and budget are unchanged.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_resubmitting_identical_grant_is_idempotent`: an identical grant is accepted
  without resetting spent budget or adding a second creation event.

#### FR-2 Bind the request

Subject, purpose and minimum quality are fixed before any model call.

Direct tests:

- [`test_agent_durability.py`](../tests/test_agent_durability.py)
  `test_legitimate_phase1_task_requires_explicit_initialization`: running the buyer
  without an initialized binding fails before any run record or authority activity,
  and a later initialization with a different subject or quality conflicts.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_quote_handles_are_durable_task_scoped_and_request_bound`: after a purchase,
  running the same task with a changed subject, purpose or quality raises
  `PurchaseConflict`.
- [`test_agent_durability.py`](../tests/test_agent_durability.py)
  `test_missing_metadata_before_purchase_cannot_change_intent`: losing the agent
  metadata does not allow a replacement subject; recovery restores the original.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_strict_argument_rejection_counts_attempts`: tool arguments that try to set
  `subject`, `purpose`, `grant_id` or `amount` are rejected.

### Buyer agent (model-dependent)

FR-3 to FR-6 are model-dependent. See "How to read this".

#### FR-3 Discover eligible services

The agent discovers eligible services from the operator-approved catalog.

Direct tests, on the `list_services` result as the next model request carries it:

- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_catalog_view_lists_only_eligible_services`: across eight cases (two bound
  qualities, cap, granted services, remaining budget, purpose, revoked, expired)
  only services that meet the grant, the bound quality and the remaining budget
  are listed, and an empty list carries the reason `no_suitable_service`.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_catalog_view_exposes_only_trusted_terms`: each service has exactly
  `service_id`, `quality` and `amount_atomic`, and internal catalog fields such as
  the description, recipient and schemas reach no model request.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_catalog_view_order_is_deterministic`: services are ordered by price, then
  identifier, whatever the catalog's own order.

Eligibility is enforced again when a quote is requested; that is tested under FR-4.

#### FR-4 Select or decline

The agent chooses a service that meets the bound quality within the remaining
budget, or declines with a reason. Refusal is a valid outcome, not a failure.

Direct tests:

- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_no_suitable_service_does_not_lower_quality`: under six restrictions (budget,
  cap, services, purpose, revoked, expired) the run completes with decision
  `no_suitable_service`, no purchase and no authority activity.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_application_enforces_cheapest_and_quality`: a quote request for a service
  below the bound quality, or above the cheapest eligible one, is refused as
  `service_not_eligible`.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_actual_sdk_free_paid_and_repeated_run`: for each quality level the scripted
  buyer completes with the matching service price and payment state.

#### FR-5 Quote and purchase through bounded tools only

Direct tests:

- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_untrusted_prose_is_not_forwarded_and_tools_are_narrow`: every model request
  carries exactly four tools with strict schemas that accept only `service_id` or
  `quote_id`, with no handoffs.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_administrative_tools_are_unavailable`: `reconcile`, `cancel`, `create_task`,
  `grant`, `shell` and `browse` are unavailable.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_quote_handles_are_durable_task_scoped_and_request_bound`: a fabricated quote
  handle, another task's handle and a handle for a different request are all
  rejected, with no authority activity.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_tool_and_turn_limits_fail_closed`: the run stops at the tool limit and at
  the turn limit.

#### FR-6 Explain the decision

The explanation is returned in the run report as untrusted text and is excluded
from durable storage. The application-owned decision record is the authoritative
audit source.

Direct tests:

- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_timeout_before_purchase_and_fabricated_success`: a model that claims a
  successful payment is reported with decision `not_purchased` and payment state
  `not_started`; its text appears only in `model_explanation` and is absent from the
  stored run records.
- [`test_offline_evaluation.py`](../tests/test_offline_evaluation.py)
  `test_evaluation_covers_unknown_replay_operator_delivery_and_incident`: a forged
  explanation neither earns nor erases an evaluation score.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_invalid_model_output`: an explanation over 1,000 characters is rejected.

Why it is not stored: the explanation is post-hoc model text. It can carry injected
content, and it does not reliably reflect why the model acted. The agent store
excludes `model_explanation` on purpose, and the first test above asserts that.

Future decision, not a gap: if audit-grade explanations are ever required, they
would be stored sanitized and length-bounded, and checked for credentials at the
same persistence boundary as `check_decoded`.

### Application and payment authority

#### FR-7 Authorize

Grant, cap, budget and quote validity are evaluated atomically, and funds are
reserved.

Direct tests:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_policy_denials`: ten denial cases, each with no payment attempt and nothing
  reserved.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_untrusted_quote_fields_never_reach_adapter`: twelve forged quote fields are
  each denied before the payment adapter.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_insufficient_budget_is_not_dispatched`: a second purchase over the remaining
  budget is refused without a payment attempt.
- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_concurrent_distinct_tasks_cannot_overspend`: twenty tasks competing for a
  budget of three purchases settle exactly three; seventeen are refused.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_recheck_before_dispatch`: a grant that expires or is revoked after
  reservation is denied before dispatch, and the reservation is released.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_adapter_rejects_unapproved_or_mutated_intent`: a reserved purchase is not
  dispatch approval, and a changed amount is refused.

#### FR-8 Execute at most once

A purchase is executed at most once, and its outcome is recorded as settled,
declined or unknown.

Direct tests:

- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_100_simultaneous_retries_one_charge`: one hundred simultaneous attempts
  produce one purchase, one execution, one charge and one dispatch event.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_real_process_exit_and_fresh_process_recovery`: after a process exit at each
  of five checkpoints, a fresh process reports the expected state with at most one
  execution and one charge.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_authoritative_decline_releases_budget`: a decline is recorded, the budget
  is released, and a repeat returns the same outcome without another attempt.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_timeout_after_charge_is_reserved_until_reconciled`: a timeout is recorded
  as `unknown` and the amount stays reserved.

Naming: a decline is stored as payment state `failed` with error
`simulated_decline`. `denied` is the separate state for a policy refusal.

#### FR-9 Request and verify delivery

Delivery is requested and verified, and delivery state is independent of payment
state.

Direct tests:

- [`test_recovery.py`](../tests/test_recovery.py)
  `test_paid_but_delivery_failed_recovers_without_payment`: with an unavailable or
  corrupted response the purchase is `settled` and `failed`; recovery delivers
  without another payment.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_success_and_independent_delivery`: a settled purchase is delivered, and the
  resource matches the requested subject.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_free_service_has_no_payment_attempt`: a free service is delivered with
  payment state `not_required` and no payment attempt.
- [`test_provider_cli.py`](../tests/test_provider_cli.py)
  `test_receipt_forgery_and_replay_are_rejected`: the merchant refuses a forged
  receipt, another purchase's receipt, a changed quote and a changed amount.

#### FR-10 Replay safely

Repeating a task returns the existing result without a new charge.

Direct tests:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_success_and_independent_delivery`: a second purchase call returns the same
  outcome with one execution.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_actual_sdk_free_paid_and_repeated_run`: a second agent run returns the same
  purchase, and the payment authority shows one charge.
- [`test_provider_cli.py`](../tests/test_provider_cli.py)
  `test_provider_returns_same_resource_after_expiry`: the merchant returns the same
  resource after quote expiry with one charge.
- [`test_provider_cli.py`](../tests/test_provider_cli.py)
  `test_cli_workflow_and_repeatable_demo`: `status` and `reconcile` return the paid
  result, and a repeated demo returns the same output.

#### FR-11 Reconcile uncertain outcomes

Reconciliation happens on operator request, by lookup or cancellation, never by
re-execution.

Direct tests:

- [`test_recovery.py`](../tests/test_recovery.py)
  `test_timeout_after_charge_is_reserved_until_reconciled`: reconciliation by lookup
  settles the purchase with the execution count still at one.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_unknown_requires_authoritative_resolution_and_fences_late_payment`:
  cancellation releases the reservation, and a late execution of the same intent is
  refused.
- [`test_recovery.py`](../tests/test_recovery.py)
  `test_crash_before_adapter_is_unknown_not_retryable`: after a crash before the
  adapter, reconciliation reports `unknown` and never executes; explicit
  cancellation releases the reservation.
- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_concurrent_reconciliation_accounts_once`: ten concurrent reconciliations
  account once, with one execution.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_uncertainty_and_delivery_failure_require_operator`: the agent reports
  `operator_reconciliation_required` and records no reconciliation request.

#### FR-12 Audit

Events are append-only, with a sanitized export.

Direct tests:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_audit_is_sanitized_and_append_only`: the export omits receipt, resource and
  request content, contains the expected event types, and a `DELETE` on events is
  rejected.
- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_audit_export_keeps_one_snapshot_during_reconciliation`: an export that
  overlaps a reconciliation describes one consistent snapshot.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_audit_events_cannot_be_rewritten`: an `UPDATE` of any column of an audit
  event is rejected through a plain database connection, and the trail is
  unchanged.

#### FR-13 Account for model usage

Model usage is reserved before a request, settled from reported usage, and the run
stops on uncertainty.

Direct tests:

- [`test_call_budget.py`](../tests/test_call_budget.py)
  `test_reservation_persistence_failure_never_dispatches`: if the reservation cannot
  be stored, no request is sent.
- [`test_comparison.py`](../tests/test_comparison.py)
  `test_counted_reservation_matches_exact_request_and_releases_on_usage`: the
  reservation matches the counted request, and reported usage settles it to the
  exact cost with nothing left reserved.
- [`test_call_budget.py`](../tests/test_call_budget.py)
  `test_invalid_usage_freezes_with_partial_evidence`: nine kinds of invalid usage
  block further calls and keep the reservation.
- [`test_call_budget_recovery.py`](../tests/test_call_budget_recovery.py)
  `test_observed_assumption_overrun_freezes_future_calls`: usage over the output
  cap, an unrecognized tier or an input overrun blocks further reservations.
- [`test_comparison_recovery.py`](../tests/test_comparison_recovery.py)
  `test_100_identical_usage_settlements_are_idempotent_and_conflict_fences`: one
  hundred identical settlements account once, and a conflicting one stops the run.

## Planned requirements (not implemented)

These are not implemented. No code or test exists for them as stated.

#### FR-14 Acceptance

Verify delivery against the quote and record an accept or reject decision with
evidence. The mechanism is an open decision: record-only, or escrow that releases
payment on acceptance.

Related existing behavior, which is not this requirement: the offline x402 fixture
path records an explicit operator-invoked delivery acceptance for an already
settled purchase ([phase3b4.md](phase3b4.md)).

#### FR-15 Multi-step purchasing

Deferred until a refund or escrow path exists. Today a failed delivery never
refunds a payment and never triggers a new charge.

## Non-functional requirements

#### NFR-1 At-most-once effects

Each financial effect happens at most once per purchase within retained,
consistent local state.

Direct tests:

- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_100_simultaneous_retries_one_charge` and
  `test_cancellation_and_execution_share_authoritative_fence`: one charge under one
  hundred simultaneous attempts; a racing cancel and execute agree on one result.
- [`test_x402_gate.py`](../tests/test_x402_gate.py)
  `test_100_concurrent_invocations_have_one_owner_and_no_replacement`: one signer
  call and one dispatch for one hundred invocations.
- [`test_x402_merchant.py`](../tests/test_x402_merchant.py)
  `test_100_concurrent_paid_replays_one_settlement_and_one_cache_row`: one
  settlement row for one hundred paid replays.
- [`test_x402_delivery.py`](../tests/test_x402_delivery.py)
  `test_100_overlapping_reads_commit_one_acceptance_without_execution`: one
  acceptance record for one hundred overlapping reads, with no new execution.
- [`test_offline_buyer.py`](../tests/test_offline_buyer.py)
  `test_100_overlapping_purchase_calls_single_execution`: one execution for one
  hundred overlapping purchase calls.

Limit: this is not a claim of exactly-once payment across lost or rolled-back
databases, several hosts, or a real chain. See [handoff.md](handoff.md).

#### NFR-2 Fail-closed behavior

When state cannot be stored, evidence is uncertain or a limit is reached, the
system stops and keeps the exposure. It does not continue or retry.

Direct tests:

- [`test_agent_durability.py`](../tests/test_agent_durability.py)
  `test_persistence_failure_prevents_provider_dispatch`: a metadata write failure
  stops the run before any model request.
- [`test_x402_gate.py`](../tests/test_x402_gate.py)
  `test_claim_persistence_failure_prevents_signing_and_rolls_back_reservation`: a
  claim that cannot be stored prevents signing and dispatch.
- [`test_call_budget.py`](../tests/test_call_budget.py)
  `test_invalid_usage_freezes_with_partial_evidence`: invalid usage blocks later
  calls.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_tool_and_turn_limits_fail_closed`: limits end the run.
- [`test_runtime_guard.py`](../tests/test_runtime_guard.py)
  `test_unverified_runtime_cannot_be_substituted`: a runtime whose evidence does not
  match is refused.
- [`test_agent_live_contract.py`](../tests/test_agent_live_contract.py)
  `test_live_requires_explicit_activation_model_and_credentials`: a live client
  cannot be built without explicit mode, model and credentials.
- [`test_comparison_live.py`](../tests/test_comparison_live.py)
  `test_nested_json_credential_reflection_stops_before_retention`,
  `test_prefixed_credential_missed_by_sanitizer_stops_before_retention` and
  `test_decoded_rejection_leaves_no_evidence_observation_or_tool_call`: a reflected
  credential stops the run before evidence, observation or a tool call.

#### NFR-3 Network isolation

Offline work makes no network connection and needs no credentials.

No direct test. No pytest test asserts isolation. It is enforced in two other ways:

- [`conftest.py`](../tests/conftest.py) has an automatic fixture that fails any test
  which opens an IPv4 or IPv6 connection or resolves a host name.
- [`scripts/validate_offline.py`](../scripts/validate_offline.py) refuses to run
  the suite unless it is inside a loopback-only network namespace, and checks that
  subprocesses inherit it. The retained validator logs record that check.

#### NFR-4 Deterministic time

Time-dependent decisions use an injected clock, so results are reproducible.

Direct tests, for expiry decisions following the injected clock:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_policy_denials` (cases `quote_expired`, `grant_expired`): advancing the
  injected clock, not wall time, causes the denial.
- [`test_provider_cli.py`](../tests/test_provider_cli.py)
  `test_cli_workflow_and_repeatable_demo`: a repeated demo returns identical output.

No direct test: of `FixedClock` itself (for example, that it cannot move
backwards), or that no code path reads wall-clock time for a financial decision.

#### NFR-5 Audit integrity

Audit records cannot be removed, expose no secrets or payloads, and are read as one
consistent snapshot.

Direct tests:

- [`test_authorization.py`](../tests/test_authorization.py)
  `test_audit_is_sanitized_and_append_only`: deletion is rejected and the export is
  sanitized.
- [`test_authorization.py`](../tests/test_authorization.py)
  `test_audit_events_cannot_be_rewritten`: rewriting an event is rejected.
- [`test_concurrency.py`](../tests/test_concurrency.py)
  `test_audit_export_keeps_one_snapshot_during_reconciliation`: one snapshot under
  a concurrent write.
- [`test_x402_merchant_recovery.py`](../tests/test_x402_merchant_recovery.py)
  `test_audit_uses_one_snapshot_during_concurrent_cache_expiry`: one snapshot under
  concurrent cache expiry.
- [`test_x402_gate.py`](../tests/test_x402_gate.py)
  `test_no_signature_or_replayable_payload_in_db_wal_audit_or_logs`: no signature or
  replayable payload in the database, its write-ahead log, reports or logs.

Limit: audit triggers are not tamper-proof against someone with filesystem access.

### Latency

Added 2026-11-16. These requirements bound how long a run may take and state what
must be measured. No latency figure has been measured yet.

- **Measurement boundary:** task start to run result returned.
- **Principle:** latency optimizations must never weaken authorization, financial
  correctness, idempotency or durable state guarantees, and must never introduce
  retries.

#### NFR-L1 Bounded wall time

Every model and tool call has a timeout, and every task has an overall deadline.
Wall time is bounded by the deadline plus the maximum duration of one in-flight
step. No new step starts after the deadline. An in-flight payment call is never
silently abandoned: if it is cut off, its outcome is recorded as unknown and the
reservation stays held. On expiry the run stops fail-closed, without retry.

The bound is "deadline plus one step", not "deadline", on purpose. Abandoning an
in-flight payment call is worse than waiting for it: the outcome becomes unknown,
and treating the call as stopped would be a correctness bug. The bound is
enforceable only if every blocking step has its own limit, so lock waits have to be
set from the remaining deadline and not fixed.

What exists today:

| Path | Overall deadline | Model call timeout | Tool call timeout |
| --- | --- | --- | --- |
| Buyer agent run (`run_buyer`), simulator and offline x402 | `Settings.timeout_seconds` around the model and tool loop. Default 30 s, maximum 120 s. | Live client: the same value, zero retries. Scripted or injected model: none of its own. | None of its own. |
| Evaluation campaign, mock pilot, live pilot | The same run deadline, set from the envelope limit (at most 30 s). | Client timeout from the same limit. The live pilot transport also sets it on each HTTP phase. | None of its own. |
| Paired comparison episode | `limits.timeout_seconds` (1 to 30 s) around the episode's requests and tool calls. | Client timeout, a per-request deadline on the live exchange, and 30 s per HTTP phase on the provider wire. | None of its own. |
| Comparison preflight | None over the whole preflight. | 30 s per request, on the client, the exchange and the wire. | No tools. |
| Generation probe, mock and live | None over the whole probe. | One deadline per provider request, from the probe limit. | No tools are executed. |
| Simulator purchase without the agent (CLI `purchase`, the demo) | None. | No model. | Not applicable. |

Two operator-invoked calls that are neither model nor tool calls are also bounded:
x402 evidence observation and delivery retrieval each have a 1 to 30 s limit
(default 5 s).

Direct tests:

- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_timeout_before_purchase_and_fabricated_success`: a model call that does not
  return ends the run as `timeout`, and nothing is purchased.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_failure_after_purchase_keeps_authoritative_state` (case `sleep_response`):
  a deadline that expires after a purchase leaves it settled and delivered, with
  one charge and usage marked incomplete.
- [`test_buyer_agent.py`](../tests/test_buyer_agent.py)
  `test_input_and_settings_bounds`: a run timeout over 120 s is rejected.
- [`test_agent_live_contract.py`](../tests/test_agent_live_contract.py)
  `test_live_transport_is_bounded_and_ignores_proxy_and_base_url`: the live client
  carries the configured timeout and zero retries.
- [`test_call_budget.py`](../tests/test_call_budget.py)
  `test_cooperative_timeout_holds_uncertain_request`: on expiry the model-usage
  reservation stays held, further calls are blocked, and nothing is charged.
- [`test_pilot_preparation.py`](../tests/test_pilot_preparation.py)
  `test_timeout_retains_reservation`: the same for the mock pilot.
- [`test_campaign_recovery.py`](../tests/test_campaign_recovery.py)
  `test_timeout_and_late_cancelled_response_keep_unknown_usage`: one request is
  sent, never a second, the campaign stops, and usage stays unknown.
- [`test_comparison_recovery.py`](../tests/test_comparison_recovery.py)
  `test_timeout_retains_generation_hold_and_no_retry`: one generation request, a
  stopped comparison, and the full reservation still held.
- [`test_comparison_live.py`](../tests/test_comparison_live.py)
  `test_live_timeout_keeps_pending_attempt_and_stops_other_arm`: the same through
  the guarded transport, and the other provider's arm does not start.
- [`test_offline_buyer_recovery.py`](../tests/test_offline_buyer_recovery.py)
  `test_late_response_cannot_claim_or_sign` (cases `timeout`): a deadline that
  expires inside a tool call or a model call ends the run as `timeout`; a late
  result cannot claim, sign or reserve.
- [`test_offline_buyer_recovery.py`](../tests/test_offline_buyer_recovery.py)
  `test_timeout_before_challenge_does_not_require_later_checkpoint`: a deadline
  before the first response completes the run record as `timeout`.
- [`test_comparison_preflight.py`](../tests/test_comparison_preflight.py)
  `test_exact_native_preflight_requests_headers_admission_and_no_generation`: each
  outbound preflight request carries the 30 s timeout on every HTTP phase.
- [`test_agent_durability.py`](../tests/test_agent_durability.py)
  `test_cancellation_cleanup_is_best_effort_and_bounded`: cleanup after a
  cancelled run returns within two seconds when the client close stalls or the
  store is locked.
- [`test_x402_evidence.py`](../tests/test_x402_evidence.py)
  `test_cooperative_timeout_keeps_reservation` and
  [`test_x402_delivery_recovery.py`](../tests/test_x402_delivery_recovery.py)
  `test_cooperative_timeout_records_failure_and_rejects_late_result`: the two
  operator-invoked calls time out, keep the reservation or the settled accounting,
  and reject a late result.

For the payment-call clause, with a scripted adapter outcome and not an elapsed
deadline:

- [`test_recovery.py`](../tests/test_recovery.py)
  `test_timeout_after_charge_is_reserved_until_reconciled` and
  `test_unknown_requires_authoritative_resolution_and_fences_late_payment`: a
  payment call that returns no usable answer, after or before the charge, is
  recorded as `unknown` and the reservation stays held.

In the offline x402 path,
`test_cancellation_during_signing_keeps_claim_and_fences_late_signer` covers a
cancellation during signing, not a deadline.

Blocking steps inside the deadline, since `f05a74d`. The agent run and the
comparison episode declare their deadline to the storage layer
(`blocking_deadline` in `storage.py`). Inside it:

- Every connection opened through `Database` waits for a lock at most the time
  left before the deadline. This covers the ledger, the simulated payment store,
  the simulator's merchant store and the agent metadata store.
- A purchase checks the deadline before the reservation, before dispatch and
  before delivery. A step refused this way raises `DeadlineExpired`. The agent run
  ends as `timeout`.
- Once dispatch is recorded, the payment call and the write of its result always
  run to the end. This is the one in-flight step. After the deadline each of its
  database calls waits at most 1 second for a lock
  (`PAYMENT_STEP_LOCK_WAIT`).
- A purchase that settles after the deadline is not delivered in that run. It is
  settled and needs operator reconciliation, which delivers without a second
  charge.

Direct tests, all in [`test_blocking_deadline.py`](../tests/test_blocking_deadline.py)
with a scripted deadline clock unless stated:

- `test_lock_wait_is_the_time_left_before_the_deadline`: the lock wait is the
  time left, a nested deadline cannot extend it, and the in-flight step keeps
  its 1-second wait.
- `test_expired_deadline_does_not_wait_for_a_held_lock`: with a real lock held,
  a write after the deadline fails at once and reserves nothing.
- `test_purchase_does_not_start_after_the_deadline` and
  `test_deadline_after_reservation_stops_before_dispatch`: nothing is
  dispatched; a reservation already made stays held until the operator releases
  it.
- `test_dispatched_payment_is_finished_and_recorded_after_the_deadline`: the
  deadline passes before, during or after the payment call. The payment is
  settled and recorded once, delivery is not started, and reconciliation
  delivers without a second charge.
- `test_deadline_after_settlement_does_not_start_delivery` and
  `test_lost_payment_response_after_the_deadline_is_recorded_unknown`.
- `test_run_ends_as_timeout_when_the_deadline_passes_inside_a_purchase`: the
  agent report says `timeout` and that reconciliation is required.
- `test_stalled_simulator_tool_call_ends_the_run_as_timeout`: a stalled quote or
  delivery, in real time, ends the run as `timeout`.

Expiry of timeouts that were only configured before, in
[`test_timeout_expiry.py`](../tests/test_timeout_expiry.py):

- `test_mock_probe_request_deadline_expires_and_keeps_the_hold` and
  `test_live_probe_request_deadline_expires_without_retry`: the generation
  probe's per-request deadline expires during the generation request. The probe
  stops, the hold is kept, and the second provider is not called. The live case
  uses the live adapter with the network replaced.
- `test_pilot_wire_timeout_keeps_the_reservation_and_blocks_the_pilot`: the
  transport sets the limit on every HTTP phase, and a timeout raised by the HTTP
  layer keeps the reservation and blocks the pilot. The timeout is raised by the
  test; no socket timeout is exercised.
- `test_preflight_deadline_expiry_is_recorded_as_timeout` (two cases): each of
  the two preflight 30 s deadlines expires while the request is in flight. The
  preflight stops, keeps the attempt as an unknown outcome, and records the
  diagnostic stage `transport` with reason `timeout`.
- `test_preflight_deadline_expiry_during_the_body_is_recorded_as_timeout` (two
  cases): the same two deadlines expire while the response body is being read.
  The recorded stage is `body` and the reason is `timeout`.

Where the requirement is not met:

- **No tool call has its own timeout.** `list_services`, `get_quote`, `purchase`
  and `get_purchase_status` are bounded only by the run or episode deadline. The
  in-process merchant client used by `get_quote` and `purchase` sets no timeout.
- **The payment adapter call is still synchronous.** The deadline cannot
  interrupt it. Its lock waits are bounded as described above, but work inside an
  adapter that is not a lock wait has no limit of its own.
- **Paths without a declared deadline keep the fixed 30-second lock wait:** a
  purchase made without the agent, operator reconciliation, and setup and
  finalization around a run.
- **In the comparison episode, a lock wait cut short by the deadline ends the
  episode as `interrupted`, not `timeout`.** Only a refused step is reported as
  `timeout` there.
- **The deadline does not cover the whole measurement boundary.** In `run_buyer`,
  the catalog read and the start record happen before it starts; the final report,
  the finish record and the client close happen after it. The close is limited to
  0.5 s and the metadata store to a 0.1 s lock wait; the catalog read uses the
  ledger's 30-second lock wait.
- **The deadline is per run, not per task.** A task that is run again gets a new
  deadline. No cumulative deadline exists for a task. This can wait: replay
  returns the existing result, so a second run creates no new financial exposure.
- **A purchase made without the agent has no deadline.**
- **Scripted and injected models have no per-call timeout.** They run in process;
  only the run deadline bounds them.
- **The comparison journal and the generation probe still store the wire code
  `cancelled` when their own deadlines expire.** `fe54a55` corrected this for
  the preflight diagnostic only. No test asserts the code in the other two.

#### NFR-L2 Application overhead budget

Non-model time per task (database, authorization, simulator) stays under 500 ms.
This is a hypothesis to be measured, not a verified bound.

No direct test. No test measures non-model time, and no code records it separately
from model time.

One informal observation suggests the budget is not safe on slow storage. On the
development machine on 2026-11-16, a synced SQLite commit took 33 to 41 ms, and
the two cases of `test_failure_after_purchase_keeps_authoritative_state`, which run
a scripted purchase under a 1-second run deadline, failed there and took over two
seconds each. Hosted CI passed the same tests. The cause is not confirmed, and this
is not a measurement of the budget.

#### NFR-L3 Model latency is measured, not guaranteed

Live runs record total task time, per-call model duration, number of sequential
model calls, tokens and outcome. Results are reported as median and maximum over
the scenario set. The threshold belongs in the provider decision rule.

Direct tests, for the paired comparison runner only. They use a scripted clock, so
they assert which spans are recorded, not how long anything takes:

- [`test_comparison_latency.py`](../tests/test_comparison_latency.py)
  `test_call_durations_exclude_host_time_and_are_summarised`: each episode result
  stores one record per count and generation request. A record's duration covers
  the send up to the last byte of the response body. Journal writes and tool calls
  are in the task time and not in any call's duration. The report gives the median
  and maximum, overall and per provider.
- [`test_comparison_latency.py`](../tests/test_comparison_latency.py)
  `test_call_cut_off_by_the_deadline_is_recorded_as_a_lower_bound`: a call that
  the episode deadline cuts off is kept, marked as having no complete body, and
  counted in the report.
- [`test_comparison_latency.py`](../tests/test_comparison_latency.py)
  `test_live_call_duration_excludes_the_dispatch_claim_and_status_commits`: through
  the guarded transport, with the network replaced, the duration excludes the
  dispatch claim before the send and the status commit after it. A dispatch that
  is refused before the send is not recorded as a call.
- [`test_comparison_latency.py`](../tests/test_comparison_latency.py)
  `test_results_without_call_timing_are_counted_not_guessed`: results stored
  before call timing existed are counted as not measured.

What existing live evidence and the current code record:

| Field | Published live evidence | Recorded by current code |
| --- | --- | --- |
| Total task time | Missing. The bundled pilot snapshot and [luna-baseline.md](luna-baseline.md) contain no timing. | Partly. The campaign runner stores `latency_ms` per entry and the comparison runner stores `latency_seconds` with a `latency_scope`. Both include host tools and persistence. The agent report, the live pilot and the generation probe record none. |
| Per-call model duration | Missing. | Comparison runner only: `model_calls` in each episode result, one record per count and generation request, with `kind`, `seconds` and `body_read`. Missing in the agent report, the campaign runner, the live pilot and the generation probe. |
| Number of sequential model calls | Recorded. The pilot snapshot lists each request by ordinal; the baseline reports request counts. | Recorded (`requests_started`, `responses_received`). The comparison report also gives generation calls per episode. |
| Tokens | Recorded, per request and per scenario in the pilot snapshot, and in total in the baseline. | Recorded. |
| Outcome | Recorded (`termination`, `decision`, payment and delivery state). | Recorded. |
| Median and maximum over the scenario set | Missing. | Comparison report only: its `latency` block gives count, median and maximum of task time, generation call duration, count call duration and generation calls per episode, overall and per provider. |
| Threshold in the provider decision rule | Missing. The rule is not written. | Missing. |

The comparison's `latency_seconds` is narrower than the measurement boundary above.
It starts after the episode is claimed and its fixture is opened, and it ends
before the result is stored. It therefore leaves out the source and state
verification that claims the episode, and the final result commit. It includes
the harness's closing checks. Task time minus the call durations is the
comparison's non-model time, but that includes the comparison journal's own
writes, so it is not the NFR-L2 figure.

The report's median and maximum cover every episode that sent at least one model
call, including one that stopped early. A call without a complete body is a lower
bound, and the report counts such calls in `unread_calls`.

The published live evidence read here is the bundled pilot snapshot
(`pilot_viewer/pilot-v1.json`), [luna-baseline.md](luna-baseline.md) and
[phase3b8.md](phase3b8.md). The original campaign and pilot databases were not
opened, so whether the Luna campaign's stored entries carry `latency_ms` is not
confirmed. No paired comparison generation has been run against a provider, so
`latency_seconds` and `model_calls` exist only for mock runs.

#### Out of scope for now

- **P99 targets:** the sample size is too small for a tail percentile to mean
  anything.
- **Throughput and concurrency targets:** a run has a single owner process.
- **Acknowledgment latency:** there is no asynchronous acceptance to acknowledge.
- **Reconciliation latency:** reconciliation is operator-invoked and is a
  correctness property, not a speed target.
- **Delivery latency:** delivery is simulated and synchronous.

#### Lock wait and latency targets

The ledger, the simulated payment store and the simulator's merchant store wait up
to 30 seconds for a database lock. The agent metadata, call budget, comparison and
x402 stores already use short waits of 0.1 to 0.2 seconds.

With one owner process the 30-second wait is not expected to be reached. Since
`f05a74d` it is only an upper limit inside an agent run or a comparison episode:
there each wait is capped by the time left before the deadline (NFR-L1). Outside
a declared deadline the 30 seconds still applies. If concurrent writers are ever
introduced, the lock wait must be revisited together with the latency targets.

## Gaps

| # | Requirement | Gap |
| --- | --- | --- |
| 1 | FR-1 | Closed in `da43583`. Identical resubmission and replacement are both tested. |
| 2 | FR-3 | Closed in `da43583`. The model's view of `list_services` is tested. |
| 3 | FR-3 to FR-6 | Tests use scripted models. Real-model behavior rests on a few bounded runs, and their explanation review is pending. |
| 4 | FR-6 | Closed. The requirement now states the behavior: returned in the report, excluded from storage. |
| 5 | FR-12, NFR-5 | Closed in `da43583` for the simulator ledger. The x402 stores test immutability of their own tables; their audit event tables were not reviewed here. |
| 6 | NFR-3 | No pytest test asserts network isolation. It relies on a fixture and the validator. |
| 7 | NFR-4 | No test of the clock itself, and none that financial decisions never read wall-clock time. |
| 8 | FR-14, FR-15 | Not implemented. FR-14's mechanism is undecided. |
| 9 | NFR-L1 | No tool call has its own timeout, and the in-process merchant client sets none. Only the run or episode deadline bounds a tool call. |
| 10 | NFR-L1 | Closed in `f05a74d` for the agent run and the comparison episode: lock waits are capped by the time left, a purchase starts no new step after the deadline, and tests let the deadline pass during the payment call. Still open: the payment adapter call is synchronous, and paths without a declared deadline keep the 30-second wait. |
| 11 | NFR-L1 | The deadline covers the model and tool loop only, not setup or finalization. It is per run, not per task, and a purchase made without the agent has none. The per-run part can wait, because replay creates no new exposure. |
| 12 | NFR-L1 | Closed in `f05a74d`, with one limit: the pilot transport test raises the HTTP layer's timeout and exercises no socket timeout. The two preflight cases were strict `xfail` until `fe54a55` closed gap 16. |
| 13 | NFR-L2 | Not measured and not tested. No code separates non-model time from model time. |
| 14 | NFR-L3 | Closed in `de0c8d0` for the paired comparison runner, which records per-call model duration and total task time. Still open elsewhere: the agent report, the live pilot and the generation probe record no timing, the campaign runner records no per-call duration, and no published live evidence contains either. |
| 15 | NFR-L3 | Partly closed in `de0c8d0`: the comparison report gives the median and maximum, overall and per provider. Still open: no latency threshold, because the provider decision rule is not written. |
| 16 | NFR-L1 | Closed in `fe54a55`. An expired preflight deadline was recorded with the diagnostic reason `cancelled`, because the shared provider wire reports the deadline as a cancellation and the preflight keeps only its first failure. The preflight now records `timeout` when one of its own deadlines has passed; a real cancellation is still `cancelled`. The shared wire is unchanged, so the comparison journal and the generation probe still store `cancelled` for their own expired deadlines. |

Gaps 1, 2, 4 and 5 are closed and keep their numbers so the others stay stable.
Gap 7 is a small test addition. Gaps 3 and 6 are limits of what a unit test can
show. Gap 8 is planned work. Gaps 9 to 15 were added with the latency requirements
on 2026-11-16. Gap 14 is closed for the comparison runner only, and gap 15 still
lacks its threshold. Gap 16 was found on 2026-11-24, a defect in an existing
diagnostic, and closed the same day in its own change.

Planned order for the latency gaps, as three separate tasks:

1. **Gaps 14 and 15:** record per-call model duration and total task time in the
   comparison runner. Done for the comparison runner in `de0c8d0`. The threshold
   waits for the provider decision rule.
2. **Gaps 10 and 12:** bound the blocking steps by deriving lock waits from the
   remaining deadline, and add expiry tests for the three timeouts that are
   configured but never tested. Done in `f05a74d`, which also found gap 16
   (closed in `fe54a55`).
3. **Gap 9:** per-tool timeouts on the merchant client. The risk is low while the
   merchant is in process. It is mandatory before the x402 phase introduces real
   HTTP.

Gap 13 is measured with the scenario set. Gap 11 is not scheduled.
