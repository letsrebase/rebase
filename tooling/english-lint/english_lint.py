#!/usr/bin/env python3
"""Fails on Italian in the code of this repository (REB-904).

The decision of 2026-11-02: every identifier, file name and comment is English; what a
person reads or hears (UI copy, messages, template content, the OpenAPI and MCP prose)
stays Italian and lives in string literals, which this check never reads. The mapping
from the Italian word to its English identifier is `docs/glossary.md`.

What it reads
    * Python: `tokenize` for names and comments, `ast` for docstrings.
    * TypeScript, TSX, JS, MJS: the repository's `typescript` package, through
      `ts_scan.mjs` (identifiers and comments; strings, template text and JSX text are
      not identifiers).
    * `#` comment lines of shell, YAML, TOML, nginx, Terraform, Dockerfile and Nix files.
    * Every tracked file's path (an Italian file or directory name is an identifier too).

What fails
    identifier  a word of a name is Italian (`vocabulary.WORDS` or a `STEMS` prefix).
    path        a segment of a file path is Italian.
    comment     a comment or docstring is written in Italian: two or more Italian
                function words and words between them, or one plus an accented letter.
                Text in quotes or backticks is removed first, since a comment may quote
                the copy it describes.

What it skips is `allowlist.py`, with a reason for each entry.

Usage (from the repository root, Python 3.10+, no install; TypeScript files need
`pnpm install` once, for the `typescript` package)
    python3 tooling/english-lint/english_lint.py                  # every tracked file
    python3 tooling/english-lint/english_lint.py projects/hub     # one path
    python3 tooling/english-lint/english_lint.py --summary        # counts per directory
    python3 tooling/english-lint/english_lint.py --changed        # files changed since origin/main
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import allowlist  # noqa: E402
import vocabulary  # noqa: E402

PY_SUFFIXES = {".py", ".pyi"}
TS_SUFFIXES = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
HASH_COMMENT_SUFFIXES = {".sh", ".yml", ".yaml", ".toml", ".conf", ".tf", ".nix", ".ini", ".cfg"}
HASH_COMMENT_NAMES = {"Dockerfile", ".dockerignore", ".gitignore", ".env.example", "crontab"}

# A decorator whose last attribute is one of these publishes the function's docstring as
# product copy: an OpenAPI description or an MCP tool, prompt or resource description.
PUBLISHING_DECORATORS = {
    "tool", "prompt", "resource", "get", "post", "put", "patch", "delete", "head",
    "options", "api_route", "websocket",
}  # fmt: skip

_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_QUOTED_RE = re.compile(r"`[^`]{0,200}`|\"[^\"]{0,400}\"|«[^»]{0,400}»|“[^”]{0,400}”|'[^'\n]{2,}'")
_URL_RE = re.compile(r"\S+://\S+")
_CODEISH_RE = re.compile(r"\S*[_/\\.:()\[\]{}=<>$#@]\S*")
_ACCENT_RE = re.compile(r"[àèéìíòóùú]")
_PROSE_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # identifier | path | comment
    word: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.detail}"


def split_words(name: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(name.replace("-", "_").replace("$", "_"))]


def is_italian_word(word: str) -> bool:
    if len(word) < 3 or word.isdigit():
        return False
    return word in vocabulary.WORDS or word.startswith(vocabulary.STEMS)


def italian_words_in(name: str) -> list[str]:
    for noun in allowlist.PROPER_NOUNS:
        name = name.replace(noun, "x")
    return [w for w in split_words(name) if is_italian_word(w)]


def _fold(word: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(c)
    )


def italian_score(text: str) -> tuple[float, list[str]]:
    """How Italian a comment reads, and the words that say so."""
    for noun in (*allowlist.PROPER_NOUNS, *allowlist.PROSE_TERMS):
        text = text.replace(noun, " ")
    text = _QUOTED_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _CODEISH_RE.sub(" ", text)
    score = 0.0
    hits: list[str] = []
    if _ACCENT_RE.search(text):
        score += 2
        hits.append("accented letter")
    for raw in _PROSE_WORD_RE.findall(text):
        folded = _fold(raw)
        if folded in vocabulary.STOPWORDS:
            if folded in vocabulary.AMBIGUOUS_STOPWORDS:
                score += 0.1
            else:
                score += 1
            hits.append(raw)
        elif is_italian_word(folded):
            score += 1
            hits.append(raw)
    return score, hits


def comment_is_italian(text: str) -> tuple[bool, list[str]]:
    score, hits = italian_score(text)
    return score >= 2, hits


# ---------------------------------------------------------------- allowlist ---------

_SKIP = [allowlist.glob_to_regex(g) for g in allowlist.SKIP_PATHS]
_ALLOW = [(allowlist.glob_to_regex(a.glob), a) for a in allowlist.ALLOW]
_REVISION_RE = re.compile(r"^(\d{4})_")


def skipped(path: str) -> bool:
    if any(r.match(path) for r in _SKIP):
        return True
    directory, _, name = path.rpartition("/")
    limit = allowlist.LEGACY_REVISIONS.get(directory)
    if limit is not None:
        m = _REVISION_RE.match(name)
        if m and int(m.group(1)) <= limit:
            return True
    return False


def allowed(path: str, kind: str, word: str) -> bool:
    for regex, entry in _ALLOW:
        if (
            kind in entry.kinds
            and regex.match(path)
            and (entry.words is None or word in entry.words)
        ):
            return True
    return False


# ---------------------------------------------------------------- scanners ----------


def _check_name(path: str, line: int, name: str, kind: str, out: list[Finding]) -> None:
    for word in italian_words_in(name):
        if not allowed(path, kind, word):
            out.append(Finding(path, line, kind, word, f"Italian word '{word}' in `{name}`"))


def _check_comment(path: str, line: int, text: str, what: str, out: list[Finding]) -> None:
    flagged, hits = comment_is_italian(text)
    if flagged and not all(allowed(path, "comment", _fold(h)) for h in hits):
        sample = " ".join(text.split())[:70]
        out.append(
            Finding(
                path,
                line,
                "comment",
                hits[0],
                f'Italian {what}: "{sample}" ({", ".join(hits[:4])})',
            )
        )


def join_runs(comments: list[tuple[str, int, bool]]) -> list[tuple[str, int]]:
    """Comments on consecutive lines are one passage: a quotation or a sentence wraps.

    Each item is (text, first line, whether it stands alone on its line); a trailing
    comment after code is its own passage.
    """
    runs: list[tuple[str, int]] = []
    end = -1
    for text, line, standalone in sorted(comments, key=lambda c: c[1]):
        if standalone and runs and line == end + 1:
            runs[-1] = (runs[-1][0] + "\n" + text, runs[-1][1])
        else:
            runs.append((text, line))
        end = line + text.count("\n") if standalone else -1
    return runs


def _publishing(node: ast.AST) -> bool:
    for deco in getattr(node, "decorator_list", []):
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name in PUBLISHING_DECORATORS:
            return True
    return False


def scan_python(path: str, source: str, out: list[Finding]) -> None:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        out.append(Finding(path, 1, "identifier", "", f"cannot tokenize: {error}"))
        return
    import keyword

    comments: list[tuple[str, int, bool]] = []
    for tok in tokens:
        if tok.type == tokenize.NAME and not keyword.iskeyword(tok.string):
            _check_name(path, tok.start[0], tok.string, "identifier", out)
        elif tok.type == tokenize.COMMENT:
            comments.append(
                (tok.string.lstrip("#"), tok.start[0], tok.line.lstrip().startswith("#"))
            )
    for text, line in join_runs(comments):
        _check_comment(path, line, text, "comment", out)
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        out.append(Finding(path, error.lineno or 1, "identifier", "", f"cannot parse: {error.msg}"))
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)):
            continue
        doc = body[0].value.value
        if not isinstance(doc, str) or _publishing(node):
            continue
        _check_comment(path, body[0].lineno, doc, "docstring", out)


def scan_hash_comments(path: str, source: str, out: list[Finding]) -> None:
    comments: list[tuple[str, int, bool]] = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            comments.append((stripped.lstrip("#"), number, True))
    for text, line in join_runs(comments):
        _check_comment(path, line, text, "comment", out)


def scan_typescript(paths: list[str], root: Path, out: list[Finding]) -> None:
    if not paths:
        return
    node = subprocess.run(
        ["node", str(HERE / "ts_scan.mjs"), *paths],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        # One argv of every TypeScript file is long; the OS limit is far above it.
    )
    if node.returncode != 0:
        sys.exit(
            f"english-lint: ts_scan.mjs failed (is `pnpm install` done?):\n{node.stderr.strip()}"
        )
    for path, found in json.loads(node.stdout).items():
        if "error" in found:
            out.append(Finding(path, 1, "identifier", "", f"cannot parse: {found['error']}"))
            continue
        for name, line, _col in found["identifiers"]:
            _check_name(path, line, name, "identifier", out)
        runs = [(text, line, True) for text, line, _col in found["comments"]]
        for text, line in join_runs(runs):
            _check_comment(path, line, text, "comment", out)


# ---------------------------------------------------------------- driver ------------


def tracked_files(root: Path, selection: list[str]) -> list[str]:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *selection],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.decode()
        return [p for p in listing.split("\0") if p and (root / p).is_file()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        found: list[str] = []
        for target in selection or ["."]:
            base = root / target
            if base.is_file():
                found.append(target)
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv"}]
                found += [str(Path(dirpath, f).relative_to(root)) for f in filenames]
        return sorted(found)


def changed_files(root: Path) -> list[str]:
    base = (
        subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"], cwd=root, capture_output=True, text=True
        ).stdout.strip()
        or "HEAD"
    )
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", base],
        cwd=root,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [p for p in diff if (root / p).is_file()]


def lint(root: Path, files: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    typescript: list[str] = []
    seen_paths: set[str] = set()
    for path in files:
        if skipped(path):
            continue
        parts = path.split("/")
        for depth, segment in enumerate(parts, 1):
            prefix = "/".join(parts[:depth])
            if prefix in seen_paths:
                continue
            seen_paths.add(prefix)
            stem = segment.rsplit(".", 1)[0] if "." in segment.lstrip(".") else segment
            _check_name(prefix, 1, stem, "path", findings)
        suffix = Path(path).suffix
        name = Path(path).name
        if suffix in PY_SUFFIXES:
            scan_python(path, (root / path).read_text(encoding="utf8", errors="replace"), findings)
        elif suffix in TS_SUFFIXES:
            typescript.append(path)
        elif (
            suffix in HASH_COMMENT_SUFFIXES
            or name in HASH_COMMENT_NAMES
            or name.endswith(".env.example")
        ):
            scan_hash_comments(
                path, (root / path).read_text(encoding="utf8", errors="replace"), findings
            )
    scan_typescript(typescript, root, findings)
    return sorted(findings, key=lambda f: (f.path, f.line, f.kind, f.word))


def directory_of(path: str, depth: int = 5) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "paths", nargs="*", help="files or directories (default: every tracked file)"
    )
    parser.add_argument(
        "--root", default=None, help="repository root (default: the one containing this file)"
    )
    parser.add_argument(
        "--summary", action="store_true", help="print counts per directory, not each finding"
    )
    parser.add_argument(
        "--changed",
        action="store_true",
        help="only files changed since the merge base with origin/main",
    )
    parser.add_argument("--depth", type=int, default=5, help="directory depth of --summary")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else HERE.parent.parent
    files = changed_files(root) if args.changed else tracked_files(root, args.paths)
    findings = lint(root, files)

    if args.summary:
        counts = Counter(directory_of(f.path, args.depth) for f in findings)
        for directory, count in sorted(counts.items()):
            print(f"{count:6d}  {directory}")
        print(f"{len(findings):6d}  total")
    else:
        for finding in findings:
            print(finding.render())
        print(f"english-lint: {len(findings)} finding(s) in {len(files)} file(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
