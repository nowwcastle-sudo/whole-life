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

- **The Codex stream survives the `collab_tool_call` item a collaboration-enabled turn can
  emit** (#51).
  The wire item is real in the CLI's `exec_events` source but absent from its SDK typings,
  so the closed item-type set read it as schema drift and failed every turn that produced
  it. The item is now mapped to the tool-use activity events, behind an empty-set guard: a
  call whose receivers and agent states are both empty passes through as tool use, while
  one that names receivers or agent states — evidence of a worker the delegation counter
  cannot count — still fails the stream closed, so the claim's scope stays the measured
  scope.
- **Conformance evidence that says "refuses an untrusted directory" only when that refusal
  was observed, from a probe that cannot outlive its bound** (#62).
  The Codex collector concluded "refuses" from a nonzero exit alone: the first stderr line
  was rendered but never checked, so a changed CLI flag or a broken install would have
  committed the same sentence as evidence. The verdict is now identified — a nonzero exit
  counts as the refusal only when the observed first stderr line is the pinned
  trusted-directory refusal, any other failure stops collection instead of committing a
  conclusion, and an accepting build is still recordable. The probe's documentation also
  claimed it submits no prompt while its code submitted one; the docstring now tells the
  truth, and the run is bounded like the Claude-side bare probe — on expiry the whole
  process tree is terminated and collection stops, so the probe cannot become an unbounded
  hang or leave a model request running behind a tidy report.
- **The launch decision records the directory the child ran in** (#63).
  Once the working directory became part of what actually spawns (#61), two runs identical in
  provider, executable and arguments but different in their input snapshot root journalled
  identically — an auditor could not tell which data the child could have read. The recorded
  decision now carries the directory the spawn used, derived from the spawned plan like every
  other field, so the decision object keeps its promise of recording exactly what started. The
  plan's maybe-unset intent is not recorded: a directory that is undecided or not absolute is
  refused before spawn, and the journal holds starts.
- **A working directory that is not absolute is refused before any child starts** (#61).
  The operating system resolves a relative path against the Broker's own current directory at
  spawn time, so accepting one would let the Broker's launch location — not the plan — decide
  where the child lands. On Windows the refusal covers more than the plain "relative" reading:
  drive-less rooted paths like `/Windows` and drive-relative paths like `C:foo` also resolve
  against the current drive or directory, and both are refused. A directory that does not
  exist is refused at the same boundary, so it arrives as a pre-start refusal like every other
  decision checked there rather than as an operating-system error from the spawn itself.
- **`close` reports what it could not reap instead of claiming a clean shutdown** (#46).
  When the fallback direct kill timed out, its result was discarded: a child that survived
  the wait came back in the same shape as a clean close, so "nothing left" was claimed while
  a process could still be running. `close()` now returns a `CloseReport` — the counts of
  child processes and drain tasks still standing, and the reasons the code observed, where a
  missing killer and an unreaped direct kill are separate entries — and reports zero only
  when zero was actually confirmed.
- **Native-worker limits that are reported honestly and held by the broker** (#17).
  Each provider now reports concurrency, total starts and delegation depth separately as
  `hard`, `cooperative` or `unsupported`, and reports them as measured. Claude Code 2.1.240
  announces every worker start and finish with a provider-issued identifier and a spawn depth,
  so its limits are observable — and none of them is refused by the provider, including depth,
  which the specification had recorded as `hard` until a recorded turn ran a worker two levels
  deep and refused nothing. Codex reports `unsupported` on every axis because its delegation
  measurement has not been made: the live attempt hit a subscription usage limit before the
  model ran, so what its stream says about workers is unknown rather than known-absent. A
  runtime in that state does not quietly become a single-agent turn — it does not start.
  Worker starts are counted against the turn's budget, the first start past it cancels the turn
  and leaves it `unknown_outcome`, a worker too deep does the same, and a turn is not completed
  while a worker it launched has no announced end. What a plan says about its own capability is
  not evidence: the pre-spawn gate resolves the row from the measurement table and compares it
  whole, so a claimed row, a row missing an axis, and an empty row are all refused.
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

- **A run that ended unknown for two reasons says both, not only the first one found** (#45).
  A cancellation before the terminal event, or a process exit that was never observed, used
  to be the whole diagnostic even when the run's native worker was also never resolved — and
  the dropped fact was always the worker one, a worker that is billable and may still be
  running. Those two rows now append `NativeWorkerUnresolved` to the first reason, and a
  caller-supplied cancel diagnostic — the delegation-budget case — keeps its own wording as
  the first component instead of being flattened into the generic cancellation wording. The
  status is unchanged in every row, and rows where only one reason holds report exactly what
  they always have.
- Python CI runs on a Windows runner (#14 follow-up). The safety invariants this project
  enforces are Windows behaviours; verifying them on Linux would prove something else.

### Fixed

- **Codex children run in the directory the plan chose, not wherever the broker happened to be** (#33).
  Codex child processes inherited the broker's own current directory, which the pinned CLI
  refuses when it is not a trusted workspace — so the broker's launch location, not the
  plan, decided whether a turn could start. The plan's working directory is now a required
  argument wired through both adapters, checked immediately before spawn — an undecided or
  missing directory is refused before anything starts — and passed to the child explicitly,
  with the git-repository check skipped for Codex where the neutral directory is not a
  repository.
- **Cleanup failure no longer masks the cancellation that asked for it** (#34).
  When escalation to `taskkill` failed, that exception surfaced in place of the
  cancellation's own answer, handing the caller a diagnosis about the broker's environment
  for a run it had itself asked to stop. Cancel now answers `UNKNOWN` — which is exactly
  "the tree's death was not confirmed" — and records the cleanup failure separately as a
  fact about the broker rather than the turn; on the spawn path the original exception
  keeps its type and place with the cleanup note attached; and the fallback kill also reaps
  what it kills, so a child it ended is not counted as still alive at close.
- **A live provider turn that runs to the end** (#32). Two line kinds a real Claude Code turn
  writes on every run — a top-level rate-limit notice and a `system` thinking-token estimate —
  were not in the stream allowlist, and the first unrecognised line ended the run. Nothing about
  this involved delegation: a turn that only says hello died on the rate-limit notice, so no real
  turn had ever finished. Both are now recognised and passed over. Neither becomes one of the
  eight canonical events, neither reaches the Journal, and none of what they carry — limit counts,
  reset times, token estimates — survives normalisation. The evidence is a recorded real turn
  replayed in the suite rather than a fixture someone wrote from memory, because a hand-written
  fixture only ever contains lines its author already knew about, which is exactly how a green
  suite coexisted with an adapter that could not complete a turn. Unknown types and unknown
  `system` subtypes still fail closed: the allowlist was widened, not removed.

- Four boundary defects in preflight and launch, all reproduced from synthetic input before the
  fix (#12 follow-up). The final gate had trusted a caller-supplied `allowlisted` flag — an
  assertion, not evidence — and now compares the whole record against the canonical supported
  version table. The forbidden-variable check compared names case-sensitively, so `openai_api_key`
  reached the child on Windows, where environment variable names are case-insensitive.
- **A prompt handover that leaves nothing running when it fails** (#28). The spawner creates
  the child before it writes the prompt, so a child that had already stopped reading made that
  write raise — and because nothing caught it, the handle never reached the caller and a process
  nobody owned was left behind. A reader that is gone is now an ordinary provider outcome,
  resolved from the child's exit code and stderr, and any other failure after the child exists
  ends its whole process tree before the error propagates. A provider binary that exits at once
  on a rejected flag or a failed sign-in is exactly this path.

### Documentation

- The v0 specification and ADR 0001 are pinned by SHA-256 in
  [`docs/project-context.md`](docs/project-context.md), and CI fails if a pinned document changes
  without its hash being updated.
- [`PRODUCT.md`](PRODUCT.md) records the GUI console product truth and surface design.
- [`CONTEXT.md`](CONTEXT.md) is the domain glossary; [`CONTRIBUTING.md`](CONTRIBUTING.md) is the
  working agreement. Korean mirrors exist for both.
