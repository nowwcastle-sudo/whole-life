# AGENTS.md

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`nowwcastle-sudo/whole-life`), using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Repository layout notes

`CONTEXT.md` is the domain glossary and nothing else, exactly as `docs/agents/domain.md`
assumes. Project status, the approved baseline and its SHA-256 hashes, the non-negotiable
v0 invariants, the implementation order, and the public-release gate live in
`docs/project-context.md`. Do not move them back into `CONTEXT.md`.

Korean counterparts exist for the human-facing documents (`README.ko.md`,
`CONTRIBUTING.ko.md`, `CONTEXT.ko.md`). When you change one language, change the other in
the same commit.
