# Diagnosis of incomplete collaborative review

Date: 2026-09-26

## Conclusion

The first reliability review lacked the complete source requirements in its
actual model input. Review and acceptance then failed to detect that omission.
This is a combination of task-context delivery, review-contract and test-oracle
gaps. No scheduling, persistence or single-Agent execution defect was reproduced.
No production code was changed during this investigation.

The earlier description of variable model review quality was incomplete: the
two full runs used the same initial prompts but the affected members retrieved
different source material. Their actual decision inputs were different.

## Source visibility established from recorded provider inputs

The source brief contains 1,803 characters. Its reliability requirements start
at character offset 1,091. They include the crash after a side-effect commit but
before acknowledgement, acknowledgement timing, durable admission and limits of
the deterministic sizing model.

| Member, before first public analysis | First run | Repeat run |
| --- | --- | --- |
| Alice | Complete source: 1,803 characters | Complete source: 1,803 characters |
| Bob | Complete source: 1,803 characters | Complete source: 1,803 characters |
| Carol | Preview only: 512 characters | Complete source: 1,803 characters |
| Dave | Preview only: 512 characters | Complete source: 1,803 characters |

In the first run, Carol and Dave queried history and read Bob's calculation in
full, but never read the original brief in full. History marked that source
`content_complete=false`. Their publication calls had approximately 4,850 and
4,966 prompt tokens, far below the configured 131,072-token context window.
The missing text was absent from the source projection/retrieval path; there is
no evidence that a model context-window overflow caused this omission.

The repeat run's Carol and Dave explicitly called `group_message` for the brief.
Its full text was present before their public analyses. The provider-input audit
decodes nested JSON and checks exact original content, rather than relying on
an ID citation or a count of retrieval calls.

Relevant implementation:

- `group/dispatch.py:34`: background selection contains the most recent messages,
  not a stable set of task requirements. The probe deliberately set
  `background_messages=2`; the default is eight. A larger recent window delays
  eviction but does not establish a requirement-delivery contract.
- `group/tools.py:109`: history is a preview/index tool. It returns the first 512
  characters and explicitly identifies incomplete content. This behavior worked
  as documented; originals were retained and available through `group_message`.
- `group/runtime.py:357`: complete triggers are mandatory, while background can
  be reduced to fit the input budget. There is currently no separate declaration
  of source material required by this particular delegated task.

Bob's first handoff to Carol said to check calculations "above" and recovery
assumptions. It did not carry the brief's full constraints or its source ID.
Carol's handoff to Dave likewise asked him to check existing claims. The profile
and probe told members to retrieve complete material, but that guidance did
not ensure the necessary document reached either reviewer's model input.

## Controlled model replays

Five fresh local-provider calls reused the first run's input at the point just
before Carol or Dave first published. Model settings and tool schemas were kept
the same. Proposed tool calls were captured, not executed; original Group stores
and sessions were not modified.

| Replay | Changed input | Observed result |
| --- | --- | --- |
| Carol baseline | None | General controls; omitted the required commit/acknowledgement discussion |
| Carol with source | Added only the full original brief to background | Discussed the crash window, acknowledgement after commit, durable admission and model limitations |
| Dave baseline | None | Confirmed the earlier claims without identifying missing requirements |
| Dave with source | Added only the full original brief to background | Still confirmed the earlier claims without identifying the key omissions |
| Dave with source and coverage review | Added a generic instruction to check every original requirement against peer evidence | Identified missing acknowledgement timing, durable-admission discussion and model limitations |

The last instruction did not enumerate those domain-specific answers. It changed
the review objective from checking existing claims to also checking omitted
requirements. These small samples support the two-layer diagnosis; they do not
prove a universal model-compliance guarantee or a completed production fix.

Alice also retained the complete brief in the original run but accepted the
incomplete peer analyses. Reliable source delivery is necessary, but sufficient
review and acceptance criteria remain separate requirements.

## Acceptance blind spots

`ResponseEvidence.satisfied` in `group/scheduling.py:21` establishes a linked
public reply from a successful assignment, or an explicit accepted resolution.
It does not evaluate the reply's business meaning. `GroupRuntime.finish` checks
settlement and required response evidence, leaving application acceptance to
the caller. This distinction is intentional and should remain explicit.

The temporary probe's `evaluate` function checked mathematical answers, expected
assignments and real citation IDs. It did not inspect delivery semantics,
acknowledgement timing or requirement coverage. An offline mutation of the saved
final JSON cleared `controls` and removed `delivery_semantics` and `reason`;
every existing phase check still passed. This experiment changed only an
in-memory copy and used already-established runtime state as a fixture.

The probe's corrected retrieval check also aggregated readers across the Group:
Bob reading the original was enough to satisfy it. It did not establish that
Carol and Dave received the material needed for their own assignments. A source
citation similarly establishes an existing identifier, not full reading or
support for every claim.

## Recommended correction boundaries

1. Let the task/application or strategy declare required source material and
   current constraints separately from optional recent background. Verify that
   the relevant material reaches the assigned member's actual model input.
   Handle budget limits explicitly for required material. Preserve source
   identity and the version of constraints when later requests change them.
2. Make review coverage explicit for tasks that need it: enumerate requirements,
   associate each with supporting evidence, and record missing or disputed items.
   Keep semantic acceptance in the task/application or strategy extension, rather
   than interpreting every ordinary Group post as accepted work.
3. Verify input coverage per assignment and assess the requested outputs, not
   tool-call frequency or Group-wide retrieval alone. Use deterministic checks
   for numerical constraints and explicit review evidence for qualitative ones.
   Missing requirements can lead to a bounded, explicit follow-up request.

These corrections do not require suppressing free public speech or making
passive posts activate peers. Merely increasing the recent-message window or
adding a generic reviewer would not address both observed gaps.

## Artifacts and scope

- First run: `/tmp/mas-complex-collaboration-eeaae3b794/`.
- Repeat run: `/tmp/mas-complex-collaboration-07e8a8f47e/`.
- Diagnostic source: `/tmp/mas_context_diagnosis.py`.
- Visibility assertions, five provider responses and acceptance mutation result:
  `/tmp/mas-context-diagnosis-20260926/`.

The original reports remain unchanged. This investigation diagnoses the content
failure and proposes correction boundaries; it does not claim an implementation
fix, complete live acceptance, or a statistically measured improvement rate.
