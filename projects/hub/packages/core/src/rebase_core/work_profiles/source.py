"""An approved work profile as a source of the anonymous card (REB-1134, spec 2026-11-17 § 6).

The card writer reads the profile the way it reads the hub's facts (`hub_facts.py`): the hub's own
data, read from `work_profiles` when the card is composed, never copied into
`freelancer_sources`. What it reads is a few lines of text: the summary, the domains, each
representative project's domain and stack, and the agentic-literacy narrative. No date, no count
and no window, so the profile can inform skills and industries and never the years (the sources
beside the CV never give years). Its key is `work_profile`.

The text goes through the anonymity guard before it is a source. The profile was redacted on the
person's own machine and an admin read it, but a source that names the person, a link of theirs or
a client of theirs is not given to the model at all: the card it would write would only be refused.
The guard reads it as it reads a card (`card_guard.identifying`): the surname, the account and the
sites the person's links name, and the clients the hub's engagements were for. The first name is
left out of this reading, since first names are ordinary words, and the card's own guard still reads
it in what the model writes.
"""

import logging
from typing import Any

from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.team_schemas import Card
from rebase_core.work_profiles.view import _dict, _dicts, _text, _texts

logger = logging.getLogger(__name__)

HEADER = "FONTE: profilo di lavoro (approvato da un admin)"


def _lines(payload: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """The prose, the domains' labels and the stack of a profile, each in reading order."""
    prose: list[str] = []
    labels: list[str] = []
    stack: list[str] = []
    if summary := _text(payload.get("summary")):
        prose.append(f"Riassunto: {summary}")
    domains: list[str] = []
    for entry in _dicts(payload.get("domains")):
        if label := _text(entry.get("label")):
            labels.append(label)
            note = _text(entry.get("note"))
            domains.append(f"- {label}: {note}" if note else f"- {label}")
    if domains:
        prose.append("Ambiti di lavoro:\n" + "\n".join(domains))
    projects: list[str] = []
    for project in _dicts(payload.get("projects")):
        about, tech = _text(project.get("domain")), _texts(project.get("tech"))
        if not about and not tech:
            continue
        stack.extend(tech)
        line = f"- {about}" if about else "- progetto"
        projects.append(f"{line} (stack: {', '.join(tech)})" if tech else line)
    if projects:
        prose.append("Progetti rappresentativi:\n" + "\n".join(projects))
    narrative = _text(_dict(payload.get("agenticLiteracy")).get("narrative"))
    if narrative:
        prose.append(f"Fluenza con gli agenti: {narrative}")
    return prose, labels, list(dict.fromkeys(stack))


def source_text(payload: dict[str, Any], known: KnownIdentifiers) -> str | None:
    """The text of the profile as a source, or `None` when it has nothing to say or the
    anonymity guard finds the person (or a link or a client of theirs) named in it."""
    prose, labels, stack = _lines(payload)
    if not prose:
        return None
    body = "\n".join(prose)
    # The card the guard reads: the whole text as the summary, the domains as the sectors, the
    # stack as the skills. `model_construct` skips the card's own limits, which are not this
    # text's: only the fields' content is read.
    reading = Card.model_construct(
        role="",
        experience_years=None,
        seniority="mid",
        skills=stack,
        industries=labels,
        languages=[],
        location=None,
        profile_summary=body,
    )
    if identifying(reading, known):
        logger.warning(
            "work profile left out of a card: it names the person or something of theirs"
        )
        return None
    return f"{HEADER}\n{body}"
