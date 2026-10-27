import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "files" / ".github" / "kit" / "bin"))

import kit  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class KitSyncAndCheck(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        cfg = json.loads((KIT / "workflow.example.json").read_text(encoding="utf-8"))
        cfg["tracker"]["prefix"] = "ABC"
        write(self.repo / ".github/workflow.json", json.dumps(cfg))
        write(self.repo / ".claude/skills/own-skill/SKILL.md", "---\nname: own-skill\n---\nmine\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(kit.sync(self.repo, KIT), 0)

    def test_a_fresh_sync_checks_clean(self):
        self.assertEqual(kit.check(self.repo), [])

    def test_a_claude_only_skill_moves_to_agents_and_stays_mirrored(self):
        self.assertTrue((self.repo / ".agents/skills/own-skill/SKILL.md").is_file())
        self.assertTrue((self.repo / ".claude/skills/pr-creation/SKILL.md").is_file())

    def test_a_hand_edit_of_a_managed_file_fails(self):
        target = self.repo / ".github/kit/bin/pr_lint.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# local tweak\n", encoding="utf-8"
        )
        findings = kit.check(self.repo)
        self.assertTrue(
            any(f.startswith(".github/kit/bin/pr_lint.py: edited by hand") for f in findings),
            findings,
        )

    def test_an_edit_made_only_in_the_claude_mirror_fails(self):
        target = self.repo / ".claude/skills/own-skill/SKILL.md"
        target.write_text("---\nname: own-skill\n---\nchanged here only\n", encoding="utf-8")
        findings = kit.check(self.repo)
        self.assertTrue(
            any("differs from .agents/skills/own-skill/SKILL.md" in f for f in findings), findings
        )

    def test_a_skill_added_only_to_claude_fails_until_synced(self):
        write(self.repo / ".claude/skills/stray/SKILL.md", "x\n")
        self.assertTrue(any("not in .agents/skills" in f for f in kit.check(self.repo)))
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)
        self.assertEqual(kit.check(self.repo), [])
        self.assertTrue((self.repo / ".agents/skills/stray/SKILL.md").is_file())

    def test_changing_workflow_json_without_sync_flags_the_template(self):
        path = self.repo / ".github/workflow.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["tracker"]["prefix"] = "XYZ"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        findings = kit.check(self.repo)
        self.assertTrue(
            any(f.startswith(".github/PULL_REQUEST_TEMPLATE.md: does not match") for f in findings),
            findings,
        )

    def test_crlf_checkout_still_matches(self):
        for p in (self.repo / ".agents").rglob("*.md"):
            p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(kit.check(self.repo), [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_claude_skills_dir_is_rejected(self):
        shutil.rmtree(self.repo / ".claude/skills")
        (self.repo / ".claude/skills").symlink_to(
            self.repo / ".agents/skills", target_is_directory=True
        )
        self.assertTrue(any("not a link" in f for f in kit.check(self.repo)))

    def test_a_symlink_checked_out_as_a_text_file_is_rejected_and_sync_repairs_it(self):
        shutil.rmtree(self.repo / ".claude/skills")
        (self.repo / ".claude/skills").write_text("../.agents/skills", encoding="utf-8")
        self.assertTrue(any("not a link" in f for f in kit.check(self.repo)))
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)
        self.assertEqual(kit.check(self.repo), [])

    def test_a_skill_deleted_from_agents_stays_deleted(self):
        shutil.rmtree(self.repo / ".agents/skills/own-skill")
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)
        self.assertFalse((self.repo / ".agents/skills/own-skill").exists())
        self.assertFalse((self.repo / ".claude/skills/own-skill").exists())

    def test_an_edit_left_in_a_deleted_skills_mirror_is_not_discarded_silently(self):
        shutil.rmtree(self.repo / ".agents/skills/own-skill")
        write(
            self.repo / ".claude/skills/own-skill/SKILL.md", "---\nname: own-skill\n---\nedited\n"
        )
        with self.assertRaises(kit.ConfigError):
            kit.sync(self.repo, KIT)
        self.assertTrue((self.repo / ".claude/skills/own-skill/SKILL.md").is_file())

    def test_a_never_mirrored_skill_beside_an_empty_agents_directory_is_not_discarded(self):
        write(self.repo / ".claude/skills/fresh/SKILL.md", "---\nname: fresh\n---\nonly here\n")
        (self.repo / ".agents/skills/fresh").mkdir(parents=True)
        with self.assertRaises(kit.ConfigError):
            kit.sync(self.repo, KIT)
        self.assertTrue((self.repo / ".claude/skills/fresh/SKILL.md").is_file())

    def test_the_template_ends_with_a_card_line_the_lint_refuses(self):
        text = (self.repo / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith("Linear: ABC-<number>."))

    def test_no_tracker_means_no_card_line(self):
        path = self.repo / ".github/workflow.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["tracker"] = None
        self.assertNotIn("Linear:", kit.render_template(cfg))

    def test_a_file_dropped_from_the_kit_is_removed_on_sync(self):
        manifest = json.loads((self.repo / kit.MANIFEST).read_text(encoding="utf-8"))
        manifest["files"]["old/thing.txt"] = "0" * 64
        (self.repo / kit.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        write(self.repo / "old/thing.txt", "stale\n")
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)
        self.assertFalse((self.repo / "old/thing.txt").exists())

    def test_bytecode_caches_are_neither_copied_nor_compared(self):
        write(self.repo / ".agents/skills/own-skill/__pycache__/x.cpython-313.pyc", "bytes\n")
        self.assertEqual(kit.check(self.repo), [])
        manifest = json.loads((self.repo / kit.MANIFEST).read_text(encoding="utf-8"))
        self.assertFalse(any("__pycache__" in path for path in manifest["files"]))

    def test_missing_config_is_a_configuration_error(self):
        (self.repo / ".github/workflow.json").unlink()
        with self.assertRaises(kit.ConfigError):
            kit.check(self.repo)

    def test_sync_refuses_to_discard_an_edit_made_only_in_the_mirror(self):
        target = self.repo / ".claude/skills/own-skill/SKILL.md"
        target.write_text("---\nname: own-skill\n---\nonly here\n", encoding="utf-8")
        write(self.repo / ".claude/skills/NOTES.md", "loose\n")
        with self.assertRaises(kit.ConfigError) as caught:
            kit.sync(self.repo, KIT)
        self.assertIn(".claude/skills/own-skill", str(caught.exception))
        self.assertIn(".claude/skills/NOTES.md", str(caught.exception))
        self.assertEqual(
            target.read_text(encoding="utf-8"), "---\nname: own-skill\n---\nonly here\n"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT, force=True)
        self.assertEqual(kit.check(self.repo), [])

    def test_the_kits_own_skills_are_replaced_without_asking(self):
        target = self.repo / ".claude/skills/pr-creation/SKILL.md"
        target.write_text("an older pr-creation\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)
        self.assertEqual(kit.check(self.repo), [])


class ClaudeHook(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        cfg = json.loads((KIT / "workflow.example.json").read_text(encoding="utf-8"))
        write(self.repo / ".github/workflow.json", json.dumps(cfg))

    def sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            kit.sync(self.repo, KIT)

    def test_sync_adds_the_gate_and_keeps_the_repositorys_own_settings(self):
        own = {
            "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
        }
        write(self.repo / ".claude/settings.json", json.dumps(own))
        self.sync()
        path = self.repo / ".claude/settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], own["permissions"])
        self.assertEqual(
            settings["hooks"]["PreToolUse"], [own["hooks"]["PreToolUse"][0], kit.CLAUDE_HOOK]
        )
        self.sync()
        settings = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
        self.assertEqual(kit.check(self.repo), [])

    def test_check_reports_a_removed_gate(self):
        self.sync()
        write(self.repo / ".claude/settings.json", "{}")
        self.assertTrue(any("Linear gate hook is missing" in f for f in kit.check(self.repo)))

    def test_a_persons_hook_under_the_kits_matcher_survives_a_resync(self):
        mine = {"type": "command", "command": "echo audited"}
        old_gate = {"type": "command", "command": "python3 .github/kit/bin/linear_gate.py"}
        entry = {"matcher": kit.CLAUDE_HOOK["matcher"], "hooks": [old_gate, mine]}
        write(self.repo / ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [entry]}}))
        self.sync()
        settings = json.loads((self.repo / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            settings["hooks"]["PreToolUse"],
            [{"matcher": kit.CLAUDE_HOOK["matcher"], "hooks": [mine]}, kit.CLAUDE_HOOK],
        )

    def test_a_malformed_settings_file_is_refused_before_anything_is_written(self):
        write(self.repo / ".claude/settings.json", json.dumps({"hooks": []}))
        with self.assertRaises(kit.ConfigError):
            self.sync()
        self.assertFalse((self.repo / ".github/kit").exists())

    def test_an_old_copy_syncs_through_the_sources_kit_py(self):
        # A repository's kit.py is the one an agent runs; when it predates a change to the
        # gate's entry, the sync must still write the new entry, not its own old one.
        self.sync()
        old = self.repo / ".github/kit/bin/kit.py"
        old.write_text(
            old.read_text(encoding="utf-8").replace("linear_gate.py", "old_gate.py"),
            encoding="utf-8",
        )
        stale = dict(kit.CLAUDE_HOOK, hooks=[{"type": "command", "command": "python3 old.py"}])
        write(self.repo / ".claude/settings.json", json.dumps({"hooks": {"PreToolUse": [stale]}}))
        run = subprocess.run(
            [sys.executable, str(old), "--repo", str(self.repo), "sync", "--source", str(KIT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(kit.check(self.repo), [])


class ThisRepositorysCopy(unittest.TestCase):
    """rebase carries a copy of the kit; a change to the source without a sync fails here."""

    ROOT = KIT.parents[1]

    @unittest.skipUnless((KIT.parents[1] / ".github/kit/manifest.json").is_file(), "not in rebase")
    def test_every_kit_file_matches_the_copy_and_the_version(self):
        source = kit.tree(KIT / "files")
        copy = {rel: kit.digest(self.ROOT / rel) for rel in source if (self.ROOT / rel).is_file()}
        stale = sorted(rel for rel in source if copy.get(rel) != source[rel])
        self.assertEqual(stale, [], "run kit.py sync --source tooling/workflow-kit")
        manifest = json.loads((self.ROOT / ".github/kit/manifest.json").read_text(encoding="utf-8"))
        managed = set(manifest["files"]) - {kit.TEMPLATE.as_posix()}
        self.assertEqual(managed, set(source), "a kit file was added or removed without a sync")
        self.assertEqual(manifest["version"], (KIT / "VERSION").read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
