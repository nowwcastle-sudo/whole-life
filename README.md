# Whole Life

Whole Life is a Windows-first, local orchestration project for collaboration between multiple Claude Code and Codex CLI participants using each user's own official CLI sign-in.

> **Status:** architecture approved; documentation only. There is no runnable broker yet.

The v0 design keeps the provider set deliberately small—Claude and Codex—while allowing a collaboration session to contain 2–8 first-class participants. Each active participant may delegate bounded, read-only work to provider-native subagents. A single local broker owns scheduling, state, and artifact writes.

## Why this project exists

Current agent tools can stream events, resume sessions, and invoke subagents, but they do not provide a shared cross-provider collaboration state with consistent replay, process cleanup, context boundaries, and deterministic output. Whole Life specifies that missing local control layer.

## v0 boundaries

- One local Python process on Windows
- Two runtime adapters: Claude Code and Codex CLI
- SQLite with one writer
- Read-only agent runtimes; broker-only artifact commits
- Event observation in real time; AI-to-AI context exchange at turn boundaries
- A deterministic dossier that preserves original answers, critiques, agreements, conflicts, and provenance
- No web UI, remote service, multi-user credential relay, worktree writing, or provider API dependency in v0

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

Technical feasibility is not the same as provider-policy approval. The repository remains private until current OpenAI and Anthropic policy, actual subscription usage attribution, and credential-handling gates have been revalidated.

## Canonical documents

- [Whole Life v0 normative specification](docs/spec/whole-life-v0.md)
- [ADR 0001: local subscription v0](docs/adr/0001-local-subscription-v0.md)
- [Implementation context](CONTEXT.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Development sequence

Implementation begins only after this documentation baseline. The first vertical slices are:

1. Safe Claude and Codex runtime adapters
2. Single-writer SQLite journal and replay
3. Deterministic dossier exporter

Each slice must start with failure-reproduction tests and include mutation checks for its safety invariants.

## License

Apache License 2.0. See [LICENSE](LICENSE).
