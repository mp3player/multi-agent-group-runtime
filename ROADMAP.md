# MAS Target Architecture

This document describes how MAS should be designed if built from the beginning
as a formal, maintainable multi-agent runtime. It is a target architecture
reference, not a migration checklist.

## Design Goal

MAS is a local multi-agent runtime where persistent agents collaborate through
structured group communication.

The core product is not a Web UI or a single chat wrapper. The core product is
the runtime model:

- each agent has its own session, tools, prompt, and provider config
- group chat provides shared transcript, shared memory, dispatch policy, and
  collaboration tools
- dispatch remains model-directed where possible, but the runtime provides
  enough structure to avoid duplicate work, stale replies, and runaway
  broadcast loops
- CLI and Web are thin interfaces over the same application layer

## Architectural Layers

### Domain Layer

The domain layer contains pure data structures and lightweight behavior.

It should not import runtime, tools, LLM clients, CLI, Web, or environment
configuration.

Expected responsibilities:

- group messages, members, turns, events, memory, and stats models
- stable render/to_dict helpers when they are pure and dependency-free
- enums or literal types for message kind, member status, dispatch mode, and
  tool side-effect classes

### Agent Runtime

The agent runtime owns single-agent execution.

Expected responsibilities:

- session history and active context management
- ReAct loop execution
- LLM client integration
- tool registry access
- timeout and cancellation behavior
- usage accounting hooks

The agent runtime should not know about group dispatch internals. A group
member is just an agent wrapped by group-specific prompt and tools.

### Group Runtime

The group runtime owns multi-agent collaboration.

Expected responsibilities:

- group transcript storage and pruning
- member store and lifecycle
- shared group memory
- event log and scheduling stats
- dispatch loop
- dispatch policy selection
- synthetic PASS behavior
- member turn execution through narrow runtime ports
- group tool runtime APIs

`GroupChat` should be a facade. It should expose a stable public API while
delegating implementation details to runtime components.

### Dispatch Policy Layer

Dispatch policy is a pluggable strategy boundary.

Expected responsibilities:

- decide which messages are unread for a member
- decide which unread messages are dispatchable
- decide whether a member should receive a real model turn or synthetic PASS
- decide the next member to run
- interpret directed, broadcast, feedback, and first-responder semantics

The dispatch policy should not run tools, mutate sessions, write events, or
record stats. The runtime loop owns side effects.

### Tool Layer

Tools should be declared and implemented with explicit side-effect boundaries.

Recommended structure:

- tool specs: names, descriptions, schemas, and side-effect classes
- tool handlers: implementation logic
- registry binding: attach/detach tools to an agent

Group tools and ordinary workspace tools should follow the same conceptual
shape, even if their implementations differ.

Side-effect classes should be available for future auditing and permissions:

- read-only
- memory-only
- propagating
- non-propagating
- workspace-mutating
- external-effect

### Prompt Layer

Prompt composition should be explicit and modular.

Recommended modules:

- base agent identity and behavior
- operating workflow
- tool protocol
- skill protocol
- communication style
- group chat basics
- group collaboration behavior
- group tool usage
- dispatch policy hint
- group memory rules
- member identity and role

Group prompt modules should only be injected when an agent becomes a group
member. Ordinary single-agent operation should not contain group-specific
rules.

Prompt content should be tested against runtime/tool declarations so tool names
and dispatch policy descriptions do not drift from code.

### Application Layer

The application layer wires runtime components together.

Expected responsibilities:

- parse config from `.env`, CLI args, and member config files
- build LLM clients
- build tool registries
- build system prompts
- build agents
- build groups
- add configured group members

Entrypoints should depend on this layer instead of constructing core runtime
objects directly.

### Interfaces

CLI and Web should be thin adapters.

Expected responsibilities:

- accept user input
- call application/runtime APIs
- render messages, status, stats, and errors
- avoid direct knowledge of low-level group internals

CLI and Web should not import each other.

## Core Data Flow

### Single Agent

1. User input enters an agent.
2. The agent builds active context from system prompt and session.
3. The LLM may call tools through the registry.
4. Tool results are appended to the session.
5. The agent returns a final response.
6. Usage and session history are updated.

### Group Chat

1. A user message is appended to the group transcript.
2. The dispatch loop asks the active policy for the next eligible member.
3. The member receives only dispatchable unread group messages plus bounded
   recent context.
4. The member may call ordinary tools or group tools.
5. Propagating group tool messages create more dispatch opportunities.
6. Non-propagating PASS records that the member saw the messages but has
   nothing material to add.
7. Directed messages wake only their target members; other members receive
   synthetic PASS when appropriate.
8. Group memory records durable collaboration state but does not notify members
   by itself.

## Configuration Model

Configuration should have one clear source of truth after parsing.

Recommended config objects:

- LLM provider config
- agent runtime config
- group runtime config
- member config
- prompt config
- tool config
- skills config
- logging and usage config

`.env`, CLI flags, and JSON member files are input formats. Runtime components
should receive typed config rather than reading environment variables directly.

## Observability Model

MAS should separate diagnostic surfaces by purpose.

- transcript: user-visible group conversation
- session history: private per-agent audit trail
- event log: internal group events
- stats: aggregate dispatch and PASS metrics
- usage records: provider token and cost accounting
- debug logs: operational troubleshooting

CLI and Web should read these through stable view/query functions rather than
serializing internal objects directly.

## Design Risks And Attention Points

The target architecture is intentionally cleaner than the current codebase.
When applying it to the existing project, these areas need special care.

### Runtime Boundary Risk

`GroupChat` should be a facade, but the current implementation still carries
meaningful coordination logic. Moving too much at once can easily change
dispatch behavior.

Risk controls:

- migrate one responsibility at a time
- keep public APIs stable
- keep behavior tests close to every runtime move
- avoid "cleanup" commits that also change scheduling semantics

### Dispatch Policy Risk

Dispatch policy is the most important extension point. If the policy interface
becomes too broad, policies will become another runtime implementation. If it
becomes too narrow, new policies will require core edits.

Risk controls:

- policies should decide visibility and eligibility only
- runtime should own model calls, tool calls, events, stats, and mutation
- tests should cover each policy with the same user-message, directed-message,
  broadcast, feedback, PASS, stale-response, and failure scenarios

### Prompt And Runtime Drift

The group behavior depends on both prompt rules and runtime dispatch rules. If
tool names, dispatch semantics, or memory rules change in code but not prompt,
agents will behave inconsistently.

Risk controls:

- keep group prompt modules separate from common agent prompt modules
- test that declared group tools appear in the group tool prompt
- keep dispatch policy hints short and policy-specific
- avoid exposing implementation details such as synthetic PASS internals to the
  model unless the model must act on them

### Synthetic PASS Trace Risk

Synthetic PASS is useful because non-targeted members can record that they saw
directed messages without running the LLM. It is also delicate because it writes
session history and group transcript entries while pretending to be a normal
tool path.

Risk controls:

- synthetic PASS should use the same tool-call trace shape as real PASS
- synthetic PASS should not count as a real member turn
- synthetic PASS should not trigger more dispatch
- tests should inspect both transcript messages and private session traces

### Read Cursor And Pruning Risk

The group currently relies on scalar read cursors per member. This is simple,
but it makes message pruning and directed/broadcast interleaving sensitive.

Risk controls:

- only mark contiguous unread prefixes as read automatically
- never skip older broadcast work when synthetic-passing newer directed work
- keep transcript pruning separate from member read semantics
- add tests before changing message id, pruning, or read cursor behavior

### Config Drift Risk

The project currently has several configuration sources: `.env`, CLI flags,
member JSON files, default constants, and runtime constructors. If these remain
scattered, behavior becomes hard to reproduce.

Risk controls:

- parse external config once into typed config objects
- keep compatibility with existing `.env` names
- document precedence between defaults, env, CLI args, and member config files
- keep runtime components free from direct environment reads where practical

### Tool Side-Effect Risk

Group tools already have side-effect classes. Ordinary workspace tools may need
the same structure later. Without consistent side-effect metadata, future
permissions and audits will be harder to add safely.

Risk controls:

- classify all tools by side-effect type
- keep specs separate from handlers
- keep registry binding separate from implementation
- treat workspace-mutating and external-effect tools as high-risk even before a
  formal permission system exists

### Async And Cancellation Risk

Group dispatch has async member execution and single-flight constraints.
Cancellation, timeout, and failed member cleanup can leave agents marked
running or active contexts dirty if handled inconsistently.

Risk controls:

- always reset member status and active context on completion, failure, and
  cancellation
- test timeout cleanup and failed member continuation
- avoid sharing mutable per-turn state across concurrent member tasks
- keep async dispatch wakeups observable through events or tests

### Interface Boundary Risk

The Web UI is intentionally a thin shell, but it can still accidentally grow
business logic by serializing internal state directly or constructing runtime
objects itself.

Risk controls:

- keep CLI and Web dependent on a common application/factory layer
- expose stable view/query helpers for messages, members, stats, and usage
- prevent CLI and Web from importing each other
- do not let UI needs redefine core runtime semantics

### Public Facade Risk

Public facades are useful when they own a stable boundary. Thin compatibility
modules are useful during migration, but should be removed once callers move to
the target architecture.

Risk controls:

- keep public facades limited to stable API ownership
- test that runtime code does not depend on removed migration paths
- remove compatibility exports once callers have migrated

## Testing Strategy

Tests should protect both behavior and architecture.

Behavior tests:

- single-agent ReAct flow
- tool call flow
- session trimming
- group first-responder behavior
- directed dispatch
- synthetic PASS
- broadcast feedback
- stale response guard
- member add/remove lifecycle
- timeout cleanup

Architecture tests:

- domain does not import core
- CLI and Web do not import each other
- runtime code does not depend on removed migration paths
- prompt references cover declared group tools
- dispatch policy registry and strategy injection work
- public exports remain stable

## Non-Goals For The Core Runtime

These may be future product work, but they should not be required for the core
runtime design:

- hosted SaaS productization
- multi-user authentication
- persistent database-backed group recovery
- vector memory
- full permission policy engine
- Web UI polish
- provider-specific orchestration assumptions

The core runtime should stay local-first, inspectable, and replaceable.

## Current Implementation Status

This section records the current implementation state so the target design does
not drift from the codebase.

Implemented:

- application services now build and coordinate CLI/Web runtime objects
- prompt runtime backs `SystemBuilder` while preserving the public facade
- agent runtime and group runtime are split into internal runtime components
- group dispatch policies are split into a formal policy package
- group tool handlers and LLM helpers are split behind formal runtime packages
- legacy core/application compatibility facades have been removed after migration
- workspace and group tools expose side-effect metadata
- tool permission/audit v1 records calls with an allow-all default policy, and
  can optionally enforce configured deny/approval rules
- observability views provide stable read-only message, member, usage, stats,
  event, and tool audit views

Not implemented:

- interactive tool approval
- database-backed audit querying or group recovery
- Web audit UI or production Web productization
- multi-user isolation, authentication, or hosted SaaS concerns
- vector memory, LLM summarization, or long-running recovery orchestration
