# Group held-out acceptance plan

> **For agentic workers:** Execute this evaluation inline; the user has approved continuing with previously unseen tasks and unchanged production configuration. This is evaluation work, not a new scheduling feature.

**Goal:** Measure whether the current on-demand Group can finish new distributed-evidence tasks without application-driven synthesis handoffs or prompt tuning after observing results.

**Architecture:** Reuse the existing traced live-provider fixture. Freeze one generic coordinator/evidence-owner protocol, three task definitions, independent semantic oracles, and two runs per task before the first provider call. The application issues one initial request per invocation; subsequent requests must come from real members.

**Tech Stack:** Python, pytest, existing `.env` provider, SQLite Group store, existing read-only evidence tools.

**Spec:** The user's approved follow-up to the 2026-09-26 acceptance: retain the baseline and evaluate new tasks without tuning configuration or prompts to the results.

## Global constraints

- Preserve all pre-existing tracked and untracked changes; do not commit or tag.
- Keep production prompts, strategy, runtime, provider settings, and existing fixtures unchanged.
- Reuse the fixture's 2,048-token output cap, 10 turns per member run, 16 member runs per invocation, and 420-second invocation deadline. Do not force compaction in this evaluation.
- Evidence is private to its owner; expected answers are visible only to the evaluator.
- Use one unchanged collaboration protocol for all tasks and repetitions.
- Record all six outcomes, including failures. No result-dependent prompt edits or selective passing retries.
- If a runtime defect is found, preserve the frozen evaluation result, reproduce it separately, and verify any correction with deterministic regression coverage.
- Do not equate an idle runtime, a linked reply, or a member's success claim with a correct task result.

## Tasks and predeclared acceptance

1. **Access recertification:** Join private identities, requested resources, and access policy. Evaluate active status, group membership, MFA, and explicit-deny precedence for six requests. Return every decision and all three original document references.
2. **Delivery allocation:** Join orders, stock, and carrier/service restrictions. Apply a strict priority order, avoid consuming stock for infeasible shipments, allocate whole orders only, and select the cheapest qualifying carrier. Return every order's status/carrier and exact remaining stock.
3. **Release dependency analysis:** Join a component dependency graph, compatibility constraints, and versioned release decisions. Compute transitive prerequisites, detect conflicts only within the selected closure, prefer the latest authorized decision, and reject an apparently newer unapproved note. Return the selected target, closure, conflicts, and deployability for both requested releases.

All tasks also require actual private evidence reads, attributable public reports from all owners visible before final publication, a final artifact from the assigned coordinator, no unresolved linked replies or failed member runs, and no new inference on a subsequent passive post. Each task runs twice with distinct source identifiers. These six observations describe only this fixed local-provider evaluation, not a statistical reliability estimate.

## Execution checklist

- [x] Inspect baseline and existing fixtures; define scope and success criteria.
- [x] Add `tests/group_heldout_cases.py` with immutable task data and independent validation.
- [x] Add `tests/test_group_heldout_oracles.py` to reject missing/duplicate/wrong results and verify the frozen expected results.
- [x] Add `tests/test_group_live_heldout.py` with one generic protocol, one external request, and two repetitions per task.
- [x] Capture source/config fingerprints and run oracle checks before any provider call. Twenty-eight checks passed; independent review also recalculated all expected answers.
- [x] Execute all six cases once, retain traces and results, and classify every failure without retuning: four passes, two delivery-reasoning failures.
- [x] Run offline regression checks and confirm the production/config fingerprints remain unchanged: 1,126 passed, 12 skipped; 99 files unchanged.
- [x] Write `docs/superpowers/reviews/2026-09-28-group-heldout-acceptance.md` with results, limitations, and the next concrete recommendation.
