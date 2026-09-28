# Delivery failure attribution

Date: 2026-09-28

## Finding

The two recorded delivery failures originate in the configured model endpoint's
generated decisions. The observed errors are reproducible without running the
MAS Agent, Group scheduler, tool executor, response parser, or persistence path.
The evidence does not identify an implementation defect in those components as
the cause of these failures.

This conclusion concerns the current model and serving configuration on this
task. It does not isolate model weights from inference-server configuration or
establish the model's ability on all tasks. A bounded acceptance validator could
still improve application reliability by rejecting incorrect generated plans.

## Recorded failure audit

Both final model inputs contain the complete initial task, including the rule
that unshipped orders must have null carriers, and all three complete private
documents after their owners published them. The original model inputs contain
4,373 and 4,363 provider-reported prompt tokens. The responses finish normally
with `tool_calls`, using 361 and 367 completion tokens against a 2,048-token cap.
No compaction took place. Tool call/result IDs are paired correctly throughout
the recorded conversation.

The published incorrect artifact text is byte-for-byte the content supplied in
the raw provider response's `group_post` arguments. Review of the actual client
and publishing paths confirms that response parsing and SQLite publication do
not calculate or replace inventory, carrier, or fee values.

An independent computation from the recorded source documents reproduced the
predeclared oracle. Stock evolves as `8 -> 4 -> 4 -> 1 -> 1 -> 1 -> 0`; orders
O1, O3, and O6 ship, with fees `8 + 4 + 5 = 17`. The original two model artifacts
remain invalid under those source rules.

## Raw HTTP controls

Four diagnostic requests were made directly with `httpx.post` to the same local
provider using the existing `.env` settings, temperature 0.7, and maximum output
2,048. Only configuration loading and evaluator helpers were reused. No MAS
runtime executed the requests or their returned tool calls. Credentials and
endpoint URLs were not included in diagnostic artifacts.

For each original failed case, two controls were specified:

1. **Exact request:** Reuse the recorded messages and tool schemas, with the
   runtime's original `tool_choice=auto` and sampling settings. Validate the
   returned `group_post` content directly, without executing any tool.
2. **Flat task:** Supply the unchanged task and authoritative documents in one
   user message with a minimal JSON-output system instruction. Omit Group
   history, reception records, scheduling instructions, and all tool schemas.
   No expected answer or corrective hint is supplied.

| Original case | Control | Prompt tokens | Completion tokens | Result |
| --- | --- | ---: | ---: | --- |
| Delivery 1 | Exact request | 4,373 | 361 | Same incorrect artifact as original |
| Delivery 1 | Flat task | 666 | 307 | Incorrect artifact |
| Delivery 2 | Exact request | 4,363 | 367 | Same incorrect artifact as original |
| Delivery 2 | Flat task | 672 | 313 | Incorrect artifact |

All four requests returned normally. Both flat requests marked O6 out of stock
despite one available unit and one required unit, attached carriers to unshipped
orders, and returned remaining stock one / fee twelve. Their responses finished
with `stop`; no output was truncated. Both exact-request artifacts equal their
corresponding original artifacts, including the second original's impossible
shipment of O4 when only one unit remained.

These controls strengthen attribution to model-side reasoning and instruction
adherence. They also show that the failure can occur in a short request with no
Group machinery or synthetic reception history. The flat control changes input
presentation and removes tools, so it does not measure the individual causal
effect of each prompt component. Four controls are diagnostic observations, not
a model benchmark or evidence that a specific intervention will work.

## Repeated release requests

The extra requests in the earlier release trial were also present in the raw
coordinator response. Each stored request matches an actual model-issued
`group_request`. All eleven member assignments reference distinct scheduling
opportunities. The two repeated evidence-owner runs come from a new explicit
request, not repeated execution of one opportunity. Structured pending-work
coordination could constrain this behavior, but no duplicate-dispatch defect was
found in this trace.

## Verification and artifacts

- Independent source-derived computation matches both original expected plans.
- Both original task/source envelopes and tool-call pairing were verified.
- Four direct-provider controls completed; all four artifacts failed the frozen
  semantic oracle. Exact-request artifacts equal their original counterparts.
- Twenty-eight evaluator integrity checks passed again in 0.07 seconds.
- All 99 frozen source/configuration/test files remain unchanged.
- No production fixes or prompt retuning were made during this attribution work.

The original held-out outcome remains **four passes and two failures**. These
four later diagnostics are separate and do not replace or inflate that result.

[Machine-readable attribution evidence](2026-09-28-group-failure-attribution-results.json)
retains the four actual artifacts, independent stock computations, and release
request audit. The standalone diagnostic script, raw request/response files, and
logs are under `/tmp/mas-attribution-20260928-0jt3jyz4/`; that temporary directory
may be removed by system cleanup.
