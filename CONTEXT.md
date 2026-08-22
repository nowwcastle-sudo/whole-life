# Whole Life implementation context

## Current status

The architecture is approved for a private, documentation-only repository. No implementation code or dependency manifest exists yet.

**Visibility history.** This repository was switched from private to public at approximately 2026-08-22 04:16 UTC to unblock a desktop client clone, and the owner reverted it to private the same hour (confirmed private again at 2026-08-22 04:57 UTC). Commit `4e38cee` and its author metadata were publicly fetchable during that window. Any later statement that this repository "has never been public" is false.

## Approved baseline

| Artifact | SHA-256 | Lines | Note |
|---|---|---:|---|
| `docs/spec/whole-life-v0.md` | `A48270E57CE1D106910A8C50443331B6973ED7F1746A6362FF8008988EF835F0` | 574 | 2026-08-20 audited baseline |
| `docs/spec/whole-life-v0.md` | `E8C6EA089A16A99106CF1C3C85D454FAF0F885E9091BD7E1CFB7EABCCE4E691C` | 607 | 2026-08-22 revision — §5 bare mode gate, §12 authentication conformance, §13 F-16 |
| `docs/adr/0001-local-subscription-v0.md` | `123BAFBDE315B7BA237E25731F78AF1E56BFBBD6A872CD2CD3F5FBC06BAE10C5` | 91 | unchanged |

The audited baseline row is kept so the 2026-08-20 audit provenance stays verifiable. The revision row is the current file.

Hashes are taken over the stored Git blob (LF line endings), reproducible with `git show HEAD:<path> | sha256sum`. Do not hash the working-tree file on a checkout with `core.autocrlf=true` — it will differ.

The original 709-line research document is intentionally not copied into this repository. It remains non-normative source material with SHA-256 `C243DB1EF4C27662872C17E85350883C5663943D4C1F13F1D6D3F977643F831F`.

Independent Codex and Claude Fable 5 audits applied architecture-improvement, code-review, grilling, brainstorming, and Ponytail/YAGNI criteria. The final targeted Claude review found no remaining P0/P1 findings, no reopened F-1–F-15 findings, and passed the private-repository design gate.

## Non-negotiable v0 invariants

1. The broker is a single local Python process.
2. Only two adapter implementations exist: Claude Code and Codex CLI.
3. Agent runtimes are read-only. Only the broker commits artifacts.
4. SQLite has one async command queue and one writer connection.
5. Runtime state stays under `%LOCALAPPDATA%\WholeLife`, never in a synced or network path.
6. Authentication is checked in the exact sanitized child environment and fails closed.
7. Unknown CLI versions, unsupported delegation observation, ambiguous process outcomes, and concurrent native-session resumes fail closed.
8. Full participant answers never enter another participant prompt; only bounded, schema-validated handoff capsules do.
9. Provider-reported token fields are nullable telemetry, not subscription quota or billing.
10. The dossier is deterministic and preserves conflicts instead of asking another model to synthesize them away.

## Keep v0 small

Do not add FastAPI, A2A, AG-UI, CloudEvents, Redis, NATS, Kafka, ULID packages, a transactional outbox, a dynamic adapter registry, a web UI, or a generalized storage interface. Add a seam only after a second real implementation or process makes the existing concrete module leak complexity to callers.

Use Python's standard library first. The first modules with enough depth to justify their boundary are the broker, runtime adapter seam, concrete journal, artifact committer, and dossier exporter.

## Implementation order

1. Pin and test safe argv/auth/version preflight for both real CLIs.
2. Implement process lifecycle, bounded stdout/stderr drain, cancellation, and Windows process-tree cleanup.
3. Implement canonical events and single-writer SQLite replay.
4. Implement broker profile scheduling and capsule validation.
5. Implement no-overwrite artifact commits and deterministic dossier export.

Every step starts from the corresponding conformance failures in the normative specification. Tests are evidence only after a deliberate mutation of the protected invariant makes them fail.

## Public-release gate

| # | Gate | Status |
|---:|---|---|
| 1 | Recheck current OpenAI and Anthropic documentation, or obtain written confirmation, for a local tool in which the operator runs their own officially authenticated CLI | **Owner-accepted 2026-08-22.** Documentation was reviewed and the owner accepted the residual risk. This is the owner's judgement, not a written confirmation from either provider; no provider has been asked. |
| 2 | Verify actual Claude and Codex subscription usage attribution with a smoke test | **Designed, not executed.** Procedure and pass criteria are in [gate 2 smoke test](docs/smoke/gate-2-usage-attribution.md). Execution is deferred until a runnable testbed exists. |
| 3 | Confirm that credentials, session tokens, user settings, runtime databases, logs, and local audit material are absent from Git history | **Verified 2026-08-22.** Every blob in every commit of the full history was scanned; no matches. |
| 4 | Keep README claims limited to verified behavior; do not describe planned subscription support as policy approval | **Applied 2026-08-22.** Five overstated or stale claims were corrected. |

Gate 2 is the one still open. Until it passes, no document in this repository may state that provider metering or billing behaves in a particular way.

While reviewing gate 1, one design-level risk surfaced that is not a policy question: Claude Code's `--bare` mode authenticates strictly from an API key and never from OAuth, and it is documented to become the default for `-p` in a future release. The specification now gates on this in §5; see [F-16](docs/spec/whole-life-v0.md).
