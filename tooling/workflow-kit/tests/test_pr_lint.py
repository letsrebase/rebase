import json
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "files" / ".github" / "kit" / "bin"))

import pr_lint  # noqa: E402

CFG = json.loads((KIT / "workflow.example.json").read_text(encoding="utf-8"))
CFG["tracker"]["prefix"] = "REB"
CFG["commits"]["scopes"] = ["api", "web", "core"]
CFG["pr"]["sections"] = [
    {"title": "What this changes", "required": True},
    {"title": "How I verified it", "required": True},
    {"title": "Migrations", "when": "a migration changed"},
    {"title": "Anything a reviewer should look at twice"},
    {"title": "Screenshots and video", "required": True},
]


def body(*sections: str, card: str | None = "Linear: REB-42.") -> str:
    parts = [f"## {s}\n\nText for {s}." for s in sections]
    return "\n\n".join(parts) + (f"\n\n{card}\n" if card else "\n")


GOOD = ("What this changes", "How I verified it", "Screenshots and video")


def rules(findings):
    return sorted({f.rule for f in findings})


class Title(unittest.TestCase):
    def test_a_good_title_passes(self):
        self.assertEqual(pr_lint.lint_title("feat(web): a draft invoice can be deleted", CFG), [])

    def test_several_known_scopes_pass_and_an_unknown_one_fails(self):
        self.assertEqual(
            pr_lint.lint_title("fix(core,api,web): dates stay in Europe/Rome", CFG), []
        )
        self.assertEqual(rules(pr_lint.lint_title("fix(core,infra): x is y", CFG)), ["title-scope"])

    def test_the_limit_is_exclusive(self):
        base = "feat(web): "
        self.assertEqual(pr_lint.lint_title(base + "a" * (71 - len(base)), CFG), [])
        self.assertEqual(
            rules(pr_lint.lint_title(base + "a" * (72 - len(base)), CFG)), ["title-length"]
        )

    def test_shape_type_case_period_and_dash(self):
        self.assertEqual(rules(pr_lint.lint_title("Add a button", CFG)), ["title-shape"])
        self.assertEqual(rules(pr_lint.lint_title("feature(web): a button", CFG)), ["title-type"])
        self.assertEqual(rules(pr_lint.lint_title("feat: a button", CFG)), ["title-scope"])
        self.assertEqual(rules(pr_lint.lint_title("feat(web): Add a button", CFG)), ["title-case"])
        self.assertEqual(
            rules(pr_lint.lint_title("feat(web): add A Button To The Page", CFG)), ["title-case"]
        )
        self.assertEqual(rules(pr_lint.lint_title("feat(web): a button.", CFG)), ["title-period"])
        self.assertEqual(
            rules(pr_lint.lint_title("feat(web): a button \u2014 finally", CFG)), ["title-dash"]
        )


class Body(unittest.TestCase):
    def test_the_required_sections_and_card_pass(self):
        self.assertEqual(pr_lint.lint_body(body(*GOOD), CFG), [])

    def test_conditional_and_optional_sections_go_in_any_order_between(self):
        b = body(
            "What this changes",
            "How I verified it",
            "Anything a reviewer should look at twice",
            "Migrations",
            "Screenshots and video",
        )
        self.assertEqual(pr_lint.lint_body(b, CFG), [])

    def test_a_conditional_before_verification_is_out_of_order(self):
        b = body("What this changes", "Migrations", "How I verified it", "Screenshots and video")
        self.assertEqual(rules(pr_lint.lint_body(b, CFG)), ["section-order"])

    def test_a_section_after_the_closing_one_fails(self):
        b = body(*GOOD, "Deploy notes")
        self.assertIn("section-last", rules(pr_lint.lint_body(b, CFG)))

    def test_an_unlisted_section_in_the_middle_is_allowed(self):
        b = body(
            "What this changes", "How I verified it", "Late additions", "Screenshots and video"
        )
        self.assertEqual(pr_lint.lint_body(b, CFG), [])

    def test_missing_and_empty_required_sections(self):
        self.assertEqual(
            rules(pr_lint.lint_body(body("What this changes", "Screenshots and video"), CFG)),
            ["section-missing"],
        )
        b = (
            "## What this changes\n\n## How I verified it\n\nran it\n\n"
            "## Screenshots and video\n\nnone\n\nLinear: REB-1.\n"
        )
        self.assertEqual(rules(pr_lint.lint_body(b, CFG)), ["section-empty"])

    def test_card_line_forms(self):
        self.assertEqual(
            pr_lint.lint_body(body(*GOOD, card="Linear: milestone Ship it (REB-1, REB-2)"), CFG), []
        )
        for bad in ("Linear: REB-<number>.", "Linear: POI-3.", "Linear: REB-3", None):
            self.assertIn("card-line", rules(pr_lint.lint_body(body(*GOOD, card=bad), CFG)), bad)

    def test_an_upload_gh_appended_after_the_card_does_not_hide_it(self):
        b = body(*GOOD) + "\nhttps://github.com/user-attachments/assets/1234-abcd\n"
        self.assertEqual(pr_lint.lint_body(b, CFG), [])

    def test_a_local_path_left_on_github_fails_only_when_landed(self):
        b = (
            body("What this changes", "How I verified it").replace("Linear: REB-42.", "")
            + "## Screenshots and video\n\n![](./demo.mp4)\n\nLinear: REB-42.\n"
        )
        self.assertEqual(pr_lint.lint_body(b, CFG), [])
        self.assertEqual(rules(pr_lint.lint_body(b, CFG, landed=True)), ["body-attachment"])

    def test_dashes_and_template_comments_outside_code_fail(self):
        self.assertEqual(
            rules(
                pr_lint.lint_body(body(*GOOD).replace("Text for How", "Text \u2014 for How"), CFG)
            ),
            ["body-dash"],
        )
        self.assertEqual(
            rules(pr_lint.lint_body(body(*GOOD).replace("Text for How", "<!-- hint -->"), CFG)),
            ["body-comment"],
        )
        fenced = body(*GOOD).replace(
            "Text for How I verified it.", "```\na \u2014 b <!-- c -->\n```"
        )
        self.assertEqual(pr_lint.lint_body(fenced, CFG), [])

    def test_no_tracker_means_no_card_line(self):
        cfg = {**CFG, "tracker": None}
        self.assertEqual(pr_lint.lint_body(body(*GOOD, card=None), cfg), [])


class Template(unittest.TestCase):
    def test_the_generated_template_fails_until_filled_in(self):
        import kit

        rendered = kit.render_template(CFG)
        found = rules(pr_lint.lint_body(rendered, CFG))
        self.assertIn("card-line", found)
        self.assertIn("body-comment", found)


if __name__ == "__main__":
    unittest.main()
