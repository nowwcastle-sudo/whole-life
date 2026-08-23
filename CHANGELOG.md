# Changelog

*English · [한국어](CHANGELOG.ko.md)*

Notable changes to this repository are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project will follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) once there is something to version.

**Nothing has been released.** There is no installable application and no runnable broker
yet — see [Status](README.md#getting-started). What exists is the approved design and the
runtime seam being built beneath it, so every entry sits under `Unreleased` until v0 ships.

Each entry says what the system now does or refuses, not which files moved. The linked
issue holds the acceptance criteria; the merge commit holds the reasoning.

## [Unreleased]

### Added

- **A runtime contract, and a safety gate that runs immediately before any process starts** (#11).
  The `AgentRuntime` protocol from specification §4 is checked at runtime, so a participant that
  quietly omits an operation is rejected rather than silently no-op. Every assembled launch plan
  passes one boundary before it becomes a process.
- **Subscription authentication preflight for Claude Code and Codex CLI** (#12).
  The broker asks each CLI whether it is already signed in; it never reads, copies, or stores a
  credential. Child processes are built from an inherit allowlist rather than the parent
  environment, and five forbidden variables are refused outright.
- **A bare-mode gate for Claude Code, enforced at the final pre-spawn check** (#13).
  `--bare` changes where Claude Code takes authentication from, which would break the
  subscription premise of v0. Three independent controls — argv, final child environment, and a
  measured `bare_default` — must all agree before a turn starts.
- **Provider turns that start through a fail-closed boundary** (#14).
  Executables resolve to direct PE images, prompts stay on UTF-8 stdin and never reach argv,
  read-only argv is preserved on both new and resumed turns, and a second concurrent start
  against the same native session is refused before spawn. Windows `.cmd` launchers are rejected
  because command-processor reparsing was measured to break the approved split-argv boundary.
- **Bounded stream observation with a two-witness outcome** (#15).
  `stdout` and `stderr` drain concurrently under a bound, and only official provider shapes
  normalise into canonical events. A malformed schema, invalid UTF-8, an oversize line, a
  terminal mismatch, or a consumer that stops early all fail closed and leave no child, reader,
  or transport behind.
- **Cancel, turn timeout, and close that end a run's whole process tree** (#16).
  A cancel closes stdin, waits, and then terminates the Windows process tree — killing only the
  launched process leaves the CLI's own workers alive and still billing a turn nobody is waiting
  for. The killer is resolved under `SYSTEMROOT` rather than from `PATH`. Cancellation and a
  terminal event resolve by whichever arrives first, and the hard turn timeout is fixed at the
  start of the turn so a continuously streaming run cannot postpone it forever.

### Changed

- Python CI runs on a Windows runner (#14 follow-up). The safety invariants this project
  enforces are Windows behaviours; verifying them on Linux would prove something else.

### Fixed

- Four boundary defects in preflight and launch, all reproduced from synthetic input before the
  fix (#12 follow-up). The final gate had trusted a caller-supplied `allowlisted` flag — an
  assertion, not evidence — and now compares the whole record against the canonical supported
  version table. The forbidden-variable check compared names case-sensitively, so `openai_api_key`
  reached the child on Windows, where environment variable names are case-insensitive.

### Documentation

- The v0 specification and ADR 0001 are pinned by SHA-256 in
  [`docs/project-context.md`](docs/project-context.md), and CI fails if a pinned document changes
  without its hash being updated.
- [`PRODUCT.md`](PRODUCT.md) records the GUI console product truth and surface design.
- [`CONTEXT.md`](CONTEXT.md) is the domain glossary; [`CONTRIBUTING.md`](CONTRIBUTING.md) is the
  working agreement. Korean mirrors exist for both.
