# Whole Life

*English · [한국어](README.ko.md)*

Whole Life specifies a Windows-first, local orchestration layer for collaboration between multiple Claude Code and Codex CLI participants, driven by the operator's own single official CLI sign-in on the operator's own machine. It is a single-user tool: it never relays, pools, or shares a sign-in between people.

> **Status:** architecture approved; the first runtime safety seam is implemented. There is no runnable broker yet.

The v0 design keeps the provider set deliberately small—Claude and Codex—while allowing a collaboration session to contain 2–8 first-class participants. Each active participant may delegate bounded, read-only work to provider-native subagents. A single local broker owns scheduling, state, and artifact writes.

## Getting started

There is no installable application yet. The repository now contains the approved design and the first Python runtime seam, but not a runnable broker.

**For repository development,** use Python 3.14 as declared in [`.python-version`](.python-version), then run the complete current suite from the repository root:

```powershell
python -B -m unittest discover -t . -s tests
```

**To read the design,** start with the [v0 specification](docs/spec/whole-life-v0.md), then [ADR 0001](docs/adr/0001-local-subscription-v0.md) for the reasoning behind it. [`docs/project-context.md`](docs/project-context.md) records the approved baseline, the SHA-256 hash of each pinned document, and the current status of every public-release gate. [`CONTEXT.md`](CONTEXT.md) is the domain glossary — the vocabulary the specification uses normatively.

**When v0 ships,** the intended shape is a single local command that starts the broker on your own machine. You sign in to Claude Code and Codex CLI yourself, exactly as you already do. The broker launches the CLIs already installed on the machine and reads their output streams; keeping subscription credentials out of its execution path is a design requirement checked at every launch, not a permanent property of the CLIs — see [v0 boundaries](#v0-boundaries).

The **broker** makes no network calls of its own: it does not use a provider API, expose a service, or reach another machine. The CLIs it launches are cloud-backed and do talk to their own providers — exactly as they do when you run them by hand. The boundary is that the broker adds no remote surface, not that the session is offline.

## Technology

| Layer | Choice |
|---|---|
| Runtime | One local Python process (`whole_life` package), Windows-first; macOS and Linux come after v0 |
| Concurrency | `asyncio`; subprocesses are spawned with `shell=False` and a split argv |
| State | SQLite with a single writer connection, replayed from an append-only event journal |
| Providers | Claude Code CLI (`stream-json`) and Codex CLI (`JSONL`), each behind a runtime adapter |
| Prompt transport | UTF-8 on stdin, never a command-line argument |
| Dependencies | Python standard library by default; anything beyond it needs a measured requirement and an ADR |

Supported CLI versions are pinned to a compiled-in allowlist. An unrecognised version refuses to start with `UnsupportedCliVersion` rather than assuming the flags and stream format still mean the same thing.

## Why this project exists

Current agent tools can stream events, resume sessions, and invoke subagents. We are not aware of one that also provides a shared cross-provider collaboration state with consistent replay, process cleanup, context boundaries, and deterministic output. Whole Life specifies that local control layer.

## v0 boundaries

- One local Python process on Windows
- Two runtime adapters: Claude Code and Codex CLI
- SQLite with one writer
- Read-only agent runtimes; broker-only artifact commits
- Event observation in real time; AI-to-AI context exchange at turn boundaries
- A deterministic dossier that preserves original answers, critiques, agreements, conflicts, and provenance
- No web UI, remote service, multi-user credential relay, or worktree writing in v0
- No provider API key in the broker's execution path — **conditional on current CLI behavior, not a permanent property.** Claude Code's `--bare` mode reads authentication strictly from `ANTHROPIC_API_KEY` or `apiKeyHelper` and never from OAuth or the keychain, and Anthropic documents that `--bare` will become the default for `-p` in a future release. The broker therefore pins CLI versions, asserts bare mode is off, and fails closed rather than silently switching to an API key (see [specification §5](docs/spec/whole-life-v0.md))

## Token-aware collaboration

Whole Life limits the parts it can actually control: participant turns, native-worker starts, result bytes, capsule bytes, and projection fan-out. It does not pretend to predict subscription quota or billing from token counts.

| Profile | Initial participants | Escalation | Cross-agent exchange |
|---|---:|---|---|
| `economy` | 2 seeds | none | bounded capsules |
| `balanced` | 2 seeds | up to 2 ordered standbys when structured review signals a conflict or evidence gap | bounded capsules |
| `deep` | full roster | none | deterministic cyclic fan-out, never full-answer mesh |

Full answers remain in the dossier and are never injected into another participant's prompt. Only schema-validated `handoff_capsule` objects cross the provider boundary.

## Security and policy boundary

Whole Life must not read, copy, export, or broker Claude or ChatGPT credentials. Official CLI processes authenticate themselves. Unknown authentication, CLI versions, worker observability, or process outcomes fail closed.

Technical feasibility is not the same as provider-policy approval. The public-release gate and its current per-item status — including this repository's visibility history — are tracked in [`docs/project-context.md`](docs/project-context.md). Subscription usage attribution has not been measured yet, so nothing here should be read as a statement about how a provider will meter or bill this usage.

## Canonical documents

- [Whole Life v0 normative specification](docs/spec/whole-life-v0.md)
- [ADR 0001: local subscription v0](docs/adr/0001-local-subscription-v0.md)
- [ADR 0002: close reports a value — CloseReport](docs/adr/0002-close-report-contract.md)
- [Gate 2 usage-attribution smoke test design](docs/smoke/gate-2-usage-attribution.md)
- [Domain glossary](CONTEXT.md)
- [Project context — baseline, invariants, release gate](docs/project-context.md)
- [Agent configuration](AGENTS.md)
- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## How this repository is developed

Work is carried out by a fixed team of eleven AI agents in a dedicated project channel, following an eleven-stage flow with an explicit gate at each stage. The Governor coordinates handoffs through channel mentions, while the durable report lives in `WORK_LOGS`. Pre-existing files are extended, never replaced. The Closer verifies the branch and prepares copy-paste-ready push commands; **the owner performs the push and decides whether to merge into `main`**. Destructive git operations stay with the owner as well.

[`AGENTS.md`](AGENTS.md) tells an agent where this repository keeps its issue tracker and domain docs; the details sit in [`docs/agents/`](docs/agents/). The working agreement — what is already established, what is deliberately absent, and how agents are expected to behave here — is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Development sequence

Implementation begins only after this documentation baseline. The first vertical slices are:

1. Safe Claude and Codex runtime adapters
2. Single-writer SQLite journal and replay
3. Deterministic dossier exporter

Each slice must start with failure-reproduction tests and include mutation checks for its safety invariants.

## License

Apache License 2.0. See [LICENSE](LICENSE).
