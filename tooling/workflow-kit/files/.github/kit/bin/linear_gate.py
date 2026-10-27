#!/usr/bin/env python3
"""linear_gate - refuse a Linear write that breaks the board's writing rules.

An agent hook, not a CI step: it runs before the tool call, so the session that wrote
the text gets the reason and rewrites it, whether or not it ever loaded the
linear-content skill. Wired for Claude Code by `.claude/settings.json` (a PreToolUse
command hook) and for omp by `.omp/hooks/pre/linear-gate.ts`; both come from the
workflow kit. It reads one JSON object on stdin, `{"tool_name": ..., "tool_input": ...}`,
and when it refuses, prints Claude Code's PreToolUse deny object on stdout. Anything it
does not understand passes: a hook that fails closed blocks work it was never about.

The rules are the ones the linear-content skill states and a machine can check:

- no em or en dash, no `\\n` escape where a real newline belongs, no emoji;
- none of the selling words the skill names, and no "not just X but Y";
- an issue or project body, a comment and an update use bold lead words, not `#`
  headings (a document may have headings);
- an issue title is under 80 characters with no colon;
- a project status update is three sentences at most.

    echo '{"tool_name": "mcp__linear__save_comment", "tool_input": {"body": "..."}}' \\
      | python3 .github/kit/bin/linear_gate.py
"""

from __future__ import annotations

import json
import re
import sys

WRITE_TOOLS = (
    "save_issue",
    "save_comment",
    "save_project",
    "save_status_update",
    "save_milestone",
    "save_initiative",
    "save_document",
)
TEXT_KEYS = ("title", "description", "body", "summary", "content", "name")
SELLING = ("robust", "seamless", "powerful", "leverage", "delve")
EMOJI_RE = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\U0001f000-\U0001f2ff]")
HEADING_RE = re.compile(r"^ {0,3}#{1,6} \S", re.MULTILINE)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
CODE_RE = re.compile(r"`[^`\n]*`")


def tool_of(name: str) -> str | None:
    """The write this call makes, or None when it is not a Linear write."""
    if "linear" not in name.lower():
        return None
    for tool in WRITE_TOOLS:
        if name.endswith(tool):
            return tool
    return None


def texts(tool_input: dict) -> dict[str, str]:
    out = {k: v for k, v in tool_input.items() if k in TEXT_KEYS and isinstance(v, str)}
    for i, op in enumerate(tool_input.get("patch") or []):
        if isinstance(op, dict):
            for key in ("text", "new_string"):
                if isinstance(op.get(key), str):
                    out[f"patch[{i}].{key}"] = op[key]
    return out


def prose(text: str) -> str:
    """The text with code blocks and inline code removed: rules are about prose."""
    return CODE_RE.sub("", FENCE_RE.sub("", text))


def sentences(text: str) -> int:
    flat = " ".join(prose(text).split())
    return len([s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*«\"(])", flat) if s.strip()])


def check(tool: str, tool_input: dict) -> list[str]:
    problems: list[str] = []
    fields = texts(tool_input)
    for key, text in fields.items():
        plain = prose(text)
        if "\u2014" in plain or "\u2013" in plain:
            problems.append(f"{key}: no em or en dash; use a comma, a colon or a full stop")
        if "\\n" in plain:
            problems.append(f"{key}: a literal \\n escape; send real newlines")
        if EMOJI_RE.search(plain):
            problems.append(f"{key}: no emoji")
        lowered = plain.lower()
        for word in SELLING:
            if re.search(rf"\b{word}\b", lowered):
                problems.append(f"{key}: drop the selling word «{word}»; say what it does")
        if re.search(r"\bnot (just|only)\b[^.]*\bbut\b", lowered):
            problems.append(f"{key}: no «not just X but Y»; say the one thing")
        is_body = key in ("description", "body", "content") or key.startswith("patch[")
        if is_body and tool != "save_document" and HEADING_RE.search(plain):
            problems.append(f"{key}: bold lead words (**Observed.**), not # headings")
    title = fields.get("title")
    if tool == "save_issue" and title is not None:
        if len(title) >= 80:
            problems.append(f"title: {len(title)} characters; under 80, the rest goes in the body")
        if ":" in prose(title):
            problems.append("title: a colon means the second half belongs in the body")
    body = fields.get("body")
    if tool == "save_status_update" and body is not None and sentences(body) > 3:
        problems.append(
            f"body: {sentences(body)} sentences; an update is three: what shipped, the health "
            "and why, the next visible thing"
        )
    return problems


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tool = tool_of(str(payload.get("tool_name") or ""))
        tool_input = payload.get("tool_input")
        if tool is None or not isinstance(tool_input, dict):
            return 0
        problems = check(tool, tool_input)
    except Exception:  # noqa: BLE001 - fail open: a broken gate must not block the call
        return 0
    if problems:
        reason = (
            "Linear write refused by the workflow kit's gate (linear-content rules): "
            + "; ".join(problems)
            + ". Rewrite and retry."
        )
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
