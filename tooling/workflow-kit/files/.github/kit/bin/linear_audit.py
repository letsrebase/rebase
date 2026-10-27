#!/usr/bin/env python3
"""linear_audit - report the cards and projects of this repository's Linear team that break
the board's rules.

    LINEAR_API_KEY=... python3 .github/kit/bin/linear_audit.py [--hours 24] [--summary FILE]

Read-only: it never writes to Linear. It audits only the team whose key is
`tracker.prefix` in `.github/workflow.json`, after proving the key belongs to
`tracker.workspace`, and it reports card identifiers and project names, never card titles
or bodies, so its output is safe in the log of a public repository.

A card (open, or created in the last `--hours` whatever its state, because a card filed
and closed inside one run never shows among the open ones), unless its title starts with
one of `tracker.audit_skip_titles` (a card a workflow files by itself, outside these rules):

- a project, a milestone, a priority;
- exactly one label of `tracker.labels.type` and exactly one of `tracker.labels.area`;
- a title under 80 characters;
- created inside the window: a `**Done when.**` line in its body.

A project of the team:

- every card Done or Canceled and the project not Completed (unless its last update
  starts with «Held open»);
- started more than a day ago with no update;
- cards Done since its last update and none for seven days;
- completed in the last seven days with no closing update.

Exit codes: 0 clean, 1 findings, 2 usage, configuration or API error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG = Path(".github/workflow.json")
API = "https://api.linear.app/graphql"
STALE = timedelta(days=7)
ISSUE_FIELDS = """identifier title description priority createdAt state { type }
  project { name } projectMilestone { name } labels { nodes { name } }"""


class AuditError(Exception):
    pass


def graphql(key: str, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Authorization": key, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise AuditError(f"Linear answered HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise AuditError(f"Linear did not answer JSON: {exc}") from exc
    if data.get("errors"):
        raise AuditError("; ".join(e.get("message", "?") for e in data["errors"]))
    return data["data"]


def paged(key: str, query: str, path: str, variables: dict) -> list[dict]:
    nodes: list[dict] = []
    after = None
    while True:
        page = graphql(key, query, {**variables, "after": after})
        for part in path.split("."):
            page = page[part]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        after = page["pageInfo"]["endCursor"]


def card_findings(issue: dict, labels: dict, window_start: datetime) -> list[str]:
    names = [n["name"] for n in issue["labels"]["nodes"]]
    out = []
    if not issue.get("project"):
        out.append("no project")
    if not issue.get("projectMilestone"):
        out.append("no milestone")
    if not issue.get("priority"):
        out.append("no priority")
    for group in ("type", "area"):
        count = sum(1 for n in names if n in set(labels.get(group, [])))
        if count != 1:
            out.append(f"{count} {group} labels")
    if len(issue["title"]) >= 80:
        out.append(f"title of {len(issue['title'])} characters")
    created = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    if created >= window_start and "**done when.**" not in (issue.get("description") or "").lower():
        out.append("no **Done when.** line")
    return out


def project_findings(p: dict, now: datetime) -> list[str]:
    updates = sorted(p["projectUpdates"]["nodes"], key=lambda u: u["createdAt"])
    last = updates[-1] if updates else None
    held = bool(last and last["body"].lstrip().lower().startswith("held open"))
    if p["state"] != "completed" and p["any"]["nodes"] and not p["open"]["nodes"] and not held:
        return [
            "every card is Done or Canceled: complete it with a closing update (cancel it "
            "when nothing in it shipped), "
            "or post one starting «Held open:»"
        ]

    def ts(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    if p["state"] == "started":
        if not last:
            started = ts(p.get("startedAt") or p["createdAt"])
            return ["no update since it started"] if now - started > timedelta(days=1) else []
        done = p.get("done_since_last", 0)
        if done and now - ts(last["createdAt"]) > STALE:
            return [f"{done} card(s) Done since the last update of {last['createdAt'][:10]}"]
    if p["state"] == "completed" and p.get("completedAt"):
        completed = ts(p["completedAt"])
        if now - completed <= STALE:
            closing = any(ts(u["createdAt"]) >= completed - timedelta(days=3) for u in updates)
            if not closing:
                return ["completed with no closing update"]
    return []


def audit(key: str, cfg: dict, hours: int) -> tuple[list[str], list[str]]:
    tracker = cfg.get("tracker") or {}
    if tracker.get("kind") != "linear":
        raise AuditError(f"{CONFIG} has no Linear tracker")
    org = graphql(key, "{ organization { urlKey } }")["organization"]["urlKey"]
    if org != tracker.get("workspace"):
        raise AuditError(f"the key belongs to workspace {org}, not {tracker.get('workspace')}")
    team = tracker["prefix"]
    now = datetime.now(timezone.utc)  # noqa: UP017 - the kit runs on 3.10, datetime.UTC is 3.11
    window_start = now - timedelta(hours=hours)

    issues_q = f"""query($after: String, $f: IssueFilter) {{ issues(first: 100, after: $after,
      filter: $f) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {ISSUE_FIELDS} }} }} }}"""
    open_f = {"team": {"key": {"eq": team}}, "state": {"type": {"nin": ["completed", "canceled"]}}}
    recent_f = {"team": {"key": {"eq": team}}, "createdAt": {"gt": window_start.isoformat()}}
    seen: dict[str, dict] = {}
    for f in (open_f, recent_f):
        for issue in paged(key, issues_q, "issues", {"f": f}):
            seen[issue["identifier"]] = issue
    cards = []
    skip = tuple(tracker.get("audit_skip_titles") or [])
    for ident in sorted(seen, key=lambda i: int(i.split("-")[1])):
        if skip and seen[ident]["title"].startswith(skip):
            continue
        problems = card_findings(seen[ident], tracker.get("labels", {}), window_start)
        if problems:
            cards.append(f"{ident}: {', '.join(problems)}")

    projects_q = """query($after: String, $team: String!) { projects(first: 20, after: $after,
      filter: { accessibleTeams: { some: { key: { eq: $team } } } }) {
      pageInfo { hasNextPage endCursor }
      nodes { id name state createdAt startedAt completedAt
        projectUpdates(first: 10) { nodes { createdAt body } }
        open: issues(first: 1, includeArchived: true,
          filter: { state: { type: { nin: ["completed", "canceled"] } } }) { nodes { id } }
        any: issues(first: 1, includeArchived: true) { nodes { id } } } } }"""
    done_q = """query($id: String!, $d: DateTimeOrDuration!) { project(id: $id) {
      issues(first: 100, includeArchived: true, filter: { completedAt: { gt: $d } }) {
      nodes { id } } } }"""
    projects = []
    for p in paged(key, projects_q, "projects", {"team": team}):
        if p["state"] not in ("started", "planned", "backlog", "paused", "completed"):
            continue
        updates = p["projectUpdates"]["nodes"]
        if p["state"] == "started" and updates:
            last = max(u["createdAt"] for u in updates)
            done = graphql(key, done_q, {"id": p["id"], "d": last})["project"]["issues"]["nodes"]
            p["done_since_last"] = len(done)
        problems = project_findings(p, now)
        if problems:
            projects.append(f"{p['name']} [{p['state']}]: {', '.join(problems)}")
    return cards, projects


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--hours", type=int, default=24, help="window for recently created cards")
    parser.add_argument("--summary", type=Path, help="append a Markdown report here")
    args = parser.parse_args(argv)
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        print("linear_audit: LINEAR_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        cfg = json.loads((args.repo / CONFIG).read_text(encoding="utf-8"))
        cards, projects = audit(key, cfg, args.hours)
    except (OSError, json.JSONDecodeError, KeyError, AuditError) as exc:
        print(f"linear_audit: {exc}", file=sys.stderr)
        return 2
    lines = [f"Cards ({len(cards)} non-conforming):", *[f"- {c}" for c in cards]]
    lines += [
        f"Projects ({len(projects)} owing an update or a state):",
        *[f"- {p}" for p in projects],
    ]
    print("\n".join(lines))
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write("## Linear board audit\n\n" + "\n".join(lines) + "\n")
    return 1 if cards or projects else 0


if __name__ == "__main__":
    sys.exit(main())
