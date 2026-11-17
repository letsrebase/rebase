"""The deterministic checks of the evaluation, run on every live card (REB-1128).

`rebase cards-eval` (REB-842) cross-checks a fixed sample with the rules of
`card_eval_checks` and a judge, but only when an operator runs it: a card that reads a senior
as a junior reached search unchallenged between two runs. This module runs the same rules
(`check_years`, `anonymity_leaks`) on each card the writer is about to keep, against the
sources it was written from, and turns what they find into `CardFlag`s: the fields the sources
do not support or contradict, each with a sentence of ours saying why. No model is asked, so
a live card costs nothing more to check than to write.

The flags are kept on the card row beside the conflicts the code found (`freelancer_cards.
conflicts`, a JSONB list: a flag has `kind` `unsupported` or `contradicted`, a conflict
(`card_draft.Conflict`) has its own kinds), so no column is added. The reasons are derived
from the CV (the years its dated periods add up to): they live in the hub's database only and
are shown to the admin, never written to a log, Linear or the repository.

Only what a rule can settle is flagged: the years (and the seniority, which follows them), any
field that names the person, and the skills no source text mentions (the groundedness check of
apply-new, applied to a card: each skill is looked for in the CV, the GitHub and link texts, the
hub's facts and the position the card was written from). A card with no flag is not certified
by this: the judge of `cards-eval` is still the one that reads the prose.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from typing import Any, Literal, NamedTuple

from rebase_core.card_draft import Conflict
from rebase_core.card_eval_checks import YearsCheck, anonymity_leaks, check_years
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.hub_facts import HubFacts
from rebase_core.sources import SourceSet
from rebase_core.team_schemas import Card

FlagVerdict = Literal["unsupported", "contradicted"]
FLAG_KINDS: tuple[FlagVerdict, ...] = ("unsupported", "contradicted")


class CardFlag(NamedTuple):
    """A field of a live card its sources do not support (`unsupported`) or contradict
    (`contradicted`), and why, in a sentence of ours that the admin reads."""

    field: str
    verdict: FlagVerdict
    reason: str


def _span(low: int | None, high: int | None) -> str:
    if low is None or high is None:
        return "un numero di anni diverso"
    return f"{low}" if low == high else f"da {low} a {high}"


def _years_reason(card: Card, years: YearsCheck) -> tuple[FlagVerdict, str]:
    got = card.experience_years
    if years.mode == "admin":
        return "contradicted", "Gli anni della scheda non sono quelli indicati dall'admin"
    if years.mode == "undated":
        return "unsupported", f"Il CV non riporta date, ma la scheda dichiara {got} anni"
    span = _span(years.low, years.high)
    if got is None:
        return (
            "contradicted",
            f"Il CV ha periodi professionali datati ({span} anni), la scheda non indica gli anni",
        )
    return "contradicted", f"La scheda dichiara {got} anni, le date del CV ne danno {span}"


# ---- skills --------------------------------------------------------------------------------

# The obvious pairs, written in normalised form (`_normal`): a skill and a source that spell the
# same technology differently. Explicit on purpose: nothing fuzzy, so a false flag is rare and
# can be explained.
_ALIASES: tuple[frozenset[str], ...] = tuple(
    frozenset(group)
    for group in (
        ("javascript", "js"),
        ("typescript", "ts"),
        ("postgresql", "postgres"),
        ("kubernetes", "k8s"),
        ("react", "react js", "reactjs"),
        ("node js", "node", "nodejs"),
        ("golang", "go"),
    )
)
_NOT_WORD = re.compile(r"[^a-z0-9+#]+")


def _normal(text: str) -> str:
    """`text` in lower case, without accents, punctuation (but `+` and `#`: C++, C#) or runs
    of whitespace: `Node.js` is `node js`, `Next-JS` is `next js`."""
    plain = unicodedata.normalize("NFKD", text).casefold()
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return _NOT_WORD.sub(" ", plain).strip()


def _forms(skill: str) -> set[str]:
    """The spellings a source may use for `skill`, all normalised: itself, joined (`node js` as
    `nodejs`), without a trailing `js` (`vue js`, `nodejs`) and without a trailing version
    (`python 3`, `html5`), and the explicit aliases of any of them."""
    base = _normal(skill)
    if not base:
        return set()
    forms = {base}
    for form in list(forms):
        tokens = form.split()
        if tokens and tokens[-1] == "js" and len(tokens) > 1:
            forms.add(" ".join(tokens[:-1]))
        if tokens and len(tokens[-1]) > 2 and tokens[-1].endswith("js"):
            forms.add(" ".join([*tokens[:-1], tokens[-1][:-2]]))
    for form in list(forms):
        tokens = form.split()
        while len(tokens) > 1 and tokens[-1].isdigit():
            tokens = tokens[:-1]
        stem = re.sub(r"(?<=[a-z]{2})[0-9]+$", "", tokens[-1])
        forms.add(" ".join([*tokens[:-1], stem]))
        forms.add(" ".join(tokens))
    forms |= {form.replace(" ", "") for form in forms}
    for group in _ALIASES:
        if forms & group:
            forms |= group
    return {form for form in forms if form}


def unsupported_skills(card: Card, texts: Iterable[str]) -> list[str]:
    """The entries of `card.skills` that no text of `texts` (what the card was written from)
    mentions, as whole words after `_normal`, by any of their `_forms`."""
    pool = f" {' '.join(_normal(text) for text in texts)} "
    return [
        skill for skill in card.skills if not any(f" {form} " in pool for form in _forms(skill))
    ]


def _skills_reason(missing: list[str]) -> str:
    if len(missing) == 1:
        return f"Nessuna fonte cita {missing[0]}"
    return f"Nessuna fonte cita queste competenze: {', '.join(missing)}"


def card_flags(
    card: Card,
    cv_text: str,
    facts: HubFacts,
    known: KnownIdentifiers,
    today: date,
    *,
    sources: SourceSet | None = None,
    job_title: str | None = None,
) -> list[CardFlag]:
    """The flags of `card` as it is about to be kept, in the card's field order. The years
    are read against the CV's dated periods, the hub's completed engagements and an admin's
    word, only when the CV allows a reliable parse (`YearsCheck.ok` is `None` otherwise, and
    nothing is flagged); a seniority that follows years the sources do not support is flagged
    with them. The anonymity is read against everything the person's data holds, as
    `cards-eval` does (the writer's own guard is wider and has already failed such a card, so
    this is a second reader, not a gate). The skills are read against every text the card was
    written from: the CV's, each readable source's (GitHub, the links), the hub's facts and the
    position (`unsupported_skills`); the skills no text mentions are one flag, naming them."""
    found: list[CardFlag] = []
    years = check_years(
        card,
        cv_text,
        hub_completed_months=facts.completed_months,
        experience_years_admin=facts.experience_years_admin,
        today=today,
    )
    if years.ok is False:
        verdict, reason = _years_reason(card, years)
        found.append(CardFlag("experience_years", verdict, reason))
        found.append(
            CardFlag("seniority", "unsupported", "Segue gli anni, che le fonti non confermano")
        )
    flagged = {flag.field for flag in found}
    for name in anonymity_leaks(card, known):
        if name not in flagged:
            found.append(
                CardFlag(
                    name,
                    "contradicted",
                    "Nomina la persona o qualcosa di suo (nome, account, sito, cliente)",
                )
            )
    texts = [cv_text, facts.text(), job_title or ""]
    if sources is not None:
        texts.extend(item.text for item in sources.readable())
    missing = unsupported_skills(card, texts)
    if missing:
        found.append(CardFlag("skills", "unsupported", _skills_reason(missing)))
    return found


def flags_json(flags: Iterable[CardFlag]) -> list[dict[str, Any]]:
    """`flags` as the entries of the stored list: `kind` is the verdict."""
    return [{"kind": flag.verdict, "field": flag.field, "reason": flag.reason} for flag in flags]


def split_stored(stored: list[Any] | None) -> tuple[list[Conflict], list[CardFlag]]:
    """The stored list (`freelancer_cards.conflicts`) as the conflicts and the flags it holds:
    a card written before the flags has only conflicts, and `None` is no card at all."""
    conflicts: list[Conflict] = []
    flags: list[CardFlag] = []
    for item in stored or []:
        if item.get("kind") in FLAG_KINDS:
            flags.append(CardFlag(item["field"], item["kind"], item["reason"]))
        else:
            conflicts.append(Conflict(**item))
    return conflicts, flags
