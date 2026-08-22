# Whole Life

*English · [한국어](CONTEXT.ko.md)*

The domain glossary for Whole Life — a local broker that runs several official provider CLIs on one machine and turns their separate answers into one deterministic record. Terms here are the ones the [v0 specification](docs/spec/whole-life-v0.md) uses normatively; when writing an issue, a test name, or a proposal, use these words.

Project status, the approved baseline, the v0 invariants, and the public-release gate are **not** here — they live in [`docs/project-context.md`](docs/project-context.md).

## Language

### The process

**Broker**:
The single local Python process that owns scheduling, session state, and every artifact write. Nothing else writes.
_Avoid_: server, orchestrator, daemon, controller

**Runtime adapter**:
The seam behind which one provider CLI is driven. Exactly two exist in v0 — Claude Code and Codex CLI.
_Avoid_: driver, connector, plugin, backend

**Preflight**:
The checks run in the sanitized child environment before a provider process starts — authentication, argv, CLI version. Failure refuses the start.
_Avoid_: healthcheck, validation, warmup

**Bare mode gate**:
The specific preflight check that Claude Code is not authenticating from an API key: no `--bare` in the final argv, no `CLAUDE_CODE_SIMPLE` in the child environment, and the pinned version's `-p` default is not bare.
_Avoid_: API key check, auth guard

### Who does the work

**Participant**:
A first-class collaborating agent in a session's immutable roster, carrying a `participant_id`. Two to eight per session.
_Avoid_: agent (a native worker is also an agent), model, seat, member

**Native worker**:
A provider-native subagent that a participant delegates bounded, read-only work to. It is not a participant and never appears in the roster.
_Avoid_: subagent (ambiguous across providers), child agent, helper

**Roster**:
The fixed set of participants for one session. Immutable once the session starts.
_Avoid_: team, pool, lineup

### Units of work

**Session**:
One Whole Life logical collaboration, carrying a `session_id`. Spans many turns and rounds.
_Avoid_: conversation, thread, job

**Native session**:
The provider's own session, carrying a `native_session_id`. Distinct from a Whole Life session; one Whole Life session drives several.
_Avoid_: provider thread, upstream session

**Turn**:
One participant's execution within a session.
_Avoid_: step, iteration, call

**Round**:
A group of turns executed in parallel against the same projection, carrying a `round_id`.
_Avoid_: batch, wave, cycle

**Run**:
One native process execution, carrying a `run_id`. Null on failures that occur before the provider process starts.
_Avoid_: invocation, execution, process

**Task**:
The logical unit of work created by one `whole-life run` command, carrying a `task_id`. Identical across every event of its session.
_Avoid_: job, request, ticket

### State and output

**Journal**:
The append-only SQLite event log with one command queue and one writer connection. Session state is replayed from it, never stored separately.
_Avoid_: log, database, event store, history

**Event**:
One record in the journal. Exactly eight canonical types exist; anything else is a specification change.
_Avoid_: message, entry, record

**Projection**:
What one participant is shown of the others' work. Bounded by the profile and never a full answer.
_Avoid_: context, prompt context, view

**Handoff capsule**:
The bounded, schema-validated object that crosses the provider boundary. The only thing that does — full answers never enter another participant's prompt.
_Avoid_: context, digest, snippet, summary

**Dossier**:
The deterministic export of a session, preserving original answers, critiques, agreements, conflicts, and provenance. Identical bytes and SHA-256 for the same committed events and exporter version.
_Avoid_: report, transcript, summary, output

### Bounding

**Profile**:
The named budget that bounds a session — `economy`, `balanced`, or `deep`. It caps turns, native workers, and byte sizes; it does not predict subscription quota or billing.
_Avoid_: mode, tier, preset, plan
