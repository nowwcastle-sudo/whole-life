<!--
Keep this pull request small: one verifiable change.
CONTRIBUTING.md asks for four things. They are the four headings below.
-->

## What changed

<!-- The change itself, not the motivation. -->

## Why it was necessary

<!-- The concrete current requirement. Link the issue or ADR if one exists. -->

## How it was verified

<!--
Name the failing reproduction you wrote first and what makes it pass now.
For a safety-relevant change, state the defect you injected and confirm the test failed with it.
A green suite is not sufficient unless its scope covers the changed contract.
-->

## What remains unverified

<!--
Say it plainly. "Nothing" is an acceptable answer only if it is true.
Anything you could not execute, could not reproduce, or assumed goes here.
-->

---

- [ ] This is one verifiable change, not several bundled together.
- [ ] No credentials, session material, real prompts, runtime databases, or machine-specific logs are included.
- [ ] No dependency, abstraction, provider, remote service, or write capability was added without a concrete current requirement and an ADR.
- [ ] If a security or state-machine invariant changed, the normative specification and the ADR were updated in this pull request.
- [ ] This is not a public-release or provider-policy decision (out of scope until the gate in CONTEXT.md passes).
