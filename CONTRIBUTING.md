# Contributing to Whole Life

*English · [한국어](CONTRIBUTING.ko.md)*

Whole Life is in a documentation-first architecture phase. Contributions should preserve the approved v0 boundary and make one verifiable change at a time.

## Before contributing

1. Read [`docs/project-context.md`](docs/project-context.md), the [normative v0 specification](docs/spec/whole-life-v0.md), and [ADR 0001](docs/adr/0001-local-subscription-v0.md). Use the vocabulary in [`CONTEXT.md`](CONTEXT.md).
2. Distinguish verified provider behavior from a proposal or inference.
3. Do not add a dependency, abstraction, provider, remote service, or write capability without a concrete current requirement and an ADR.
4. Never include credentials, session material, real user prompts, runtime databases, or machine-specific logs.

## Working with the agent team

Work on this repository is carried out by a fixed team of eleven AI agents in a dedicated project channel, following an eleven-stage flow: bootstrap, discovery, mapping, specification, ticket breakdown, implementation, review, a safety gate, verification and merge, release and documentation, then operation. Each stage has an explicit gate, and a stage that has not passed its gate does not advance.

Two rules make the team safe rather than merely busy. The Governor coordinates handoffs through channel mentions, and every completed handoff is recorded first in a durable artifact — an issue, a branch, or a `WORK_LOGS` report — so the trail remains readable afterwards. And the fence is the ordering, not a list of prohibitions: the implementer's work ends at the commit, and the owner does not push until the review and safety gates have run.

Treat the following as **established, not as gaps to fill**:

| Artifact | Status |
|---|---|
| `LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.gitignore` | Established. Extend; do not replace. |
| `docs/spec/whole-life-v0.md`, `docs/adr/0001-*.md` | Approved baseline, pinned by SHA-256 in `docs/project-context.md`. Changing either requires a new ADR and a matching baseline update in the same commit. |
| `CONTEXT.md` | The **domain glossary** and nothing else, per [`docs/agents/domain.md`](docs/agents/domain.md). Extend it when a term is resolved; do not put status, decisions, or implementation detail here. |
| `docs/project-context.md` | Project status, approved baseline, non-negotiable v0 invariants, implementation order, and the public-release gate. |
| CI workflows | Established. Documentation checks and the Python unit-test suite run on pushes to `main` and on pull requests. The Python version comes from `.python-version`. |
| Commit hooks | Deliberately absent. The repository guardrails and CI checks are the current enforcement points. |

Everything the table does not list is filled in progressively, as the flow reaches it. The team does not front-load scaffolding for work that has not started.

**Where each kind of knowledge goes.** The engineering skills assume `CONTEXT.md` is a glossary and nothing else, so that is what it is here. Everything that used to share the file now lives in `docs/project-context.md`. A term belongs in `CONTEXT.md`; a decision belongs in `docs/adr/`; status, baseline hashes, and gates belong in `docs/project-context.md`. If you are unsure which, it is not a term.

**Contribution surface.** This file and `SECURITY.md` exist, but the repository stays private until the gates in the README's *Security and policy boundary* clear. No external triage process is in place, and none is created until distribution is unblocked.

**Push and merge are owner-gated.** The Closer verifies the branch and prepares complete copy-paste-ready push commands. The owner runs the push; after confirmation, the Closer may open the pull request. Merging into `main` is also the owner's decision, made on the evidence in that pull request. Destructive git operations — force push, `reset --hard`, history rewriting, remote branch deletion — are never performed by an agent: it stops and reports instead.

A project-scoped guardrail in `.claude/settings.json` blocks those commands for a session whose project directory is this repository. It does not reach an agent working from another directory, so the rule above is the binding one — the guardrail is a backstop, not the boundary.

## Change standards

- Prefer the Python standard library until a measured requirement proves it insufficient.
- Keep provider differences inside the runtime adapter boundary; do not claim common guarantees that one CLI cannot enforce.
- Write a failing reproduction before fixing behavior.
- Test malformed streams, process interruption, duplicate commands, path traversal, concurrent resume, and crash recovery where relevant.
- For every safety test, inject or mutate the protected defect and confirm that the test fails.
- Update the normative specification and ADR before changing a security or state-machine invariant.

## Pull requests

Keep pull requests small and explain what changed, why it was necessary, how it was verified, and what remains unverified. A green test suite is not sufficient unless its scope covers the changed contract.

Public-release and provider-policy decisions are out of scope for ordinary pull requests until the policy gate in [`docs/project-context.md`](docs/project-context.md) has passed.
