# Offline evaluation catalog, version 1

This catalog organizes the **seven already executable cases** in the existing offline
evaluator. It is coverage metadata, not a new model evaluator, scoring framework or
execution plan. The evaluator does not load this file. Changing a catalog record
cannot select code, change a fixture, authorize a purchase or change pass/fail logic.

The [JSONL catalog](cases/offline-v1.jsonl) has one object per case. Its generated
[record schema](schemas/case-v1.schema.json) and the strict Python
[validator](../src/agentic_payments_lab/buyer_agent/eval_catalog.py) use existing locked
dependencies. No new runtime, network transport or credentials are required.

## Suite map and linked state

| Suite / sequence group | Order | Stable case ID | Existing coverage |
| --- | ---: | --- | --- |
| `offline_x402` / `x402_lifecycle` | 1 | `new_unknown` | An injected execution leaves the fixture purchase unknown and reserved. |
| Same sequence | 2 | `replay_unknown` | Status replay retains identity without another double execution. |
| Same sequence | 3 | `operator_delivered` | Explicit operator fixture reconciliation and delivery acceptance precede agent status. |
| Same sequence | 4 | `incident_visible` | Contradictory fixture evidence exposes an incident without reversing accepted history. |
| `responses_mock_over_simulator` / `simulator_wire_lifecycle` | 1 | `wire_paid` | The scripted Responses mock acquires a simulated service. |
| Same sequence | 2 | `wire_replay` | The same settled simulated purchase is inspected without another charge. |
| Same sequence | 3 | `wire_missing_usage` | Missing mocked usage preserves settled payment/delivery and blocks further requests. |

These are **two separate linked sequences, not seven independent fresh purchases**.
The x402 sequence owns one fresh fixture root and one purchase. The Responses-mock
sequence owns separate simulator state under `responses-mock/` and one shared purchase
and call-budget batch. Each complete evaluator invocation requires a new, nonexistent
root directory. Never point it at retained pilot, viewer or interrupted-run state.

The first fixture classification is `scripted_x402_asgi_doubles`: scripted SDK buyer,
in-process ASGI merchant, signer/dispatcher/settlement doubles and synthetic public
evidence. The second is `scripted_responses_mock_simulator`: scripted Responses over
an in-memory HTTP mock, with simulator payment records and mocked API usage.

Authoritative execution and scoring remain in the existing
[x402 evaluator](../src/agentic_payments_lab/buyer_agent/evaluation.py) and
[Responses-mock evaluator](../src/agentic_payments_lab/buyer_agent/wire_evaluation.py).
The catalog contains check **identifiers**, not amounts, thresholds, expected financial
values, executable expressions, import paths or policy definitions. Descriptions are
short explanatory text. Fixture/suite/group references are allowlisted symbolic names;
they are never resolved with `eval`, dynamic imports or record-supplied paths.

## Validation contract

Every record requires `version`, `case_id`, `suite`, `sequence_group`,
`sequence_order`, `description`, `fixture` and `expected_checks`. Version 1 is strict:
unknown fields and unsupported versions/identifiers are rejected. Expected check IDs
are unique; their order is descriptive and does not define execution order.

The generated JSON Schema uses draft 2020-12 and describes **one record's structure**:
required fields, object shape, primitive types, enum allowlists, number/string/array
bounds and unique check IDs. It does not describe an entire JSONL file. It contains no
external references that need network resolution. The Python loader additionally
requires:

- UTF-8 JSON objects, exactly one per nonblank line, with an optional final newline.
  CRLF is accepted. Empty files, blank records and duplicate JSON keys at any depth
  are rejected, as are malformed/nonfinite JSON values.
- A file at most 32 KiB, records at most 4 KiB, and exactly seven records. File reads
  consume at most the file limit plus one overflow byte before validation.
- Exactly the known case IDs, in the table's contiguous group/order sequence, with
  no missing, extra or duplicate case. Each ID is bound to its known suite, group,
  order and fixture classification. Checking independent record schemas alone does
  not establish these cross-record or cross-field relationships.
- The exact check-ID set for that case's suite, not merely any allowlisted check.
  Descriptions must be nonblank and have no leading/trailing whitespace.
- Strict integer versions/orders: booleans, strings and decimal numeric forms such
  as `1.0` are rejected. JSON Schema treats mathematically integral numbers such as
  `1.0` as integers, so this last distinction is enforced only by Python.

Use `load_catalog(Path(...))` with an explicit trusted host path, or `decode_catalog`
with bounded bytes. Neither imports nor invokes an evaluator. Successful decoding
returns immutable case records in an immutable tuple. Loader errors expose one fixed
message, without echoing rejected values, source paths or parser exceptions.

The [existing evaluation regression](../tests/test_offline_evaluation.py) compares
catalog case IDs and expected check sets with the **actual output of the invocation
it already executes**. It does not add another full evaluation. Existing ledger,
authority, tool-trace, false-narrative and duplicate-execution assertions remain in
place. The [catalog tests](../tests/test_eval_catalog.py) cover record/schema drift,
negative parsing and semantics, bounded reads and import/load without execution code.
The JSON Schema tests use the `jsonschema` package already present in the existing
lockfile; no dependency was added.

## Run offline

Use the verified project runtime and install locked dependencies as described in the
[repository validation instructions](../README.md#validation). Do not source `.env`.
The full validator runs both test files and the unchanged executable evaluator, under
OS-level isolation including subprocesses:

```bash
parent_netns=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net -- \
  .runtime/venv/bin/python -B scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

For a standalone existing evaluation artifact, create only a disposable **parent**;
the evaluator creates its own new child directory:

```bash
evaluation_parent=$(mktemp -d /tmp/payments-evaluation.XXXXXX)
parent_netns=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net -- \
  .runtime/venv/bin/python -B scripts/evaluate_offline.py \
  --parent-netns "$parent_netns" --state-dir "$evaluation_parent/run"
```

The existing evaluator writes `evaluation.json` and its disposable databases there.
The top-level `cases` array contains the x402 sequence; `wire.cases` contains the
Responses-mock sequence. Overall success also includes the wire restart fence;
the catalog does not replace that top-level check or change any pass semantics.
A catalog-only import/read is inert; the evaluator and full validator remain the
explicit execution entrypoints. `uv --offline` alone is not runtime isolation.

The schema is generated by `case_json_schema()`; do not hand-edit it. A schema-drift
regression compares the stored object to that function's output. To regenerate after
an explicitly reviewed record-model change, use the verified Python under OS isolation
and serialize `case_json_schema()` with `json.dumps(..., indent=2, sort_keys=True)` plus
a final newline to `evals/schemas/case-v1.schema.json`.

## Evidence limits

Scores read quiescent buyer/authority/metadata records and durable tool events; model
prose cannot earn a pass. Separate explicit read snapshots do not imply cross-file
atomicity. Double-call counts and synthetic transfers are not blockchain charges.
Mocked Responses usage and scripted choices prove neither real-model decision quality,
provider billing nor live integration readiness. Missing usage remains unknown and
retains its planning reservation; settled payment and delivery remain authoritative.

This metadata phase adds no evaluator behavior, scoring model, automatic recovery,
backend switch, live calls or payment controls. Original stopped evidence/reservations
and the successful pilot/viewer snapshot remain separate and untouched. The verified
runtime requirement and existing live-integration blocks are unchanged.
