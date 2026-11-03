#!/usr/bin/env python3
"""Fails on Italian in the code of this repository (REB-904).

The decision of 2026-11-02, widened on 2026-11-03 (REB-908): every identifier, file name,
comment and docstring is English, and so is the prose the code publishes to a developer or
an agent (OpenAPI and MCP descriptions, CLI help). What a person reads inside the product
(UI copy, messages, template content) stays Italian and lives in string literals, which this
check never reads, except the keyword strings of `PUBLISHING_CALLS`. The mapping from the
Italian word to its English identifier is `docs/glossary.md`.

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
                the copy it describes. A published keyword string (`description=`,
                `summary=`, `title=`, `help=`, `instructions=` of `PUBLISHING_CALLS`) is
                read the same way, and up to five words one Italian word is enough.

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

# A decorator whose last attribute is one of these publishes the function's docstring: an
# OpenAPI description or an MCP tool, prompt or resource description. Since 2026-11-03 that
# prose is English like the rest (REB-908); the docstring is read like any other, and the
# decorator's own `description=`/`summary=`/`title=` strings are read as published text.
PUBLISHING_DECORATORS = {
    "tool", "prompt", "resource", "get", "post", "put", "patch", "delete", "head",
    "options", "api_route", "websocket",
}  # fmt: skip

# Calls whose keyword strings below are published: FastAPI's parameters and app, Pydantic's
# `Field` (an OpenAPI schema and an MCP tool's input schema), the MCP server and its tool
# annotations, and argparse's help. A keyword of any other call is data (a document's
# `title`, an invoice line's `description`) and stays unread like every string literal.
PUBLISHING_CALLS = PUBLISHING_DECORATORS | {
    "Field", "Query", "Path", "Body", "Header", "Cookie", "Form", "File", "FastAPI",
    "APIRouter", "include_router", "FastMCP", "ToolAnnotations", "ArgumentParser",
    "add_argument", "add_parser", "add_subparsers", "add_argument_group",
}  # fmt: skip
PUBLISHED_KEYWORDS = {
    "description", "summary", "title", "help", "instructions", "epilog",
    "response_description",
}  # fmt: skip
# An OpenAPI response object (`responses={401: {"description": ...}}`): a dict literal
# whose string keys are all among these and include `description`.
RESPONSE_OBJECT_KEYS = {"description", "content", "model", "headers", "links"}


def _is_response_object(node: ast.Dict, marked: set[int]) -> bool:
    """A dict literal with only string keys, all OpenAPI response keys, `description`
    among them; a lone `{"description": ...}` counts only where `_marked_responses` found
    it under a status code or bound to a `*_RESPONSE` name, since a lone description key
    is otherwise as likely to be data."""
    keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
    if not keys or not all(isinstance(k, str) for k in keys) or "description" not in keys:
        return False
    if not set(keys) <= RESPONSE_OBJECT_KEYS:
        return False
    return len(keys) > 1 or id(node) in marked


def _marked_responses(tree: ast.Module) -> set[int]:
    marked: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Dict)
            and node.keys
            and all(isinstance(k, ast.Constant) and isinstance(k.value, int) for k in node.keys)
        ):
            marked.update(id(v) for v in node.values if isinstance(v, ast.Dict))
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        value = getattr(node, "value", None)
        if isinstance(value, ast.Dict) and any(
            isinstance(t, ast.Name) and t.id.upper().endswith("_RESPONSE") for t in targets
        ):
            marked.add(id(value))
    return marked


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
    return [w for w in split_words(name) if is_italian_word(w) or w in vocabulary.IDENTIFIER_WORDS]


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


def _call_name(node: ast.Call) -> str:
    target = node.func
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")


def _publishes(name: str) -> bool:
    """A call whose keyword strings are published: one of `PUBLISHING_CALLS`, a router
    factory (`timeline_router`), or a helper that builds an OpenAPI response object
    (`_problem_response`), whose first argument is the response's description too."""
    return name in PUBLISHING_CALLS or name.endswith(("router", "_response", "_responses"))


def _string_constants(node: ast.AST, constants: dict[str, str]) -> str:
    """Every string a keyword's value is built from: a literal, an implicit or `+`
    concatenation, an f-string's text, or a module-level constant it names."""
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    return " ".join(
        c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)
    )


# English function words: a published text holding one of them is English prose that may
# borrow an ambiguous word (`per client`), not Italian.
_ENGLISH_FUNCTION_WORDS = frozenset((
    "the", "a", "an", "of", "to", "is", "are", "and", "or", "in", "on", "for", "with",
    "by", "from", "as", "at", "it", "its", "this", "that", "be", "not",
))  # fmt: skip


def published_is_italian(text: str) -> tuple[bool, list[str]]:
    """Stricter than a comment, since published prose is short (`title="Cliente"`,
    `"Fine del periodo"`) and nobody quotes copy in it: one Italian word that is not
    ambiguous is enough, and so are two ambiguous ones (`Il nome del comando`) in a text
    with no English function word."""
    score, hits = italian_score(text)
    if score >= 1:
        return True, hits
    words = {w.lower() for w in _PROSE_WORD_RE.findall(_QUOTED_RE.sub(" ", text))}
    return len(hits) >= 2 and not words & _ENGLISH_FUNCTION_WORDS, hits


def _check_published(path: str, line: int, text: str, what: str, out: list[Finding]) -> None:
    flagged, hits = published_is_italian(text)
    if flagged and not all(allowed(path, "comment", _fold(h)) for h in hits):
        sample = " ".join(text.split())[:70]
        detail = f'Italian {what}: "{sample}" ({", ".join(hits[:4])})'
        out.append(Finding(path, line, "comment", hits[0], detail))


def module_constants(tree: ast.Module) -> dict[str, str]:
    """The module-level string constants: `INSTRUCTIONS = "..."`, a `+` or implicit
    concatenation, an f-string's text."""
    constants: dict[str, str] = {}
    for statement in tree.body:
        if (
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and statement.value is not None
            and isinstance(statement.value, (ast.Constant, ast.JoinedStr, ast.BinOp))
        ):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            text = _string_constants(statement.value, {})
            for target in targets:
                if isinstance(target, ast.Name) and text:
                    constants[target.id] = text
    return constants


def _is_test(path: str) -> bool:
    return "/tests/" in f"/{path}" or Path(path).name.startswith("test_")


def scan_python(
    path: str, source: str, out: list[Finding], shared: dict[str, str] | None = None
) -> None:
    """`shared` holds every module's constants, so `Query(description=IMPORTED_TEXT)` is
    read with the text its own module gives the name."""
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
    texts = {**(shared or {}), **module_constants(tree)}
    marked = _marked_responses(tree)
    for node in ast.walk(tree):
        # A test publishes nothing; a dict shaped like a response object there is data.
        if isinstance(node, ast.Dict) and not _is_test(path) and _is_response_object(node, marked):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "description" and value:
                    text = _string_constants(value, texts)
                    if text:
                        _check_published(path, value.lineno, text, "response description", out)
            continue
        if isinstance(node, ast.Call) and _publishes(_call_name(node)):
            name = _call_name(node)
            if name.endswith(("_response", "_responses")) and node.args and not _is_test(path):
                text = _string_constants(node.args[0], texts)
                if text:
                    _check_published(path, node.args[0].lineno, text, "response description", out)
            for keyword_arg in node.keywords:
                if keyword_arg.arg in PUBLISHED_KEYWORDS:
                    text = _string_constants(keyword_arg.value, texts)
                    if text:
                        _check_published(
                            path, keyword_arg.value.lineno, text, f"`{keyword_arg.arg}=`", out
                        )
            continue
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)):
            continue
        doc = body[0].value.value
        if not isinstance(doc, str):
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
    shared: dict[str, str] = {}
    for path in files:
        if Path(path).suffix in PY_SUFFIXES and not skipped(path):
            try:
                tree = ast.parse((root / path).read_text(encoding="utf8", errors="replace"))
            except SyntaxError:
                continue
            shared.update(module_constants(tree))
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
            source = (root / path).read_text(encoding="utf8", errors="replace")
            scan_python(path, source, findings, shared)
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
