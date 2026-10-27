import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SOURCE = KIT / "files" / ".github" / "kit" / "bin" / "preflight"
loader = importlib.machinery.SourceFileLoader("preflight", str(SOURCE))
spec = importlib.util.spec_from_loader("preflight", loader)
assert spec is not None
preflight = importlib.util.module_from_spec(spec)
loader.exec_module(preflight)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ChangedPaths(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.test")
        git(self.root, "config", "user.name", "t")
        (self.root / "projects" / "hub").mkdir(parents=True)
        (self.root / "projects" / "hub" / "old.txt").write_text("x\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "init")

    def test_a_staged_rename_reports_both_paths_whole(self):
        git(self.root, "mv", "projects/hub/old.txt", "docs_new.txt")
        paths, _ = preflight.changed_paths(self.root, "main")
        self.assertIn("projects/hub/old.txt", paths)
        self.assertIn("docs_new.txt", paths)

    def test_a_committed_move_reports_the_directory_it_left(self):
        git(self.root, "checkout", "-q", "-b", "work")
        git(self.root, "mv", "projects/hub/old.txt", "docs_new.txt")
        git(self.root, "commit", "-q", "-m", "move")
        paths, _ = preflight.changed_paths(self.root, "main")
        self.assertEqual(paths, ["docs_new.txt", "projects/hub/old.txt"])

    def test_a_new_directory_is_listed_file_by_file(self):
        new = self.root / "projects" / "api" / "pkg"
        new.mkdir(parents=True)
        (new / "mod.py").write_text("x = 1\n", encoding="utf-8")
        paths, _ = preflight.changed_paths(self.root, "main")
        self.assertEqual(paths, ["projects/api/pkg/mod.py"])

    def test_an_unstaged_edit_keeps_its_first_character(self):
        (self.root / "projects" / "hub" / "old.txt").write_text("y\n", encoding="utf-8")
        paths, _ = preflight.changed_paths(self.root, "main")
        self.assertEqual(paths, ["projects/hub/old.txt"])

    def test_git_paths_resolve_at_the_root_whatever_the_cwd(self):
        sub = self.root / "projects"
        here = os.getcwd()
        os.chdir(sub)
        self.addCleanup(os.chdir, here)
        self.assertEqual(
            preflight.git_path("preflight", self.root), self.root / ".git" / "preflight"
        )


if __name__ == "__main__":
    unittest.main()
