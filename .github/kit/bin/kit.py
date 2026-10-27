#!/usr/bin/env python3
"""kit - copy the shared agent workflow into a repository and prove the copy is intact.

The workflow kit lives in letsrebase/rebase under tooling/workflow-kit. Every
repository that uses it carries a copy of `tooling/workflow-kit/files/`, a pull
request template generated from its own `.github/workflow.json`, a
`.claude/skills/` tree that mirrors `.agents/skills/`, and a manifest of hashes in
`.github/kit/manifest.json`. Nothing here needs the network or a dependency beyond
Python 3.10, so `check` runs the same on a laptop, on Windows and in CI.

    python3 .github/kit/bin/kit.py check
    python3 .github/kit/bin/kit.py sync --source <path to tooling/workflow-kit>
    python3 .github/kit/bin/kit.py template

`check` fails (exit 1) when a file the kit manages was edited by hand, when the
pull request template no longer matches `.github/workflow.json`, or when
`.claude/skills/` and `.agents/skills/` differ. Edit the kit's source, or the
repository's own skill under `.agents/skills/`, and run `sync` again.

Why `.claude/skills/` is a copy and not a symlink: Codex reads `.agents/skills/`,
Claude Code reads `.claude/skills/`, and Git for Windows checks a symlink out as a
plain text file unless the machine runs in Developer Mode with `core.symlinks`
enabled, which would leave Claude Code with no skills and no error.

Exit codes: 0 intact, 1 findings, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

MANIFEST = Path(".github/kit/manifest.json")
CONFIG = Path(".github/workflow.json")
TEMPLATE = Path(".github/PULL_REQUEST_TEMPLATE.md")
AGENTS_SKILLS = Path(".agents/skills")
CLAUDE_SKILLS = Path(".claude/skills")
CLAUDE_SETTINGS = Path(".claude/settings.json")
# Claude Code's wiring of .github/kit/bin/linear_gate.py. The rest of settings.json is the
# repository's own: sync adds this entry when it is missing and leaves every other key alone.
CLAUDE_HOOK = {
    "matcher": "mcp__.*[Ll]inear.*",
    "hooks": [
        {
            "type": "command",
            # Claude Code runs hooks through a POSIX shell (Git Bash on Windows), where
            # `python3` is often missing and `python` is the interpreter.
            "command": "py=$(command -v python3 || command -v python) && "
            '"$py" "$CLAUDE_PROJECT_DIR/.github/kit/bin/linear_gate.py"',
        }
    ],
}

DEFAULT_HINTS = {
    "What this changes": (
        "One paragraph, in the first person. The first sentence names the problem: what was\n"
        "wrong, missing or impossible before. Then what is true now, in words a product person\n"
        "understands."
    ),
    "How I verified it": (
        'The command you ran and what it said, not "tests pass". If a UI changed, say what you\n'
        "looked at. If you could not verify something, say which part and why."
    ),
    "Anything a reviewer should look at twice": (
        "The bit you are least sure about, a decision that could have gone the other way, or a\n"
        "path that is not covered by a test. Delete the section if there is genuinely nothing."
    ),
    "Screenshots and video": (
        "For anything a person could see: one before-and-after pair per change. For anything a\n"
        "person could do: one video of the feature in use. Attach them with `gh pr create\n"
        "--attach`. If nothing visible changed, say so and why. Keep this section either way."
    ),
}


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------- #
# configuration


def load_config(repo: Path) -> dict:
    path = repo / CONFIG
    if not path.is_file():
        raise ConfigError(
            f"{CONFIG} is missing: copy tooling/workflow-kit/workflow.example.json "
            "there and fill it in"
        )
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG} is not valid JSON: {exc}") from exc
    problems = validate_config(cfg)
    if problems:
        raise ConfigError(f"{CONFIG}: " + "; ".join(problems))
    return cfg


def validate_config(cfg: dict) -> list[str]:
    problems: list[str] = []
    if cfg.get("version") != 1:
        problems.append("`version` must be 1")
    tracker = cfg.get("tracker")
    if tracker is not None:
        if not isinstance(tracker, dict):
            problems.append("`tracker` is an object or null")
        else:
            for key in ("kind", "prefix"):
                if not isinstance(tracker.get(key), str) or not tracker.get(key):
                    problems.append(f"`tracker.{key}` is required")
    commits = cfg.get("commits", {})
    if not isinstance(commits.get("types"), list) or not commits.get("types"):
        problems.append("`commits.types` is a non-empty list")
    if "scopes" in commits and not isinstance(commits["scopes"], list):
        problems.append("`commits.scopes` is a list")
    sections = cfg.get("pr", {}).get("sections")
    if not isinstance(sections, list) or not sections:
        problems.append("`pr.sections` is a non-empty list")
    else:
        titles = [s.get("title") for s in sections if isinstance(s, dict)]
        if len(titles) != len(sections) or not all(isinstance(t, str) and t for t in titles):
            problems.append("every `pr.sections` entry has a `title`")
        elif len(set(titles)) != len(titles):
            problems.append("`pr.sections` titles are unique")
    return problems


# --------------------------------------------------------------------------- #
# generated files


def render_template(cfg: dict) -> str:
    """The pull request template, from the same section list pr_lint.py checks."""
    pr = cfg["pr"]
    sections = pr["sections"]
    out: list[str] = []
    conditional = [s for s in sections if s.get("when")]
    shown = [s for s in sections if not s.get("when")]
    lead = ""
    for s in sections:
        if not s.get("required"):
            break
        lead = s["title"]
    for section in shown:
        out.append(f"## {section['title']}")
        out.append("")
        hint = section.get("hint") or DEFAULT_HINTS.get(section["title"], "")
        if section is shown[-1] and conditional:
            listed = "; ".join(f"«{s['title']}» ({s['when']})" for s in conditional)
            where = f"between «{lead}» and this one" if lead and lead != section["title"] else ""
            extra = textwrap.fill(
                f"Sections to add only when true{', ' + where if where else ''}: {listed}.",
                width=88,
            )
            hint = (hint + "\n\n" if hint else "") + extra
        if hint:
            out.append("<!-- " + hint.replace("-->", "->") + " -->")
            out.append("")
    tracker = cfg.get("tracker")
    if tracker and pr.get("card_line", True):
        out.append(f"Linear: {tracker['prefix']}-<number>.")
    else:
        while out and out[-1] == "":
            out.pop()
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# hashing and trees


def normalized(path: Path) -> bytes:
    """File bytes with CRLF folded to LF, so an autocrlf checkout on Windows still matches."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def digest(path: Path) -> str:
    return hashlib.sha256(normalized(path)).hexdigest()


def is_noise(path: Path) -> bool:
    """Files no repository should carry or compare: bytecode caches and Finder droppings."""
    return "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == ".DS_Store"


def tree(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        p.relative_to(root).as_posix(): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not is_noise(p.relative_to(root))
    }


# --------------------------------------------------------------------------- #
# sync


def sync(repo: Path, source: Path, force: bool = False) -> int:
    files = source / "files"
    version_file = source / "VERSION"
    if not files.is_dir() or not version_file.is_file():
        raise ConfigError(f"{source} is not a workflow kit (no files/ or VERSION)")
    cfg = load_config(repo)
    managed: dict[str, str] = {}

    previous = read_manifest(repo)
    losses = pending_losses(repo, files, previous.get("mirror", {}))
    if losses and not force:
        raise ConfigError(
            "sync would discard work that exists only here; move it into "
            f"{AGENTS_SKILLS.as_posix()} first, or run sync --force to discard it:\n  "
            + "\n  ".join(losses)
        )
    for stale in sorted(set(previous.get("files", {})) - set(collect(files))):
        target = repo / stale
        if target.is_file() and stale != TEMPLATE.as_posix():
            target.unlink()
            print(f"removed {stale} (no longer in the kit)")
    for rel in collect(files):
        src = files / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(normalized(src))
        if rel.startswith(".github/kit/bin/"):
            dst.chmod(0o755)
        managed[rel] = digest(dst)

    template = repo / TEMPLATE
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(render_template(cfg), encoding="utf-8", newline="\n")
    managed[TEMPLATE.as_posix()] = digest(template)

    migrate_claude_only_skills(repo, previous.get("mirror", {}))
    mirror(repo / AGENTS_SKILLS, repo / CLAUDE_SKILLS)
    ensure_claude_hook(repo)

    manifest = {
        "kit": "letsrebase/rebase:tooling/workflow-kit",
        "version": version_file.read_text(encoding="utf-8").strip(),
        "files": dict(sorted(managed.items())),
        # What the mirror held right after this sync, so the next one can tell an edit
        # made in .claude/skills (lost by a mirror) from one made in .agents/skills.
        "mirror": tree(repo / CLAUDE_SKILLS),
    }
    (repo / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (repo / MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        f"synced workflow kit {manifest['version']}: {len(managed)} managed files, "
        f"{len(tree(repo / AGENTS_SKILLS))} skill files mirrored into {CLAUDE_SKILLS.as_posix()}"
    )
    return 0


def collect(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not is_noise(p.relative_to(root))
    )


def pending_losses(repo: Path, files: Path, mirrored: dict[str, str]) -> list[str]:
    """What the mirror would delete or overwrite that exists nowhere else.

    The mirror rebuilds .claude/skills from .agents/skills. A loose file at the top of
    .claude/skills, or a file of a repository's own skill edited in .claude/skills since
    the last sync (its hash is not the one the manifest recorded for the mirror, and not
    the .agents copy's either), would be lost without a word. The kit's own skills are
    exempt: their copies are replaced by design, and each replacement is printed.
    """
    claude, agents = repo / CLAUDE_SKILLS, repo / AGENTS_SKILLS
    if not claude.is_dir() or claude.is_symlink():
        return []
    kit_skills = {p.name for p in (files / AGENTS_SKILLS).iterdir() if p.is_dir()}
    out: list[str] = []
    for entry in sorted(claude.iterdir()):
        rel = f"{CLAUDE_SKILLS.as_posix()}/{entry.name}"
        if entry.is_file() and not is_noise(Path(entry.name)):
            out.append(f"{rel}: a file outside any skill directory")
        elif entry.is_dir() and entry.name in kit_skills:
            if tree(entry) != tree(files / AGENTS_SKILLS / entry.name):
                print(f"replacing {rel} with the kit's {entry.name}")
        elif entry.is_dir():
            # Still in .agents: compare with it. Gone from .agents (deleted or renamed
            # there): every file must still be what the last sync mirrored.
            theirs = tree(agents / entry.name) if (agents / entry.name).is_dir() else {}
            was_mirrored = any(key.startswith(f"{entry.name}/") for key in mirrored)
            if not (agents / entry.name).exists() and not was_mirrored:
                continue  # migrated to .agents below, so nothing is lost
            for name, digest_ in tree(entry).items():
                if digest_ not in (theirs.get(name), mirrored.get(f"{entry.name}/{name}")):
                    out.append(f"{rel}/{name}: edited here, not in {AGENTS_SKILLS.as_posix()}")
    return out


def migrate_claude_only_skills(repo: Path, mirrored: dict[str, str]) -> None:
    """A skill only under .claude/skills moves to .agents/skills before the mirror runs.

    Unless the last sync mirrored it there: then it was deleted from .agents/skills on
    purpose, and bringing it back would make a skill impossible to remove.
    """
    claude, agents = repo / CLAUDE_SKILLS, repo / AGENTS_SKILLS
    if not claude.is_dir() or claude.is_symlink():
        return
    for skill in sorted(p for p in claude.iterdir() if p.is_dir()):
        if any(key.startswith(f"{skill.name}/") for key in mirrored):
            continue
        if not (agents / skill.name).exists():
            (agents / skill.name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill, agents / skill.name)
            print(
                f"moved {CLAUDE_SKILLS.as_posix()}/{skill.name} "
                f"to {AGENTS_SKILLS.as_posix()}/{skill.name}"
            )


def mirror(src: Path, dst: Path) -> None:
    # A symlink, or a symlink Git for Windows checked out as a text file, is replaced.
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    if dst.exists():
        shutil.rmtree(dst)
    if src.is_dir():
        for rel in collect(src):
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes((src / rel).read_bytes())


def claude_settings(repo: Path) -> dict:
    path = repo / CLAUDE_SETTINGS
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CLAUDE_SETTINGS} is not valid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


def pre_tool_use(settings: dict) -> list:
    """settings.hooks.PreToolUse, refusing a shape Claude Code would not read either."""
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ConfigError(f"{CLAUDE_SETTINGS}: `hooks` is an object")
    entries = hooks.get("PreToolUse", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CLAUDE_SETTINGS}: `hooks.PreToolUse` is a list")
    return entries


def has_claude_hook(repo: Path) -> bool:
    return CLAUDE_HOOK in pre_tool_use(claude_settings(repo))


def ensure_claude_hook(repo: Path) -> None:
    if has_claude_hook(repo):
        return
    settings = claude_settings(repo)
    pre_tool_use(settings)
    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    # an older copy of the kit's entry (same matcher) is replaced, not duplicated
    entries[:] = [
        e
        for e in entries
        if not (isinstance(e, dict) and e.get("matcher") == CLAUDE_HOOK["matcher"])
    ]
    entries.append(CLAUDE_HOOK)
    path = repo / CLAUDE_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"added the Linear gate hook to {CLAUDE_SETTINGS.as_posix()}")


def read_manifest(repo: Path) -> dict:
    path = repo / MANIFEST
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------- #
# check


def check(repo: Path) -> list[str]:
    findings: list[str] = []
    cfg = load_config(repo)
    manifest = read_manifest(repo)
    if not manifest.get("files"):
        return [f"{MANIFEST.as_posix()} is missing or empty: run kit.py sync"]

    for rel, expected in manifest["files"].items():
        path = repo / rel
        if not path.is_file():
            findings.append(f"{rel}: managed by the kit and missing")
        elif digest(path) != expected:
            findings.append(f"{rel}: edited by hand; change it in the kit and run kit.py sync")

    template = repo / TEMPLATE
    if template.is_file() and normalized(template).decode("utf-8") != render_template(cfg):
        findings.append(
            f"{TEMPLATE.as_posix()}: does not match {CONFIG.as_posix()}; run kit.py sync"
        )
    if not has_claude_hook(repo):
        findings.append(
            f"{CLAUDE_SETTINGS.as_posix()}: the kit's Linear gate hook is missing; run kit.py sync"
        )

    claude_dir = repo / CLAUDE_SKILLS
    if claude_dir.is_symlink() or (claude_dir.is_file() and not claude_dir.is_dir()):
        findings.append(
            f"{CLAUDE_SKILLS.as_posix()}: must be a directory copy, not a link; run kit.py sync"
        )
    else:
        agents, claude = tree(repo / AGENTS_SKILLS), tree(claude_dir)
        for rel in sorted(set(agents) | set(claude)):
            if rel not in claude:
                findings.append(
                    f"{CLAUDE_SKILLS.as_posix()}/{rel}: missing from the mirror; run kit.py sync"
                )
            elif rel not in agents:
                findings.append(
                    f"{CLAUDE_SKILLS.as_posix()}/{rel}: not in {AGENTS_SKILLS.as_posix()}; "
                    f"skills are written there and mirrored"
                )
            elif agents[rel] != claude[rel]:
                findings.append(
                    f"{CLAUDE_SKILLS.as_posix()}/{rel}: differs from "
                    f"{AGENTS_SKILLS.as_posix()}/{rel}; "
                    f"edit the {AGENTS_SKILLS.as_posix()} copy and run kit.py sync"
                )
    return findings


# --------------------------------------------------------------------------- #
# cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "check", help="prove the kit's files, the template and the skill mirror are intact"
    )
    s = sub.add_parser(
        "sync", help="copy the kit in, regenerate the template and the mirror, write the manifest"
    )
    s.add_argument(
        "--source",
        type=Path,
        required=True,
        help="path to tooling/workflow-kit in a rebase checkout",
    )
    s.add_argument(
        "--force",
        action="store_true",
        help="discard a .claude/skills file or skill edit that exists nowhere else",
    )
    sub.add_parser("template", help="print the pull request template workflow.json describes")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == "sync":
            source = args.source.resolve()
            newer = source / "files" / ".github" / "kit" / "bin" / "kit.py"
            if newer.is_file() and newer.read_bytes() != Path(__file__).read_bytes():
                # The source's kit.py knows what this copy does not (a new managed file,
                # a changed settings entry), so it does the sync: an old copy would copy
                # the new files in and still write the old entry.
                rest = argv if argv is not None else sys.argv[1:]
                return subprocess.run([sys.executable, str(newer), *rest], check=False).returncode
            return sync(repo, source, force=args.force)
        if args.command == "template":
            sys.stdout.write(render_template(load_config(repo)))
            return 0
        findings = check(repo)
    except ConfigError as exc:
        print(f"kit: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(
            f"\nkit check: {len(findings)} finding(s). The kit's source is "
            "tooling/workflow-kit in letsrebase/rebase; this repository's own skills "
            f"are written in {AGENTS_SKILLS.as_posix()}."
        )
        return 1
    print("kit check: intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
