# 0001. No code comments

Status: accepted (2026-11)

## Context

A review of every comment in the repository found most of them restated the code, a
few excused a workaround that should have been fixed, and the rest recorded decisions
that were easy to lose when the surrounding code moved.

## Decision

- Code carries no comments. Names, types, docstrings and tests explain the *what*.
- A non-obvious decision goes in an ADR in this directory.
- A workaround gets an issue, not an apologetic comment.
- Tool directives are not comments and stay allowed: `noqa`, `type:`/`ty:` ignores,
  `pragma: no cover`, `fmt:`, TypeScript `/// <reference>`, and JSDoc `/** */` blocks
  (TypeScript's docstrings).
- `scripts/check_comments.py` (run by `just comments`, part of `just lint`) enforces this
  with a ratchet: `comment-baseline.json` records how many comments each file may still
  hold. A file may not gain comments; when it loses some, `just comments --update` lowers
  its allowance.

## Consequences

Docstrings and ADRs must be kept current instead. Re-run the review with the
`/delete-comment` skill (`.claude/skills/delete-comment/`).
