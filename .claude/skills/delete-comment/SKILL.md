---
name: delete-comment
description: Review code comments in ogprod (optionally a path/glob scope), delete tautological and stale ones, file issues for excuses, move decision records into docs/adr/, and tighten the comment ratchet. Use when asked to clean up comments or to shrink comment-baseline.json.
---

# Delete comments

Comments are banned in this repository ([ADR 0001](../../../docs/adr/0001-no-code-comments.md)).
`scripts/check_comments.py` (`just comments`) enforces a per-file ratchet stored in
`comment-baseline.json`. This skill shrinks that baseline.

Scope: the argument, if given (a path or glob); otherwise every file the checker scans.

## 1. Inventory

```sh
just comments --list            # every banned comment as path:line: text
```

Directives are not comments and never count: `# noqa`, `# type:`/`# ty:` ignores,
`# pragma: no cover`, `# fmt:`, shebangs, `/// <reference>`, `// @ts-…`, and JSDoc `/** */`.
Docstrings are not comments either.

Read each comment **with its surrounding code** before judging it.

## 2. Categorise and act

| Category | Test | Action |
| --- | --- | --- |
| Tautology | Restates what the names, types or the next line already say | Delete |
| Stale | Describes code, values or behaviour that no longer match | Delete (and fix any doc that repeats the stale claim) |
| Excuse | Justifies a workaround, magic value, unused parameter, oversized function, stringly type… | Delete; `gh issue create` describing the hack and the fix. If the fix is a few lines, do it now instead |
| Decision Record | Explains a non-obvious *why*: an external constraint, a tolerance, a trade-off | Move to `docs/adr/NNNN-slug.md` (Context / Decision / Consequences), add it to `docs/adr/README.md`, delete the comment |

Guidance:
- Prefer extending an existing ADR over creating a near-duplicate.
- A test comment that derives an expected value is an Excuse for a magic number:
  write the derivation as the expression (`3_100.0 / 31`, not `100.0  # 3100 / 31`).
- A comment you cannot remove in this change (e.g. it needs a refactor) stays: file the
  issue and leave it in the baseline. That is what the ratchet is for.
- Verify every claim you copy into an ADR against the code; comments are often stale.
- Never put attribution trailers in commits or issues.

## 3. Ratchet and verify

```sh
just format
just comments --update          # lowers allowances; refuses if any file grew
just check
```

Commit `comment-baseline.json` with the deletions.

## 4. Report

Summarise per category (counts and file:line), the ADRs written, the issues filed, the
lines removed (`git diff --shortstat`), and what is left in the baseline.
