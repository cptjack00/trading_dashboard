## Agent skills

### Issue tracker

Issues live as GitHub Issues on `github.com/cptjack00/trading_dashboard` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root (not yet created; will be added lazily as terms/decisions get resolved). See `docs/agents/domain.md`.

### Change and Review Conventions

- Follow the existing concise commit style, preferably Conventional Commit
  subjects such as `fix(engine): ...`, `feat(straddle): ...`, or `docs: ...`.
- Explain why a change is needed when the subject alone does not capture the
  safety or architectural consequence.
- Add user-visible changes under `## [Unreleased]` in `CHANGELOG.md`. Use Added,
  Changed, Deprecated, Removed, Fixed, or Security headings and omit empty ones.
- Reviews should prioritize correctness, determinism, live safety, dependency
  direction, test coverage, and performance regressions over stylistic taste.

