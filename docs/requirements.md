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

Status as of 2026-11-15, at `main` commit `ba125c5`.

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

No direct test: that an existing grant cannot be replaced with different contents
(`Ledger.add_grant` raises `PurchaseConflict`).

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

No direct test. No test asserts that the `list_services` tool returns only the
services that meet the grant, the bound quality and the remaining budget
(`Boundary.catalog`). Eligibility is enforced again when a quote is requested, and
that is tested under FR-4.

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

The agent's explanation is untrusted text, kept separate from the application-owned
decision record.

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

Note on wording: the buyer's explanation is returned in the run report, not stored.
The agent store excludes `model_explanation` on purpose, and the first test above
asserts that. If FR-6 should require the explanation to be stored, that is a change
to the current behavior.

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

No direct test: that an `UPDATE` on events is rejected. The schema has a trigger
for it, but only `DELETE` is asserted.

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

## Gaps

| # | Requirement | Gap |
| --- | --- | --- |
| 1 | FR-1 | No test that an existing grant cannot be replaced with different contents. |
| 2 | FR-3 | No test of what `list_services` returns to the model. Eligibility is only tested at quote time. |
| 3 | FR-3 to FR-6 | Tests use scripted models. Real-model behavior rests on a few bounded runs, and their explanation review is pending. |
| 4 | FR-6 | The explanation is returned, not stored. The requirement's wording and the behavior should be reconciled. |
| 5 | FR-12, NFR-5 | Append-only is asserted for `DELETE` only, not `UPDATE`, and only on the simulator ledger. |
| 6 | NFR-3 | No pytest test asserts network isolation. It relies on a fixture and the validator. |
| 7 | NFR-4 | No test of the clock itself, and none that financial decisions never read wall-clock time. |
| 8 | FR-14, FR-15 | Not implemented. FR-14's mechanism is undecided. |

Gaps 1, 2, 5 and 7 are small test additions. Gap 4 is a wording decision. Gaps 3
and 6 are limits of what a unit test can show. Gap 8 is planned work.
