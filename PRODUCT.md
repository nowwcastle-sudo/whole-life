# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Tauri desktop shell with a plain HTML/CSS/JS frontend - no bundler, no
frontend framework. Chosen by the operator over Electron and a Python
webview host. The Rust toolchain is not yet installed on the development
machine. The dependency requires an ADR before it lands (CONTRIBUTING section 3).

## Users

Someone who already uses Claude Code and Codex CLI separately and holds a
subscription to each. They reach for Whole Life when a piece of work is long
enough that they judge it needs several agents collaborating - not daily, but
for a long sitting once opened.

The graphical surface exists because a command line alone excludes people who
will not adapt to it. The field is drawing newcomers quickly, and the place
they stop is installation and sign-in, not the collaboration itself.

## Product Purpose

Run several Claude and Codex participants inside one shared session using the
operator's own official CLI sign-ins, exchange their answers and critiques at
turn boundaries, and leave a deterministic dossier that preserves originals,
agreements, conflicts, and provenance.

Success is that the operator can see what is happening - and above all, see
the collaboration itself.

## Positioning

Existing agent tools stream events, resume sessions, and invoke subagents. We
are not aware of one that also provides shared cross-provider collaboration
state with consistent replay, process cleanup, context boundaries, and
deterministic output.

## Operating Context

One person on their own Windows machine. Both CLIs are installed and signed in
beforehand; the broker launches them and never reads, copies, or stores their
credentials. The graphical surface is a shell over the command line - every
action it offers is available as a broker command.

The operator's path through the surface: detect and confirm both CLIs are
signed in; choose how many participants and in which roles; set the budget
profile; write the work to be done; run it and watch the session; read the
dossier.

## Capabilities and Constraints

Preserved invariants:

- one writer; one broker process, one SQLite writer connection
- append-only event journal; exactly eight canonical event types
- the broker exposes no service and makes no network calls of its own
- the dossier's eight sections, their order, and byte-for-byte determinism
- supported CLI versions come from a compiled-in allowlist

Terminology is fixed by CONTEXT.md's glossary. A label in the interface that
departs from it is a defect, not a style choice.

Confirmed future goal - NOT v0, NOT yet specified: a live table that
subdivides session records across many rows and columns and updates while the
session is running, so participants' work is visible as it happens rather than
only at turn boundaries. The agreed shape is a projection over the append-only
journal, not direct writes by each agent. A v1 specification will be
established through the full engineering process after v0 is complete and
BEFORE graphical work begins.

Undecided: whether participants may read each other's in-progress work. That
is mid-turn context push, which v0 excludes by design.

## Brand Commitments

Name: Whole Life. Documentation and the interface are bilingual, English and
Korean.

Binding visual constraints given by the operator:

- The gruvbox colour scheme is the reference for the palette.
- Nothing too sharp.
- Movement - button presses, modals, popups, window transitions - is smooth.

Ruled out by the operator:

- The enterprise SaaS dashboard look.
- Terminal or hacker aesthetics. This excludes gruvbox's own literal reading
  as a terminal colour scheme: the palette is taken, the costume is not.

No logo or typeface has been chosen.

## Evidence on Hand

- docs/spec/whole-life-v0.md - approved normative design, hash-baselined
- docs/adr/0001-local-subscription-v0.md - reasoning, hash-baselined
- docs/conformance/claude-2.1.239.md and codex-0.149.0.md - measured CLI evidence
