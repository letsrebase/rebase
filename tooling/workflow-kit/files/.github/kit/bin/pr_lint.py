#!/usr/bin/env python3
"""pr_lint - check a pull request's title and body against .github/workflow.json.

    python3 .github/kit/bin/pr_lint.py --title "feat(web): ..." --body-file pr-body.md
    python3 .github/kit/bin/pr_lint.py --github        # in CI, reads the PR from the API

The rules are the ones the pr-creation skill describes, read from the repository's
own `.github/workflow.json`, so the template, the skill and this check cannot
disagree:

- title: Conventional Commits `type(scope): subject`, a type and every scope from the
  configured lists, under `pr.title_max` characters, the subject starting lowercase,
  nothing in Title Case, no trailing period, no em or en dash;
- body: the configured sections in their order (a heading the list does not name may
  sit anywhere before the closing section), each required one present and not
  empty, no template comment left in, no em or en dash, and when the repository has
  a tracker the last line names the card (`Linear: REB-42.`, or the milestone form
  `Linear: milestone <name> (REB-1, REB-2)`).

`--github` reads the title and body from the API rather than from the event
payload, because a re-run reuses the payload of the original event: fix the title
with `gh pr edit`, then re-run the failed jobs, and the check sees the new text.

Exit codes: 0 pass, 1 findings, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path(".github/workflow.json")
EM_DASH, EN_DASH = "\u2014", "\u2013"
HEADING_RE = re.compile(r"^## (.+?)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "when",
    "that",
    "this",
    "its",
    "is",
    "are",
    "not",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


# --------------------------------------------------------------------------- #
# title


def lint_title(title: str, cfg: dict) -> list[Finding]:
    commits = cfg.get("commits", {})
    types = commits.get("types", [])
    scopes = commits.get("scopes")
    scope_required = commits.get("scope_required", bool(scopes))
    limit = cfg.get("pr", {}).get("title_max", 72)

    out: list[Finding] = []
    title = title.strip()
    if not title:
        return [Finding("title", "the title is empty")]
    if len(title) >= limit:
        out.append(Finding("title-length", f"{len(title)} characters; a title is under {limit}"))
    if EM_DASH in title or EN_DASH in title:
        out.append(Finding("title-dash", "no em or en dash in the title"))
    if title.endswith("."):
        out.append(Finding("title-period", "no period at the end of the title"))

    m = re.match(r"^([a-z]+)(?:\(([^)]*)\))?(!)?: (.+)$", title)
    if not m:
        shape = (
            "type(scope): subject" if scope_required else "type(scope): subject or type: subject"
        )
        out.append(
            Finding(
                "title-shape", f"Conventional Commits, `{shape}`, type one of {', '.join(types)}"
            )
        )
        return out
    kind, scope, _, subject = m.groups()
    if kind not in types:
        out.append(
            Finding("title-type", f"`{kind}` is not a type here; use one of {', '.join(types)}")
        )
    if scope is None:
        if scope_required:
            out.append(Finding("title-scope", "a scope is required: `type(scope): subject`"))
    elif scopes is not None:
        for part in [p.strip() for p in scope.split(",")]:
            if part not in scopes:
                out.append(
                    Finding(
                        "title-scope",
                        f"`{part}` is not a scope here; use one of {', '.join(scopes)}",
                    )
                )
    if subject[:1].isupper():
        out.append(
            Finding("title-case", "the subject after the colon starts with a lowercase letter")
        )
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", subject)[1:]
    # A one-letter word is a name or a variable (`Plan A`), not Title Case.
    if any(len(w) > 1 and w[0].isupper() and w.lower() in SMALL_WORDS for w in words):
        out.append(Finding("title-case", "nothing in Title Case after the colon"))
    return out


# --------------------------------------------------------------------------- #
# body


def fenced_flags(lines: list[str]) -> list[bool]:
    """True for each line that is a fence or inside a fenced code block."""
    flags, opener = [], ""
    for line in lines:
        m = FENCE_RE.match(line)
        if not opener:
            flags.append(bool(m))
            opener = m.group(1) if m else ""
        else:
            flags.append(True)
            if (
                m
                and m.group(1)[0] == opener[0]
                and len(m.group(1)) >= len(opener)
                and not line.strip().strip(opener[0])
            ):
                opener = ""
    return flags


def prose_lines(text: str) -> list[str]:
    lines = text.splitlines()
    return [ln for ln, inside in zip(lines, fenced_flags(lines), strict=True) if not inside]


def sections_of(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    heading, buf = None, []
    lines = text.splitlines()
    for line, inside in zip(lines, fenced_flags(lines), strict=True):
        m = None if inside else HEADING_RE.match(line)
        if m:
            if heading is not None:
                out.append((heading, "\n".join(buf)))
            heading, buf = m.group(1), []
        elif heading is not None:
            buf.append(line)
    if heading is not None:
        out.append((heading, "\n".join(buf)))
    return out


def card_patterns(prefix: str) -> tuple[re.Pattern, re.Pattern]:
    p = re.escape(prefix)
    single = re.compile(rf"^Linear: {p}-[0-9]+\.$")
    milestone = re.compile(rf"^Linear: milestone .+ \({p}-[0-9]+(, {p}-[0-9]+)*\)$")
    return single, milestone


ATTACHMENT_LINE_RE = re.compile(r"^(!\[[^\]]*\]\()?https://github\.com/user-attachments/\S+?\)?$")
LOCAL_REF_RE = re.compile(r"!\[[^\]]*\]\((\./[^)\s]+)\)")


def section_ranks(configured: list[dict]) -> dict[str, int]:
    """The leading required sections keep their order, a required closing section stays last,
    and everything between them (conditional and optional) may come in any order."""
    ranks: dict[str, int] = {}
    lead = 0
    while lead < len(configured) and configured[lead].get("required"):
        ranks[configured[lead]["title"]] = lead
        lead += 1
    last = len(configured) - 1
    closes = configured[last].get("required") and last >= lead
    for s in configured[lead : last if closes else len(configured)]:
        ranks[s["title"]] = lead
    if closes:
        ranks[configured[last]["title"]] = lead + 1
    return ranks


def lint_body(body: str, cfg: dict, *, landed: bool = False) -> list[Finding]:
    """`landed` is the body as GitHub stores it, after `gh pr create --attach` rewrote it."""
    pr = cfg["pr"]
    configured = pr["sections"]
    rank = section_ranks(configured)
    required = [s["title"] for s in configured if s.get("required")]
    closing = configured[-1]["title"] if configured[-1].get("required") else None

    out: list[Finding] = []
    text = body.replace("\r\n", "\n")
    prose = "\n".join(prose_lines(text))
    if EM_DASH in prose or EN_DASH in prose:
        out.append(Finding("body-dash", "no em or en dash in the body (code blocks are exempt)"))
    if "<!--" in prose:
        out.append(
            Finding(
                "body-comment", "a template comment is still in the body; replace it with your text"
            )
        )
    if landed:
        for ref in LOCAL_REF_RE.findall(prose):
            out.append(
                Finding(
                    "body-attachment",
                    f"{ref} is still a local path on GitHub, so it renders broken; "
                    f"point it at its user-attachments upload (gh appends an upload it "
                    f"could not place at the end of the body)",
                )
            )

    sections = sections_of(text)
    headings = [h for h, _ in sections]
    for title in required:
        if title not in headings:
            out.append(Finding("section-missing", f"«{title}» is required"))
    seen: set[str] = set()
    for h in headings:
        if h in seen:
            out.append(Finding("section-duplicate", f"«{h}» appears twice"))
        seen.add(h)
    known = [h for h in headings if h in rank]
    if known != sorted(known, key=rank.__getitem__):
        out.append(
            Finding(
                "section-order",
                "sections out of order: "
                + " > ".join(known)
                + "; the order is "
                + " > ".join(s["title"] for s in configured)
                + " (the ones between the first two and the last in any order)",
            )
        )
    if closing and closing in headings and headings[-1] != closing:
        out.append(
            Finding("section-last", f"«{closing}» closes the body; «{headings[-1]}» comes after it")
        )
    for heading, content in sections:
        if heading in required and not strip_card(content, cfg).strip():
            out.append(Finding("section-empty", f"«{heading}» is empty"))

    tracker = cfg.get("tracker")
    if tracker and pr.get("card_line", True):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # gh appends an upload it could not place at the end of the body; the
        # body-attachment finding above is what reports that, not this one.
        while lines and ATTACHMENT_LINE_RE.match(lines[-1]):
            lines.pop()
        last = lines[-1] if lines else ""
        single, milestone = card_patterns(tracker["prefix"])
        if not (
            single.match(last) or (pr.get("milestone_card_line", True) and milestone.match(last))
        ):
            p = tracker["prefix"]
            out.append(
                Finding(
                    "card-line",
                    f"the last line names the card: `Linear: {p}-42.` or "
                    f"`Linear: milestone <name> ({p}-1, {p}-2)`; found `{last[:80]}`",
                )
            )
    return out


def strip_card(content: str, cfg: dict) -> str:
    tracker = cfg.get("tracker")
    if not tracker:
        return content
    single, milestone = card_patterns(tracker["prefix"])
    return "\n".join(
        ln
        for ln in content.splitlines()
        if not (single.match(ln.strip()) or milestone.match(ln.strip()))
    )


# --------------------------------------------------------------------------- #
# inputs


class ConfigError(Exception):
    pass


def load_config(repo: Path) -> dict:
    path = repo / CONFIG
    if not path.is_file():
        raise ConfigError(f"{CONFIG} is missing")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG} is not valid JSON: {exc}") from exc
    sections = (cfg.get("pr") or {}).get("sections") if isinstance(cfg, dict) else None
    if not isinstance(sections, list) or not sections:
        raise ConfigError(f"{CONFIG} has no `pr.sections`")
    if not all(isinstance(s, dict) and isinstance(s.get("title"), str) for s in sections):
        raise ConfigError(f"every `pr.sections` entry in {CONFIG} has a `title`")
    return cfg


def from_github() -> dict:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not (event_path and repo):
        raise SystemExit("pr_lint: --github needs GITHUB_EVENT_PATH and GITHUB_REPOSITORY")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    number = (event.get("pull_request") or {}).get("number")
    if not number:
        return {}
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    req = urllib.request.Request(
        f"{api}/repos/{repo}/pulls/{number}", headers={"Accept": "application/vnd.github+json"}
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        pr = json.load(resp)
    return {
        "number": number,
        "title": pr.get("title") or "",
        "body": pr.get("body") or "",
        "author": (pr.get("user") or {}).get("login", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--title")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--author", default="")
    parser.add_argument(
        "--github", action="store_true", help="read the pull request from the GitHub API"
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.repo)
    except ConfigError as exc:
        print(f"pr_lint: {exc}", file=sys.stderr)
        return 2
    if args.github:
        pr = from_github()
        if not pr:
            print("pr_lint: not a pull request event, nothing to check")
            return 0
    elif args.title is not None or args.body_file is not None:
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else None
        pr = {"title": args.title, "body": body, "author": args.author}
    else:
        parser.print_usage(sys.stderr)
        return 2

    if pr.get("author") in cfg.get("pr", {}).get("exempt_authors", []):
        print(f"pr_lint: {pr['author']} is exempt in {CONFIG}")
        return 0

    findings: list[Finding] = []
    if pr.get("title") is not None:
        findings += lint_title(pr["title"], cfg)
    if pr.get("body") is not None:
        findings += lint_body(pr["body"], cfg, landed=args.github)

    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        print(f"::error title=pr_lint {f.rule}::{f.message}" if in_actions else str(f))
    if findings:
        if args.github:
            print(
                f"\nFix the PR with `gh pr edit {pr['number']} --title ... --body-file ...`, "
                "then re-run the failed jobs of this run (`gh run rerun "
                f"{os.environ.get('GITHUB_RUN_ID', '<run id>')} --failed`): "
                "this check reads the PR fresh from the API."
            )
        return 1
    print("pr_lint: title and body follow .github/workflow.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
