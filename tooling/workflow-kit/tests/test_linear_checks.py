import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "files" / ".github" / "kit" / "bin"))

import linear_audit  # noqa: E402
import linear_gate  # noqa: E402


def stdin(text: str) -> io.TextIOWrapper:
    """Bytes in UTF-8 behind a cp1252 text layer, as on a Windows machine's default locale."""
    return io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="cp1252")


def gate(tool_name: str, tool_input: dict) -> str | None:
    """The deny reason the gate prints for this call, or None when it lets it through."""
    out = io.StringIO()
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input}, ensure_ascii=False)
    with mock.patch("sys.stdin", stdin(payload)), redirect_stdout(out):
        linear_gate.main()
    text = out.getvalue()
    return json.loads(text)["hookSpecificOutput"]["permissionDecisionReason"] if text else None


GOOD_BODY = "**Observed.** The list is slow.\n\n**Done when.** It loads in a second."


class Gate(unittest.TestCase):
    def test_a_clean_card_passes_through_either_tool_name_shape(self):
        args = {"title": "Make the list load in a second", "description": GOOD_BODY}
        self.assertIsNone(gate("mcp__linear-rebase__save_issue", args))
        self.assertIsNone(gate("mcp__linear_rebase_save_issue", args))

    def test_dashes_escapes_headings_and_selling_words_are_refused(self):
        self.assertIn("em dash", gate("mcp__linear__save_comment", {"body": "Done \u2014 mostly."}))
        self.assertIn("\\n escape", gate("mcp__linear__save_comment", {"body": "one\\ntwo"}))
        self.assertIn(
            "# headings", gate("mcp__linear__save_issue", {"description": "## Observed\nslow"})
        )
        self.assertIn("robust", gate("mcp__linear__save_comment", {"body": "A robust fix."}))
        self.assertIn(
            "not just", gate("mcp__linear__save_comment", {"body": "Not just faster but cheaper."})
        )

    def test_code_is_exempt(self):
        body = "Ran `a \u2014 b` and:\n```\n# not a heading \u2014 output\n```"
        self.assertIsNone(gate("mcp__linear__save_comment", {"body": body}))

    def test_issue_title_rules_and_document_headings(self):
        self.assertIn("80", gate("mcp__linear__save_issue", {"title": "x" * 80}))
        self.assertIn(
            "colon", gate("mcp__linear__save_issue", {"title": "Fix the list: it is slow"})
        )
        self.assertIsNone(gate("mcp__linear__save_project", {"name": "Ship it: phase one"}))
        self.assertIsNone(gate("mcp__linear__save_document", {"content": "# A real document"}))

    def test_a_status_update_is_three_sentences(self):
        ok = "The list shipped. On track, nothing blocks. Next is search."
        self.assertIsNone(gate("mcp__linear__save_status_update", {"body": ok}))
        self.assertIn(
            "4 sentences", gate("mcp__linear__save_status_update", {"body": ok + " Also this."})
        )

    def test_patches_are_checked(self):
        args = {"patch": [{"op": "append", "text": "\n\n**Adjacent.** None \u2014 empty."}]}
        self.assertIn("patch[0].text", gate("mcp__linear__save_issue", args))

    def test_reads_and_other_servers_pass(self):
        self.assertIsNone(gate("mcp__linear__list_issues", {"query": "x \u2014 y"}))
        self.assertIsNone(gate("mcp__notion__save_page", {"body": "x \u2014 y"}))

    def test_what_the_rules_leave_alone(self):
        ok = {
            "body": "**Observed.** «Lui ha detto: sì \u2014 certo, robust», on 2026-10-27.\n\n"
            "**Needed.** Two\u2013three items, \u2713 checked.\n\n"
            "~~~\n# not a heading \u2014 inside a fence\n~~~\n\n**Done when.** It loads.",
            "issueId": "REB-1",
        }
        self.assertIsNone(gate("mcp__linear__save_comment", ok))

    def test_abbreviations_do_not_count_as_sentences(self):
        body = "Shipped the list, e.g. Stripe checkout. On track. Next is search."
        self.assertIsNone(gate("mcp__linear__save_status_update", {"body": body}))
        self.assertIn(
            "4 sentences",
            gate("mcp__linear__save_status_update", {"body": body + " Then billing."}),
        )

    def test_an_unclosed_fence_is_code_to_the_end(self):
        body = "**Observed.** Log:\n```\nERROR \u2014 boom"
        self.assertIsNone(gate("mcp__linear__save_comment", {"body": body}))

    def test_garbage_fails_open(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", stdin("not json")), redirect_stdout(out):
            self.assertEqual(linear_gate.main(), 0)
        self.assertEqual(out.getvalue(), "")


LABELS = {"type": ["feature", "fix"], "area": ["area:web", "area:api"]}
NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)  # noqa: UP017 - datetime.UTC is 3.11


def card(**overrides) -> dict:
    base = {
        "identifier": "REB-1",
        "title": "Make the list load in a second",
        "description": GOOD_BODY,
        "priority": 3,
        "createdAt": "2026-10-01T00:00:00Z",
        "project": {"name": "p"},
        "projectMilestone": {"name": "m"},
        "labels": {"nodes": [{"name": "feature"}, {"name": "area:web"}]},
    }
    return {**base, **overrides}


class AuditCards(unittest.TestCase):
    window = NOW - timedelta(hours=24)

    def test_a_conforming_card_has_no_finding(self):
        self.assertEqual(linear_audit.card_findings(card(), LABELS, self.window), [])

    def test_missing_fields_and_label_counts(self):
        bad = card(
            project=None,
            projectMilestone=None,
            priority=0,
            labels={"nodes": [{"name": "feature"}, {"name": "fix"}]},
        )
        self.assertEqual(
            linear_audit.card_findings(bad, LABELS, self.window),
            ["no project", "no milestone", "no priority", "2 type labels", "0 area labels"],
        )

    def test_done_when_is_required_only_inside_the_window(self):
        old = card(description="**Observed.** slow")
        new = card(description="**Observed.** slow", createdAt="2026-10-27T11:00:00Z")
        self.assertEqual(linear_audit.card_findings(old, LABELS, self.window), [])
        self.assertEqual(
            linear_audit.card_findings(new, LABELS, self.window), ["no **Done when.** line"]
        )

    def test_a_card_whose_title_is_skipped_is_not_audited(self):
        snyk = card(identifier="REB-2", title="Fix the Snyk findings of the week of 2026-10-26")
        snyk.update(project=None, projectMilestone=None)
        lost = card(identifier="REB-3", project=None)
        cfg = {
            "tracker": {
                "kind": "linear",
                "workspace": "acme",
                "prefix": "REB",
                "labels": LABELS,
                "audit_skip_titles": ["Fix the Snyk findings of the week of"],
            }
        }

        def paged(key, query, root, variables):
            return [snyk, lost] if root == "issues" else []

        org = {"organization": {"urlKey": "acme"}}
        with (
            mock.patch.object(linear_audit, "graphql", return_value=org),
            mock.patch.object(linear_audit, "paged", side_effect=paged),
        ):
            cards, projects = linear_audit.audit("key", cfg, 24)
        self.assertEqual(cards, ["REB-3: no project"])
        self.assertEqual(projects, [])


def project(**overrides) -> dict:
    base = {
        "state": "started",
        "createdAt": "2026-10-01T00:00:00Z",
        "startedAt": "2026-10-01T00:00:00Z",
        "completedAt": None,
        "projectUpdates": {"nodes": [{"createdAt": "2026-10-25T00:00:00Z", "body": "Started."}]},
        "open": {"nodes": [{"id": "a"}]},
        "any": {"nodes": [{"id": "a"}]},
    }
    return {**base, **overrides}


class AuditProjects(unittest.TestCase):
    def test_a_finished_project_that_is_not_completed_is_reported_unless_held_open(self):
        done = project(open={"nodes": []})
        self.assertIn("every card is Done", linear_audit.project_findings(done, NOW)[0])
        held = project(
            open={"nodes": []},
            projectUpdates={
                "nodes": [{"createdAt": "2026-10-26T00:00:00Z", "body": "Held open: a release."}]
            },
        )
        self.assertEqual(linear_audit.project_findings(held, NOW), [])

    def test_a_started_project_owes_its_first_update_after_a_day(self):
        fresh = project(projectUpdates={"nodes": []}, startedAt="2026-10-27T06:00:00Z")
        stale = project(projectUpdates={"nodes": []})
        self.assertEqual(linear_audit.project_findings(fresh, NOW), [])
        self.assertEqual(linear_audit.project_findings(stale, NOW), ["no update since it started"])

    def test_cards_done_since_an_update_older_than_a_week(self):
        old = project(
            projectUpdates={"nodes": [{"createdAt": "2026-10-10T00:00:00Z", "body": "x"}]},
            done_since_last=2,
        )
        recent = project(done_since_last=2)
        self.assertIn("2 card(s) Done", linear_audit.project_findings(old, NOW)[0])
        self.assertEqual(linear_audit.project_findings(recent, NOW), [])

    def test_a_recent_completion_needs_a_closing_update(self):
        closed = project(
            state="completed",
            completedAt="2026-10-26T00:00:00Z",
            open={"nodes": []},
            projectUpdates={"nodes": [{"createdAt": "2026-10-01T00:00:00Z", "body": "x"}]},
        )
        self.assertEqual(
            linear_audit.project_findings(closed, NOW), ["completed with no closing update"]
        )


if __name__ == "__main__":
    unittest.main()
