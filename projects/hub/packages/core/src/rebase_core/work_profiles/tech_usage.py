"""The technology usage block of a received work profile (REB-1130): its structure check and the
rows the hub shows.

`techUsage` is what the fork's `src/tech-usage.mjs` emits: per technology the sessions, the
products and the distinct months it showed up in, with a `band` derived from the months and
nothing else. Upstream's ADR-001 holds: the band is a plain-language label for the counts printed
next to it, never a score and never a measure of how well the person knows the technology.

`band_for` ports the fork's `bandFor` constant for constant, and `issues` ports the checks of
`assessStructure` (`src/consistency.mjs`) that need no logs, so a profile whose claimed band does
not match its own counts is refused here the way the fork refuses it before sending. The month
windows against the logs are the fork's alone: the hub has no logs.

The names are held to `tech-labels.json`, the labels the fork's stack detection can emit
(`labelVocabulary()`), by dropping, not refusing: a label the fork learns before the hub's copy is
refreshed must not cost the person the whole profile. `drop_unknown` removes such rows before the
payload is stored and only counts them, so a name that is stored is always a public technology
label and a repository, a client or a private package name never reaches the hub's tables. The
file is a second vendored one beside `tech-lexicon.json` because that one is tokenised and
stop-worded for prose matching, which drops «Go» and «Shell»: see `VENDORED.txt`.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any, TypeGuard
from zoneinfo import ZoneInfo

LABELS_PATH = Path(__file__).with_name("tech-labels.json")

# The fork's thresholds (`BAND_*` in `src/tech-usage.mjs`). Empirical, not calibrated against
# outcomes, and they say nothing about skill.
BAND_CONTINUOUS_MIN_MONTHS = 6
BAND_CONTINUOUS_MIN_COVERAGE = 0.75
BAND_RECURRING_MIN_MONTHS = 3

_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z", re.ASCII)


@cache
def technology_labels() -> frozenset[str]:
    """Every technology label the stack detection can emit, from the vendored artifact."""
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"]
    return frozenset(str(label) for label in labels)


def month_span(first: str, last: str) -> int:
    """Inclusive number of calendar months from `first` to `last` (`YYYY-MM`)."""
    (first_year, first_month), (last_year, last_month) = (
        (int(part) for part in month.split("-")) for month in (first, last)
    )
    return (last_year - first_year) * 12 + (last_month - first_month) + 1


def band_for(months: int, first_month: str, last_month: str) -> str:
    """The band from the counts alone: `continuous` for 6 or more distinct months covering at
    least 75% of the months between the first and the last, `recurring` for 3 or more,
    `occasional` for 1 or 2. Both months are `YYYY-MM` with the first not after the last, and
    `months` is at least 1: `issues` checks all three before it asks."""
    span = month_span(first_month, last_month)
    if months >= BAND_CONTINUOUS_MIN_MONTHS and months / span >= BAND_CONTINUOUS_MIN_COVERAGE:
        return "continuous"
    if months >= BAND_RECURRING_MIN_MONTHS:
        return "recurring"
    return "occasional"


def is_count(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_month(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _MONTH.match(value) is not None


def is_timezone(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        ZoneInfo(value)
    except (KeyError, ValueError, OSError):  # `ZoneInfoNotFoundError` is a `KeyError`
        return False
    return True


def issues(payload: dict[str, Any]) -> list[str]:
    """What is wrong with the `techUsage` block of `payload`, as sentences; nothing when the
    block is absent (it is optional) or sound. The counts are `structure`'s kind of check, so
    this runs after it and a count that is no integer is already refused there for the rest."""
    block = payload.get("techUsage")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["techUsage: serve un oggetto"]
    found: list[str] = []
    zone = block.get("timezone")
    if not is_timezone(zone):
        found.append(f"techUsage.timezone: «{zone}» non è un fuso orario IANA")
    else:
        intensity = payload.get("intensity")
        other = intensity.get("timezone") if isinstance(intensity, dict) else None
        if other and other != zone:
            found.append(
                f"techUsage.timezone è {zone} ma intensity.timezone è {other}: "
                "una generazione usa un solo fuso"
            )
    rows = block.get("technologies")
    if not isinstance(rows, list):
        found.append("techUsage.technologies: serve una lista")
        return found
    volume = payload.get("volume")
    volume = volume if isinstance(volume, dict) else {}
    seen: set[str] = set()
    for row in rows:
        row = row if isinstance(row, dict) else {}
        name = row.get("name")
        if not isinstance(name, str) or not name:
            found.append(f"techUsage: «{name}» non è un nome di tecnologia")
            continue
        where = f"techUsage.{name}"
        if name in seen:
            found.append(f"{where}: compare più di una volta")
        seen.add(name)
        first, last = row.get("firstMonth"), row.get("lastMonth")
        if not is_month(first) or not is_month(last) or first > last:
            found.append(f"{where}: i mesi {first}..{last} non sono un intervallo valido")
            continue
        months = row.get("months")
        if not is_count(months) or not 1 <= months <= month_span(first, last):
            found.append(f"{where}: {months} mesi non stanno fra {first} e {last}")
            continue
        sessions, products = row.get("sessions"), row.get("products")
        if not is_count(sessions) or not is_count(products):
            found.append(f"{where}: sessioni e prodotti sono numeri interi da zero in su")
            continue
        derived = band_for(months, first, last)
        if row.get("band") != derived:
            found.append(
                f"{where}: la fascia è «{row.get('band')}» ma i suoi numeri danno «{derived}»"
            )
        for key, count in (("products", products), ("sessions", sessions)):
            total = volume.get(key)
            if is_count(total) and count > total:
                found.append(f"{where}: {count} {key} ma il profilo ne ha {total}")
        if products > sessions:
            found.append(f"{where}: {products} prodotti ma solo {sessions} sessioni")
    return found


def drop_unknown(payload: dict[str, Any]) -> int:
    """Remove from `payload["techUsage"]["technologies"]`, in place, every row whose name is not a
    known label; how many were removed, 0 when the block is absent."""
    block = payload.get("techUsage")
    if not isinstance(block, dict):
        return 0
    rows = block.get("technologies")
    if not isinstance(rows, list):
        return 0
    labels = technology_labels()
    kept = [r for r in rows if isinstance(r, dict) and r.get("name") in labels]
    block["technologies"] = kept
    return len(rows) - len(kept)


def reads_true(name: Any, band: Any, months: Any, first: Any, last: Any) -> bool:
    """Whether a stored row is one the intake would have accepted on these points: a label the
    detector emits, a valid month window the months fit in, and the band its counts derive. The
    view applies it so a profile stored before a rule changed never shows a name or a band the
    rules refuse."""
    return (
        isinstance(name, str)
        and name in technology_labels()
        and is_month(first)
        and is_month(last)
        and first <= last
        and is_count(months)
        and 1 <= months <= month_span(first, last)
        and band == band_for(months, first, last)
    )
