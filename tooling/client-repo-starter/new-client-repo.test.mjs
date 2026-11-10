import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "new-client-repo.mjs");

function which(cmd) {
  for (const dir of (process.env.PATH || "").split(delimiter)) {
    const full = join(dir, cmd);
    if (dir && existsSync(full)) return full;
  }
  return null;
}

// The first of python3, python that is 3.10+, as the script itself picks it.
function findPython() {
  for (const cmd of ["python3", "python"]) {
    const full = which(cmd);
    if (!full) continue;
    try {
      const out = execFileSync(full, ["-c", "import sys; print(sys.version_info >= (3, 10))"], { encoding: "utf8" });
      if (out.trim() === "True") return full;
    } catch {
      // not runnable
    }
  }
  return null;
}

const python = findPython();
const git = which("git");
const skip = !python ? "no Python 3.10+ on PATH" : !git ? "no git on PATH" : false;

// A PATH holding git and python3 only, so the script's `gh` calls fail and nothing touches GitHub.
function makeSandbox() {
  const root = mkdtempSync(join(tmpdir(), "client-repo-starter-"));
  const bin = join(root, "bin");
  mkdirSync(bin);
  symlinkSync(git, join(bin, "git"));
  symlinkSync(python, join(bin, "python3"));
  const target = join(root, "target");
  mkdirSync(target);
  const env = { ...process.env, PATH: bin, GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_SYSTEM: "/dev/null" };
  execFileSync(git, ["init", "-q"], { cwd: target, env });
  return { root, target, env };
}

function runScript({ target, env }, extra = []) {
  return spawnSync(
    process.execPath,
    [
      SCRIPT,
      "--repo", "demo", "--target-dir", target,
      "--project-name", "DEMO", "--one-liner", "a demo",
      "--linear-team", "Demo", "--linear-prefix", "dem",
      "--initiative", "DEMO", "--linear-project", "MVP delivery",
      ...extra,
    ],
    { env, encoding: "utf8" },
  );
}

function ignored({ target, env }, rel) {
  return spawnSync(git, ["-C", target, "check-ignore", "-q", "--no-index", rel], { env }).status === 0;
}

test("a retrofit passes kit.py check and leaves .claude/ tracked", { skip }, () => {
  const box = makeSandbox();
  try {
    writeFileSync(join(box.target, ".gitignore"), "node_modules/\n.claude/\n");
    const res = runScript(box);
    assert.equal(res.status, 0, res.stderr + res.stdout);
    assert.match(res.stdout, /Changed \.gitignore/);
    assert.match(res.stdout, /^Wrote \d+ file\(s\)/m);
    assert.doesNotMatch(res.stdout, /^  workflow kit:/m);

    const check = spawnSync("python3", [".github/kit/bin/kit.py", "check"], { cwd: box.target, env: box.env, encoding: "utf8" });
    assert.equal(check.status, 0, check.stderr + check.stdout);

    assert.equal(ignored(box, ".claude/settings.json"), false);
    assert.equal(ignored(box, ".claude/skills/pr-creation/SKILL.md"), false);
    assert.equal(ignored(box, "node_modules/x"), true);
    assert.match(readFileSync(join(box.target, ".gitignore"), "utf8"), /^\.claude\/\*$/m);
    assert.ok(existsSync(join(box.target, ".claude", "settings.json")));
  } finally {
    rmSync(box.root, { recursive: true, force: true });
  }
});

test("an ignore rule the script cannot rewrite stops it before anything is written", { skip }, () => {
  const box = makeSandbox();
  try {
    writeFileSync(join(box.target, ".gitignore"), ".claude/settings.json\n");
    const res = runScript(box);
    assert.notEqual(res.status, 0);
    assert.match(res.stderr, /\.gitignore:1/);
    assert.equal(existsSync(join(box.target, ".github")), false);
  } finally {
    rmSync(box.root, { recursive: true, force: true });
  }
});
