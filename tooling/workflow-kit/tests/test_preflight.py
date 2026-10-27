import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        # A machine's own core.hooksPath or init template must not reach the fixture.
        isolated = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        )
        isolated.start()
        self.addCleanup(isolated.stop)
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

    def test_a_committed_path_with_an_accent_arrives_unquoted(self):
        git(self.root, "checkout", "-q", "-b", "work")
        (self.root / "projects" / "hub" / "caff\u00e8.py").write_text("x = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "accent")
        paths, _ = preflight.changed_paths(self.root, "main")
        self.assertEqual(paths, ["projects/hub/caff\u00e8.py"])

    def test_a_pass_over_uncommitted_files_is_not_recorded_for_the_hook(self):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True
        ).stdout.strip()
        state = preflight.git_path("preflight", self.root) / "state.json"
        (self.root / "projects" / "hub" / "old.txt").write_text("dirty\n", encoding="utf-8")
        preflight.record_pass(self.root, head, "main", ["lint"], clean_at_start=False)
        self.assertFalse(state.exists())
        git(self.root, "checkout", "--", ".")
        preflight.record_pass(self.root, head, "main", ["lint"], clean_at_start=True)
        self.assertTrue(state.exists())

    def test_install_hook_refuses_a_hooks_path_inside_the_repository(self):
        git(self.root, "config", "core.hooksPath", ".githooks")
        run = subprocess.run(
            ["python3", str(SOURCE), "--install-hook"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("versioned file", run.stderr)
        self.assertFalse((self.root / ".githooks" / "pre-push").exists())

    def test_install_hook_refuses_a_shared_hooks_directory(self):
        shared = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, shared)
        git(self.root, "config", "core.hooksPath", str(shared))
        run = subprocess.run(
            ["python3", str(SOURCE), "--install-hook"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("every repository", run.stderr)
        self.assertFalse((shared / "pre-push").exists())

    def test_install_hook_writes_the_repositorys_own_hook(self):
        run = subprocess.run(
            ["python3", str(SOURCE), "--install-hook"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        hook = self.root / ".git" / "hooks" / "pre-push"
        self.assertIn(".github/kit/bin/preflight", hook.read_text(encoding="utf-8"))

    def hook(self, stdin: str) -> subprocess.CompletedProcess:
        """Run preflight as git's pre-push hook would, with `stdin` as the ref lines."""
        return subprocess.run(
            ["python3", str(SOURCE), "--hook", "--base", "main"],
            cwd=self.root,
            input=stdin,
            capture_output=True,
            text=True,
        )

    def add_manifest(self):
        (self.root / ".github").mkdir()
        (self.root / ".github" / "preflight.json").write_text(
            '{"checks": [{"id": "ok", "run": "true"}]}', encoding="utf-8"
        )
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "manifest")

    def test_the_hook_refuses_uncommitted_changes(self):
        self.add_manifest()
        (self.root / "projects" / "hub" / "old.txt").write_text("fixed\n", encoding="utf-8")
        run = self.hook("")
        self.assertEqual(run.returncode, 2)
        self.assertIn("uncommitted changes", run.stderr)

    def test_the_hook_refuses_a_push_of_another_commit(self):
        self.add_manifest()
        git(self.root, "branch", "other")
        git(self.root, "checkout", "-q", "other")
        (self.root / "projects" / "hub" / "old.txt").write_text("other\n", encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "other")
        other = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True
        ).stdout.strip()
        git(self.root, "checkout", "-q", "main")
        zero = "0" * 40
        run = self.hook(f"refs/heads/other {other} refs/heads/other {zero}\n")
        self.assertEqual(run.returncode, 2)
        self.assertIn("preflight tests only HEAD", run.stderr)

    def test_the_hook_passes_a_push_of_head_and_a_deletion(self):
        self.add_manifest()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True
        ).stdout.strip()
        zero = "0" * 40
        run = self.hook(
            f"refs/heads/main {head} refs/heads/main {zero}\n"
            f"(delete) {zero} refs/heads/gone {head}\n"
        )
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
