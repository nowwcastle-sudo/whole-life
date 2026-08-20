# Security policy

## Project maturity

Whole Life currently contains architecture documentation only. There is no supported runtime release. Security reports about the design and future implementation are still welcome.

## Reporting a vulnerability

Do not open a public issue containing credentials, session identifiers, personal data, exploit details, or machine-specific logs. Report security issues privately through GitHub's private vulnerability reporting or a private security advisory for this repository when available.

Include only the minimum reproducible information. Replace real tokens, account identifiers, and local file contents with synthetic values.

## Security boundaries

Whole Life is designed as a single-user local tool. It must not:

- read, copy, persist, relay, or export Claude or ChatGPT credentials;
- scrape provider web sessions or extract browser tokens;
- execute an agent outside the broker's read-only runtime boundary;
- store SQLite WAL, runtime artifacts, or temporary state in OneDrive, another sync root, a network share, or the repository;
- treat model text as user approval or a verified fact;
- retry an execution whose external outcome is unknown.

The broker may record allowlisted metadata and provider-reported usage fields. Raw environment values, stderr, reasoning traces, and native-worker transcripts are not durable events.

## Supported versions

No CLI version is supported until its authentication, safe arguments, sandbox behavior, stream schema, delegation observation, cancellation, and process cleanup pass conformance tests. An unknown version must fail closed.

## Secrets in contributions

Never commit `.env` files, API keys, OAuth tokens, session data, provider configuration, runtime databases, logs, or captured prompts from a real account. If a secret is committed, revoke it before attempting repository cleanup and report the incident privately.
