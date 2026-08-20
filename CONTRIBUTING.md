# Contributing to Whole Life

Whole Life is in a documentation-first architecture phase. Contributions should preserve the approved v0 boundary and make one verifiable change at a time.

## Before contributing

1. Read [CONTEXT.md](CONTEXT.md), the [normative v0 specification](docs/spec/whole-life-v0.md), and [ADR 0001](docs/adr/0001-local-subscription-v0.md).
2. Distinguish verified provider behavior from a proposal or inference.
3. Do not add a dependency, abstraction, provider, remote service, or write capability without a concrete current requirement and an ADR.
4. Never include credentials, session material, real user prompts, runtime databases, or machine-specific logs.

## Change standards

- Prefer the Python standard library until a measured requirement proves it insufficient.
- Keep provider differences inside the runtime adapter boundary; do not claim common guarantees that one CLI cannot enforce.
- Write a failing reproduction before fixing behavior.
- Test malformed streams, process interruption, duplicate commands, path traversal, concurrent resume, and crash recovery where relevant.
- For every safety test, inject or mutate the protected defect and confirm that the test fails.
- Update the normative specification and ADR before changing a security or state-machine invariant.

## Pull requests

Keep pull requests small and explain what changed, why it was necessary, how it was verified, and what remains unverified. A green test suite is not sufficient unless its scope covers the changed contract.

Public-release and provider-policy decisions are out of scope for ordinary pull requests until the policy gate in [CONTEXT.md](CONTEXT.md) has passed.
