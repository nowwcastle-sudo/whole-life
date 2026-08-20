# Whole Life implementation context

## Current status

The architecture is approved for a private, documentation-only repository. No implementation code or dependency manifest exists yet. Public distribution remains blocked by provider-policy and usage-attribution gates.

## Approved baseline

| Artifact | SHA-256 | Lines |
|---|---|---:|
| `docs/spec/whole-life-v0.md` | `A48270E57CE1D106910A8C50443331B6973ED7F1746A6362FF8008988EF835F0` | 574 |
| `docs/adr/0001-local-subscription-v0.md` | `123BAFBDE315B7BA237E25731F78AF1E56BFBBD6A872CD2CD3F5FBC06BAE10C5` | 91 |

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

Before considering public visibility:

- Recheck current OpenAI and Anthropic documentation or obtain written confirmation for a local tool in which every user runs their own officially authenticated CLI.
- Verify actual Claude and Codex subscription usage attribution with a smoke test.
- Confirm that credentials, session tokens, user settings, runtime databases, logs, and local audit material are absent from Git history.
- Keep README claims limited to verified behavior; do not describe planned subscription support as policy approval.
