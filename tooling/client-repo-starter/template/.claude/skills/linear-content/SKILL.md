---
name: linear-content
description: Use when writing anything that lands on Linear for this repository — an issue title and body, a comment, a closing comment, a project description, a project status update. The shape, the language and what never goes in, so every card reads the same way. Triggers whenever save_issue, save_comment, save_project or save_status_update is about to be called.
---

# Writing on Linear

The board's job is that anyone can see at a glance who is on what and how far. Write
for a reader skimming the list, not one studying a card: what was decided, what was
left undone on purpose, in as few lines as carry them. The full record is the commit,
the PR, or a project document; the card indexes them.

## Language

English, first person, plain words, the way you would say it to a colleague. No em
dashes, no "not just X but Y", no emoji, no selling ("robust", "seamless",
"powerful"). Real newlines, never `\n` escapes. Markdown headings are bold lead
words, not `#` titles — a card is not a document.

## The title

One sentence **under 80 characters, starting with a verb**, one clause, or two when
the second only names what makes the first visible. It names the work the card does.
The observed problem is not lost: it is the `**Observed.**` line of the body. A colon
inside a title means the second half belongs in the body.

- Good: `Let a booking be cancelled up to 2 hours before start`
- Bad: `The confirmed -> draft transition is gone` (the end state, not the work; a
  riddle to anyone who did not write it)
- Bad: a two-hundred-character sentence that is the whole body, in the title

## The issue body

**It fits one screen.** At most four bold lead words, each two or three sentences.
Skip one only when it has nothing to say. Point at the file and line, the spec
section, the run id; never paste them.

```markdown
**Observed.** What happens today, where (`path/to/file.py:123`, a run id), for whom.

**Needed.** The outcome, as behaviour a reader can check, not a list of files to
edit. Where a design choice is already taken, say so and where it is recorded.

**Done when.** One line, the check a reader can test. If it needs more than one
line, it is the work, not a done-when.

**Not here.** One sentence, when a reader might expect something and will not find
it.

**Adjacent.** The open cards the scan found and what was done about each, or that
the area calls answered empty.
```

**Adjacent** is skipped only on a card filed for later, added when it is picked up.
When they apply, one line each: **Blocks** / **Blocked by** (issue ids you have
read), **Decision for the lead** (one question, the options, your recommendation
first).

## Comments

One comment per event that changes the issue, two or three sentences, closing
evidence bullets on the closing comment. Each opens with a bold lead naming the kind:

- **Progress.** `**Step done and live:** ...` What landed, where to see it, the
  commit. Never "working on it".
- **Found.** `**Cause is different from the title:** ...` The reproduction and what
  it changes about the plan.
- **Scope.** `**Scope, narrowed:** ...` or `**Scope, grown:** ...` What the card now
  covers, and why.
- **Waiting.** `**Waiting on <name>:** ...` What you are stopped on, from whom, and
  what you will do when it arrives.
- **Decision.** `**Decided.** ...` What was chosen and why, or, when it is not
  yours to take: the question, the options, your recommendation, and stop.
- **PR.** `PR: <url>` plus one sentence on what it contains and what is still
  running (review, CI).
- **Review.** `**Review applied:** ...` How many findings, which changed the code
  (commit sha), which you left as they were and why.
- **Closing.** `**Merged:** <url> (merge commit ...)` followed by `Evidence:` and a
  bulleted list a reader can chase — run ids, test counts, the request you made and
  what came back, what you opened and saw. Then `Left open on purpose:` if anything
  is.

## Project and milestone naming, and the description

The name is a verb and the work it does (`Let a member invite a colleague by
email`). An outcome-only clause reads as a riddle to anyone who did not write it. No
initiative prefix and no version number — the initiative field already says which
macroprogetto, and `vN` checks nothing. No state word either (`deployed`, `done`,
`complete`): the status already says that, and a name that repeats it goes stale the
moment the status moves.

A milestone carries the same shape; its description is one sentence starting `Closes
when`, with the observable check.

`save_project`: `summary` is one sentence stating the outcome (under 255 chars). The
description is at most a short paragraph — what the contract covers, how it deploys,
what is open now. The architecture and the design record live in the repository's
own `AGENTS.md` and `docs/`; the description points at them in one line.

## Status updates

`save_status_update` with `type: "project"`. Three sentences, no more: what shipped
or slipped, the health word and why, the next visible thing. Post one when the board
alone would mislead; skip it when it would only restate the issue list.

## References

- Commits: seven-character sha in backticks, `03c4461`.
- Code: `path/from/repo/root.py:123`, or the symbol in backticks.
- PRs and pages: the full URL.
- Issues: `{{LINEAR_PREFIX}}-N`, only one you have read.

## What never goes on the board

Secrets, tokens, passwords, or where to find them beyond "ask the person who set it
up". Personal data belonging to the client's own customers or users. Screenshots
carrying real customer data. A claim a test did not make. A restated board. An issue
filed after the work only to have a `Done` card — the commit is the record of
finished work that needed no decision.
