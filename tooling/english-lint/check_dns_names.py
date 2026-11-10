#!/usr/bin/env python3
"""REB-914: no Italian word in the name of a DNS record.

The English lint reads identifiers and comments, not the `name` of a `cloudflare_dns_record`,
so nothing stopped a new Italian host name. This splits every record name of
`infra/cloudflare/rebase.tf` into words (on `.`, `-`, `_` and digits) and fails on a word
from the lint's own Italian vocabulary (`vocabulary.WORDS` and `STEMS`, and the words that are
Italian only as identifiers). The rule is the 2026-11-09 row of `docs/design/DECISIONS.md`:
host names are English, a proper noun is allowed, and `pigrocrm` is one.

    python3 tooling/english-lint/check_dns_names.py [path-to-a-terraform-file]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import english_lint  # noqa: E402
import vocabulary  # noqa: E402

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "infra" / "cloudflare" / "rebase.tf"

# Words a record name may carry although the vocabulary would flag them: proper nouns, and
# the two names that stay only while the hosts move (REB-915 adds `sign` and `pigrocrm`,
# REB-918 retires `firma` and `pigro`; delete both entries with it).
ALLOWED_WORDS: frozenset[str] = frozenset(
    {
        "pigrocrm",  # the product's name, a proper noun
        "firma",  # Documenso's host until REB-918
        "pigro",  # PigroCRM's host until REB-918
    }
)

_RESOURCE = re.compile(r'^resource\s+"([^"]+)"')
_NAME = re.compile(r'^\s*name\s*=\s*"([^"]+)"')
_WORD = re.compile(r"[a-z]+")


def record_names(text: str) -> list[tuple[int, str]]:
    """The (line, name) of every `cloudflare_dns_record` in `text`."""
    names: list[tuple[int, str]] = []
    kind = ""
    for number, line in enumerate(text.splitlines(), start=1):
        resource = _RESOURCE.match(line)
        if resource:
            kind = resource.group(1)
            continue
        name = _NAME.match(line)
        if name and kind == "cloudflare_dns_record":
            names.append((number, name.group(1)))
    return names


def italian_words(name: str) -> list[str]:
    """The words of a record name that the English lint's vocabulary calls Italian and no
    allowance covers."""
    return [
        word
        for word in _WORD.findall(name.lower())
        if word not in ALLOWED_WORDS
        and (english_lint.is_italian_word(word) or word in vocabulary.IDENTIFIER_WORDS)
    ]


def check(text: str) -> list[str]:
    """One finding per Italian word, as `line N: name: word`."""
    return [
        f"line {number}: {name}: «{word}» is Italian"
        for number, name in record_names(text)
        for word in italian_words(name)
    ]


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_FILE
    text = path.read_text(encoding="utf-8")
    findings = check(text)
    for finding in findings:
        print(f"{path}: {finding}")
    if findings:
        print(
            "DNS record names are English; a proper noun is allowed (docs/design/DECISIONS.md, "
            "REB-914). Add one to ALLOWED_WORDS in tooling/english-lint/check_dns_names.py "
            "only when it is one.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{path}: {len(record_names(path.read_text(encoding='utf-8')))} record names, all English"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
