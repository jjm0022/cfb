---
name: "source-command-handoff"
description: "Refresh docs/HANDOFF.md so it matches what is actually built and verified."
---

# source-command-handoff

Use this skill when the user asks to run the migrated source command `handoff`.

## Command Template

Update `docs/HANDOFF.md` — the document a future session reads first, after a
context reset, to resume this project. Its whole value is that it can be
trusted without re-deriving anything, so accuracy matters more than coverage.

## Update, never regenerate

The file already holds knowledge that **cannot be recovered from the code or
git history**, and rewriting from scratch destroys it. These sections are
load-bearing — carry them forward, correct them only when they are wrong, and
never drop an entry because it seems obvious now:

- **Decisions and amendments — do not re-litigate.** Each entry exists because
  something was tried and failed, or a human ruled on it. The reason is the
  content; keep it.
- **Load-bearing conventions — do not "improve" these.** Sign conventions, the
  append-only rule, the id format, fail-loud-per-source. A future session that
  "cleans these up" breaks the system silently.
- **Known gaps.** A gap that is still open stays listed. A gap that closed gets
  rewritten as closed, with what closed it.

## Verify before you write

Do not restate what the last version claimed. Check it:

```bash
uv run pytest -q                          # exact passing count
uv run ruff check src tests && uv run ruff format --check src tests
git log --oneline -20                     # what landed since the last update
git status --short                        # uncommitted work is worth naming
```

Every number in the file — test counts, row counts, credits, hit rates — must
come from a command you ran in this session, not from the previous version of
the document. If you cannot verify a claim, say it is unverified rather than
repeating it.

## What belongs in it

- **Current state:** branch, test count, lint status, what phase the work is in.
- **What is built:** one line per module, describing responsibility rather than
  implementation. Update when a module's job changes, not when its internals do.
- **Remaining work:** ordered, with the blocking dependency named for each item.
- **Anything a fresh session would get wrong.** This is the real test. If a
  reasonable engineer would make a mistake without knowing it, write it down —
  especially discoveries that cost real time or money to find.

## What does not belong

- Anything the code, tests, or `git log` already state plainly.
- Narrative of how the work went. Conclusions only.
- Speculation about future work that has not been decided.

## Rules

- **State what the evidence does not establish.** A result with an untested
  assumption behind it must say so in the same breath. An overclaiming handoff
  is worse than no handoff, because it is trusted.
- **Absolute dates, never relative.** "2026-08-19", not "last week".
- Update the `**Last updated:**` line with the date and what prompted the pass.
- Keep the existing section order and heading style.
- If arguments were passed, treat them as the focus for this pass, but still
  correct anything else you find to be stale.

Finally, commit the update on its own, with a message saying what changed in
the project's understanding — not merely that the handoff was updated.
