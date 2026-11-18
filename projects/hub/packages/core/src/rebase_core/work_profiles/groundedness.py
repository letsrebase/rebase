"""The groundedness check of a work profile, ported from the fork's `src/groundedness.mjs`.

A work profile mixes deterministic fields (numbers, tags, stack) with prose written by the
person's own model on their machine (summary, narratives, a project's `did`). The prose can drift
from the data: claim numbers that are not in the logs, name a technology nobody touched. This is
not a fact-checker. It extracts ANCHORS from the prose (numbers, percents, year-months,
technology names from the vendored lexicon, type tags) and checks each against the structured
data the prose was written from. The score is the percentage of anchors supported.

The hub recomputes it on what it receives (spec 2026-11-17 § 3 step 5) and refuses under 60, as
the CLI does. The port is line for line so the two agree: `test_work_profile_groundedness.py`
carries upstream's own test vectors, so an upstream change that moves a score fails a test when
the fork merges it. JavaScript's `Math.round` rounds a half up, Python's `round` to even, so
`_round` is used wherever upstream rounds. Two things differ on purpose and cannot move a score:
the tech lexicon is the checked-in artifact, sorted (upstream builds it in insertion order), so
only the ORDER of the surfaced anomalies may differ, and the regular expressions are ASCII
(`re.ASCII`), as JavaScript's `\\b` and `\\d` are.

Provenance and licence of this module and `tech-lexicon.json`: `VENDORED.txt` beside it.
"""

import json
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

LEXICON_PATH = Path(__file__).with_name("tech-lexicon.json")

# Below this many anchors the prose cannot be judged: the score is `None`, not 0.
MIN_ANCHORS = 4
# The anomalies a result carries; the rest are counted in `total` and not listed.
MAX_ANOMALIES = 12

# AI-tooling vocabulary: always considered grounded, since the person uses these tools to make
# the profile at all.
_AI_TOOLING = (
    "claude",
    "claude code",
    "claude.ai",
    "anthropic",
    "openai",
    "gpt",
    "gemini",
    "codex",
    "sdk",
    "api",
    "mcp",
    "agent",
    "agents",
    "subagent",
    "sub-agent",
    "skill",
    "skills",
    "llm",
    "llms",
    "prompt",
    "tool use",
    "tool-use",
    "reasoning",
    "thinking",
    "context window",
)

_TYPE_TAGS = (
    "product-build",
    "audit-research",
    "agent-tooling",
    "static-site",
    "ai-platform",
    "data-migration",
    "feature-work",
    "testing",
    "quality-gating",
    "orchestrated",
    "design-research",
    "exploration",
)

_COGNITIVE_TAGS = (
    "research-first",
    "decomposer",
    "orchestrator",
    "verification-heavy",
    "risk-calibrated",
)

# JavaScript's `\s` (ECMAScript WhiteSpace and LineTerminator), which `re.ASCII` does not give.
_JS_SPACE = "\\t\\n\\v\\f\\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_PERIOD = r"\b20\d{2}-(?:Q[1-4]|0?[1-9]|1[0-2])\b"
_NUMBER_RE = re.compile(r"\b\d{1,5}(?:[.,]\d+)?\b", re.ASCII)
_PERCENT_RE = re.compile(rf"(\d{{1,3}})[{_JS_SPACE}]*%", re.ASCII)
_PERIOD_RE = re.compile(_PERIOD, re.ASCII)
# What `splitTech` splits on, and what it keeps.
_SPLIT_RE = re.compile(rf"[{_JS_SPACE}/(),]+")
_UNKEPT_RE = re.compile(r"[^a-z.+-]")


@dataclass(frozen=True)
class Anchor:
    kind: str
    value: float | str
    raw: str


@dataclass(frozen=True)
class Groundedness:
    """`score` is 0-100, or `None` when fewer than `MIN_ANCHORS` anchors could be checked.
    `anomalies` is at most `MAX_ANOMALIES` of `{where, anchor, kind}`."""

    score: int | None
    supported: int
    total: int
    anomalies: list[dict[str, str]]


@cache
def tech_lexicon() -> tuple[str, ...]:
    """The technology tokens the prose is checked for, from the vendored artifact."""
    tokens = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))["tokens"]
    return tuple(str(token) for token in tokens)


@cache
def _tech_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (token, re.compile(rf"\b{re.escape(token)}\b", re.ASCII | re.IGNORECASE))
        for token in tech_lexicon()
    )


@cache
def _tooling_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (word, re.compile(rf"\b{re.escape(word)}\b", re.ASCII | re.IGNORECASE))
        for word in _AI_TOOLING
    )


def _round(value: float) -> int:
    """JavaScript's `Math.round`: a half goes up."""
    return math.floor(value + 0.5)


_DECIMAL = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\Z", re.ASCII)
_RADIX = re.compile(r"0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)\Z", re.ASCII)
_JS_TRIM = re.compile(rf"^[{_JS_SPACE}]+|[{_JS_SPACE}]+$")


def _js_string(value: Any) -> str:
    """`String(value)` for the JSON values `Number` is applied to: an array joins its
    elements with a comma, `null` inside one is empty, an object is `[object Object]`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_js_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def _number_of_string(text: str) -> float | None:
    """`Number("...")` where it is finite: no underscores, `0x10` is 16, `""` is 0."""
    trimmed = _JS_TRIM.sub("", text)
    try:
        if not trimmed:
            return 0.0
        if _RADIX.match(trimmed):
            return float(int(trimmed, 0))
        if _DECIMAL.match(trimmed):
            number = float(trimmed)
            return number if math.isfinite(number) else None
    except (ValueError, OverflowError):
        return None
    return None


def _number(value: Any) -> float | None:
    """JavaScript's `Number(value)` where it is finite, `None` where it is not (`NaN`,
    `Infinity`, an object, a string that is not a number)."""
    if value is None:
        return None  # callers skip `null` before they ask, as upstream's `n == null` does
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        try:
            number = float(value)
        except OverflowError:
            return None
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        return _number_of_string(value)
    if isinstance(value, list):
        return _number_of_string(_js_string(value))
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> Iterable[dict[str, Any]]:
    return (item for item in _list(value) if isinstance(item, dict))


def split_tech(label: Any) -> list[str]:
    """`"Supabase/Postgres"` or `"Playwright (E2E)"` make both `supabase`, `postgres` and
    `playwright` individually grounded."""
    parts = _SPLIT_RE.split(str(label).lower())
    return [kept for part in parts if len(kept := _UNKEPT_RE.sub("", part)) >= 3]


def extract_anchors(text: Any) -> list[Anchor]:
    """The verifiable anchors of a chunk of prose."""
    if not text or not isinstance(text, str):
        return []
    anchors: list[Anchor] = []
    # Numbers that look meaningful (two or more).
    for match in _NUMBER_RE.finditer(text):
        value = float(match.group(0).replace(",", "."))
        if math.isfinite(value) and value >= 2:
            anchors.append(Anchor("number", value, match.group(0)))
    for match in _PERCENT_RE.finditer(text):
        anchors.append(Anchor("percent", int(match.group(1)) / 100, match.group(0)))
    for match in _PERIOD_RE.finditer(text):
        anchors.append(Anchor("period", match.group(0), match.group(0)))
    for token, pattern in _tech_patterns():
        if pattern.search(text):
            anchors.append(Anchor("tech", token.lower(), token))
    for word, pattern in _tooling_patterns():
        if pattern.search(text):
            anchors.append(Anchor("ai-tooling", word, word))
    lowered = text.lower()
    for tag in (*_TYPE_TAGS, *_COGNITIVE_TAGS):
        if tag in lowered:
            anchors.append(Anchor("tag", tag, tag))
    return anchors


class _Pool:
    """What the prose may cite. Percent anchors have a pool of their own: the number pool is
    full of COUNTS, and a count collides with a percent at the fractions' edges (a 1 anywhere
    equals the «100%» anchor, a 0 the «0%»), so any such profile would silently ground any
    fabricated percentage. Only share and rate fields feed `ratios`."""

    def __init__(self) -> None:
        self.numbers: set[float] = set()
        self.ratios: set[float] = set()
        self.periods: set[str] = set()
        self.tech: set[str] = set()
        self.tags: set[str] = set()

    def add_number(self, value: Any) -> None:
        number = _number(value)
        if number is not None:
            self.numbers.add(number)

    def add_ratio(self, value: Any) -> None:
        number = _number(value)
        if number is not None:
            self.ratios.add(number)

    def add_text(self, value: Any) -> None:
        if value:
            self.periods.update(_PERIOD_RE.findall(str(value)))

    def add_count(self, value: Any) -> None:
        """Counts below two are never citable (number anchors start at 2), so they stay out."""
        number = _number(value)
        if number is not None and number >= 2:
            self.numbers.add(number)

    def add_percent_of(self, fraction: Any) -> None:
        number = _number(fraction)
        if number is not None and math.isfinite(number * 100):
            self.numbers.add(float(_round(number * 100)))


def collect_support_pool(profile: dict[str, Any]) -> _Pool:
    pool = _Pool()
    volume = _dict(profile.get("volume"))
    window = _dict(profile.get("window"))

    pool.add_number(volume.get("sessions"))
    pool.add_number(volume.get("products"))
    pool.add_number(volume.get("instructions"))
    pool.add_number(_dict(profile.get("authenticity")).get("score"))
    # Per-source capture counts are citable («941 sessions read from ...»).
    for source in _dicts(profile.get("sources")):
        pool.add_number(source.get("sessions"))
    pool.add_text(window.get("from"))
    pool.add_text(window.get("to"))

    for project in _dicts(profile.get("projects")):
        landing = _dict(project.get("landing"))
        metrics = _dict(project.get("metrics"))
        orchestration = _dict(metrics.get("orchestration"))
        span = _dict(project.get("span"))
        pool.add_number(project.get("sessions"))
        pool.add_number(landing.get("commits"))
        pool.add_number(landing.get("reverts"))
        pool.add_number(metrics.get("researchToMutation"))
        pool.add_number(metrics.get("delegation"))
        # Orchestration counts a narrative may cite («N dispatches across M CLIs»).
        pool.add_count(orchestration.get("dispatchCommands"))
        pool.add_count(orchestration.get("toolCount"))
        # The narrative is fed the per-CLI session split too.
        for value in _dict(orchestration.get("tools")).values():
            pool.add_count(value)
        pool.add_text(span.get("from"))
        pool.add_text(span.get("to"))
        for tech in _list(project.get("tech")):
            pool.tech.update(split_tech(tech))
        pool.tags.update(str(tag) for tag in _list(project.get("type")))

    for other in _dicts(profile.get("otherProjects")):
        span = _dict(other.get("span"))
        pool.add_number(other.get("sessions"))
        pool.add_text(span.get("from"))
        pool.add_text(span.get("to"))
        pool.tags.update(str(tag) for tag in _list(other.get("type")))

    for tech in _list(profile.get("stackAdopted")):
        pool.tech.update(split_tech(tech))
    pool.tags.update(str(tag) for tag in _list(_dict(profile.get("cognitive")).get("tags")))

    # The domains' counts (sum-checked in `assess_groundedness`; once they pass, the prose may
    # cite them).
    for domain in _dicts(profile.get("domains")):
        pool.add_number(domain.get("products"))
        pool.add_number(domain.get("sessions"))

    # Practice intensity: the prose may quote any of it, raw or as a percent (the fraction
    # feeds the ratios, the rounded percent stays a citable number).
    intensity = _dict(profile.get("intensity"))
    if intensity:
        for key in (
            "observedDays",
            "activeDays",
            "longestStreak",
            "medianSessionsPerActiveDay",
            "medianSessionToolCalls",
            "peakDayToolCalls",
        ):
            pool.add_number(intensity.get(key))
        pool.add_ratio(intensity.get("activeDaysRatio"))
        pool.add_percent_of(intensity.get("activeDaysRatio"))

    # Work distribution (the same fraction and rounded-percent split).
    distribution = _dict(profile.get("distribution"))
    if distribution:
        pool.add_number(distribution.get("meanSessionsPerProduct"))
        pool.add_number(distribution.get("medianSessionsPerProduct"))
        pool.add_number(distribution.get("multiMonthProducts"))
        pool.add_ratio(distribution.get("top3Share"))
        pool.add_percent_of(distribution.get("top3Share"))
        pool.add_ratio(distribution.get("multiMonthShare"))
        pool.add_percent_of(distribution.get("multiMonthShare"))

    # Technology-usage counts (the prose may cite "Supabase in 7 months", REB-1130). Names already
    # join the tech pool through the projects' stacks; names seen only in non-representative
    # products are pooled here. Months pool as year-months too.
    for usage in _dicts(_dict(profile.get("techUsage")).get("technologies")):
        pool.tech.update(split_tech(usage.get("name")))
        pool.add_number(usage.get("sessions"))
        pool.add_number(usage.get("products"))
        pool.add_number(usage.get("months"))
        pool.add_text(usage.get("firstMonth"))
        pool.add_text(usage.get("lastMonth"))

    # Practice counts (the prose may cite "in 18 of 25 sessions, over 4 months", REB-1144).
    for item in _dicts(_dict(profile.get("practices")).get("items")):
        pool.add_number(item.get("seen"))
        pool.add_number(item.get("of"))
        pool.add_number(item.get("products"))
        pool.add_number(item.get("months"))

    # Trajectory. A percent-format shift holds fractions the prose cites as percents; the
    # others (prompt words, research:mutation) are cited as plain numbers.
    trajectory = _dict(profile.get("trajectory"))
    shifts = _dict(trajectory.get("shifts"))
    for delta in _dicts(shifts.get("deltas")):
        if delta.get("format") == "percent":
            pool.add_ratio(delta.get("early"))
            pool.add_ratio(delta.get("late"))
            pool.add_percent_of(delta.get("early"))
            pool.add_percent_of(delta.get("late"))
        else:
            pool.add_number(delta.get("early"))
            pool.add_number(delta.get("late"))
    if shifts.get("midpoint"):
        pool.add_text(shifts["midpoint"])
    for topic in _dicts(trajectory.get("topics")):
        pool.add_text(topic.get("quarter"))
        for theme in _dicts(topic.get("themes")):
            pool.add_number(theme.get("count"))
    for principle in _dicts(trajectory.get("principlesAdopted")):
        pool.add_text(principle.get("when"))

    return pool


_PAD = 1e-9


class _Matcher:
    """`numberMatches` of upstream, without its scan of the whole pool for every anchor: a value
    matches a pooled number that equals it or is within 5% of it, and the pooled numbers
    within 5% of `n` lie in `[n / 1.05, n / 0.95]` (the other sign never matches), so the sorted
    pool is searched for that window and the exact predicate is applied to what is in it. The
    window is padded, so a rounding at its edge never decides; parity is the predicate's."""

    def __init__(self, numbers: set[float]) -> None:
        self.numbers = numbers
        self.ordered = sorted(numbers)
        self._known: dict[float, bool] = {}

    def matches(self, value: float) -> bool:
        known = self._known.get(value)
        if known is None:
            known = self._known[value] = self._decide(value)
        return known

    def _decide(self, value: float) -> bool:
        if value in self.numbers:
            return True
        if value == 0:
            return False
        low, high = sorted((value / 1.05, value / 0.95))
        start = bisect_left(self.ordered, low - abs(low) * _PAD)
        stop = bisect_right(self.ordered, high + abs(high) * _PAD)
        for candidate in self.ordered[start:stop]:
            if candidate != 0 and abs((value - candidate) / candidate) <= 0.05:
                return True
        return False


def classify_text_fields(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """The prose fields to verify, as `(where, text)`."""
    fields: list[tuple[str, str]] = []

    def add(where: str, text: Any) -> None:
        if text:
            fields.append((where, text))

    add("summary", profile.get("summary"))
    add("cognitive.narrative", _dict(profile.get("cognitive")).get("narrative"))
    add("trajectory.narrative", _dict(profile.get("trajectory")).get("narrative"))
    add("distribution.narrative", _dict(profile.get("distribution")).get("narrative"))
    add("intensity.narrative", _dict(profile.get("intensity")).get("narrative"))
    for index, domain in enumerate(_list(profile.get("domains"))):
        add(f"domains[{index}].note", _dict(domain).get("note"))
    for project in _dicts(profile.get("projects")):
        project_id = project.get("id")
        add(f"{project_id}.domain", project.get("domain"))
        add(f"{project_id}.did", project.get("did"))
        add(f"{project_id}.whyRepresentative", project.get("whyRepresentative"))
    for principle in _dicts(_dict(profile.get("trajectory")).get("principlesAdopted")):
        add("trajectory.principles", principle.get("text"))
    return fields


def _supported(anchor: Anchor, pool: _Pool, numbers: _Matcher, ratios: _Matcher) -> bool:
    if anchor.kind == "number" and isinstance(anchor.value, float):
        return numbers.matches(anchor.value)
    if anchor.kind == "percent" and isinstance(anchor.value, float):
        return ratios.matches(anchor.value)
    if anchor.kind == "period":
        return anchor.value in pool.periods
    if anchor.kind == "tech":
        return anchor.value in pool.tech
    if anchor.kind == "tag":
        return anchor.value in pool.tags
    # `ai-tooling` is grounded by definition.
    return anchor.kind == "ai-tooling"


class TooManyAnchors(Exception):
    """The prose holds more anchors than a profile can honestly carry."""


def assess_groundedness(profile: dict[str, Any], max_anchors: int | None = None) -> Groundedness:
    """The score of a profile. `max_anchors`, when given, stops the work with `TooManyAnchors`
    past that many anchors: a person's profile has a few dozen, and the intake must not spend
    its time on a payload made of them."""
    pool = collect_support_pool(profile)
    numbers, ratios = _Matcher(pool.numbers), _Matcher(pool.ratios)
    total = 0
    supported = 0
    anomalies: list[dict[str, str]] = []

    for where, text in classify_text_fields(profile):
        for anchor in extract_anchors(text):
            total += 1
            if max_anchors is not None and total > max_anchors:
                raise TooManyAnchors
            if _supported(anchor, pool, numbers, ratios):
                supported += 1
            else:
                anomalies.append({"where": where, "anchor": anchor.raw, "kind": anchor.kind})

    # The domains' rollup: per-domain products and sessions are the model's claims, and their
    # sums must not exceed the deterministic totals. An excess is an invented count.
    domains = list(_dicts(profile.get("domains")))
    if domains:
        volume = _dict(profile.get("volume"))
        sum_products = sum(_number(d.get("products")) or 0 for d in domains)
        sum_sessions = sum(_number(d.get("sessions")) or 0 for d in domains)
        total += 2
        if sum_products <= (_number(volume.get("products")) or 0):
            supported += 1
        else:
            anomalies.append(
                {
                    "where": "domains",
                    "anchor": f"{_plain(sum_products)} products across domains",
                    "kind": "number",
                }
            )
        if sum_sessions <= (_number(volume.get("sessions")) or 0):
            supported += 1
        else:
            anomalies.append(
                {
                    "where": "domains",
                    "anchor": f"{_plain(sum_sessions)} sessions across domains",
                    "kind": "number",
                }
            )

    # Soft floor: fewer than four anchors cannot be judged, so the score is `None`.
    score = _round(supported / total * 100) if total >= MIN_ANCHORS else None
    return Groundedness(score, supported, total, anomalies[:MAX_ANOMALIES])


def _plain(value: float) -> str:
    """A number as JavaScript prints it in a template: `5`, not `5.0`."""
    if not math.isfinite(value):
        return str(value)
    return str(int(value)) if value == int(value) else str(value)
