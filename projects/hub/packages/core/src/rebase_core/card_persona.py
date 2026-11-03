"""Who the person is, as the card writer reads it (REB-892, split out of `cards.py`): the
named card the model reads off the CV (`persona`, REB-855) and what it is kept with, the
identifiers that could name the person (`known_identifiers`, `with_persona`), and the
gender guard (REB-574, REB-802).
"""

import re
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.card_draft import CardDraft, clean_persona, differenze, phone_digits
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.github_source import owned_repo_names
from rebase_core.hub_facts import HubFacts
from rebase_core.llm import LlmResponse
from rebase_core.models import Freelancer, FreelancerCard, User
from rebase_core.sources import SourceSet, github_login_of, link_hostnames
from rebase_core.team_schemas import Card, CardPersonaRead, PersonaDifference

# What gives a card's person away as a woman (REB-574): Italian writes the gender into
# the role, the participle and the pronoun, and in a catalogue of a few dozen people
# that is one more hint of who they are. A hit costs a second call, and a card still
# gendered after it is a failure (REB-802), so the guard reads only what can refer to the
# person, whole words in any case. The feminine role nouns a CV turns into, one by one
# rather than by the ending, which is also «matrice», «attrice», «calcolatrice», «stessa»
# and «commessa»; «lei»; «la candidata»; and a role with no gender of its own given one
# by «una» or «un'» («una senior», «un'analista»).
_HER = re.compile(
    r"\b(?:"
    r"sviluppatrice|programmatrice|progettatrice|amministratrice|coordinatrice"
    r"|organizzatrice|collaboratrice|facilitatrice|formatrice|illustratrice"
    r"|ricercatrice|direttrice|traduttrice|redattrice|istruttrice|autrice"
    r"|(?:co-?)?fondatrice|dottoressa|professoressa|studentessa|presidentessa"
    r"|ingegnera|architetta|avvocata"
    r"|lei|(?:la|una)\s+candidata"
    r"|una\s+(?:senior|junior|consulente|analista|professionista|progettista|designer|freelance)"
    r"|un['’](?:analista|assistente|economista)"
    r")\b",
    re.IGNORECASE,
)
# A feminine participle or adjective is the person's only where no other noun can be
# the one it agrees with: at the start of a field or of a sentence («Esperta in
# cloud»), after a comma, a semicolon or a colon, where it follows the role
# («Backend developer, specializzata in API», what the preview's cards wrote), after
# «è» («È specializzata in API»), after «profilo», or made a noun by «una» or «un'»
# («un'esperta»). Directly after any other noun it is that noun's («un'agenzia
# specializzata», «una piattaforma certificata») and says nothing of the person.
_HER_PARTICIPLE = re.compile(
    r"(?:^|[.!?,;:]\s+|\b(?:è|profilo|una)\s+|\bun['’])"
    r"(specializzata|esperta|laureata|certificata|appassionata|diplomata)\b",
    re.IGNORECASE,
)


# The key a stored persona carries beside its fields: the SHA-256 of the CV it was read
# from. A write for another CV that has no persona of its own retires the stored one
# (`CardWriter._save`), so a person's contacts never outlive the CV they came from. Not a
# field of `CardPersonaRead`: reads ignore it.
PERSONA_CV = "cv_sha256"


def persona_json(persona: CardPersonaRead, digest: str) -> dict[str, Any]:
    return {**persona.model_dump(mode="json"), PERSONA_CV: digest}


def stored_persona(session: Session, freelancer_id: UUID) -> CardPersonaRead | None:
    """The named card stored for the person, or `None` (no row, no persona read yet, a card
    written before prompt version 3, or a persona read from a CV that is no longer the one
    on file: its `PERSONA_CV` tag differs from the SHA-256 of the current CV, so a person's
    old contacts are never shown beside a new CV). For the admin's page, the admin's MCP
    tool and the anonymous CV's writer, whose guard reads it as one more spelling of the
    person (REB-862): nothing else reads `freelancer_cards.persona`."""
    found = session.execute(
        select(FreelancerCard.persona, Freelancer.cv_sha256)
        .join(Freelancer, Freelancer.id == FreelancerCard.freelancer_id)
        .where(FreelancerCard.freelancer_id == freelancer_id)
    ).one_or_none()
    if found is None or found[0] is None or found[0].get(PERSONA_CV) != found[1]:
        return None
    return CardPersonaRead.model_validate(found[0])


def persona_differences(
    session: Session, persona: CardPersonaRead | None, freelancer_id: UUID
) -> list[PersonaDifference]:
    """Where the CV's persona disagrees with the profile (`users.nome`, `cognome`,
    `email`, `telefono`, `linkedin_url`): `card_draft.differenze`, read off the person's
    account. Empty without a persona."""
    if persona is None:
        return []
    row = session.get(Freelancer, freelancer_id)
    user = session.get(User, row.user_id) if row is not None else None
    if user is None:
        return []
    return differenze(
        persona,
        nome=user.nome,
        cognome=user.cognome,
        email=user.email,
        telefono=user.telefono,
        linkedin_url=user.linkedin_url,
    )


def stored_gendered(stored: Any) -> bool:
    """Whether a card as stored gives the gender away: `False` for none, and for one that
    is no longer a card, which is not this guard's to judge."""
    try:
        return bool(gendered_words(Card.model_validate(stored)))
    except ValidationError:
        return False


def gendered_words(card: Card) -> list[str]:
    """What `_HER` and `_HER_PARTICIPLE` find in the card's role, summary, skills and
    sectors, in lower case, each once, in the order written; empty for a card that tells
    nothing of the gender. `luogo` and `lingue` are a place and languages, never the
    person's.

    The gate under the prompt's rule (REB-802): a hit asks for one rewrite, and a card
    still hit after it is failed, never written. So it misses what it is not sure of, a
    feminine form not listed («è stata», «brava») or a word between the article and the
    role («una backend developer»); and a participle after a feminine subject that is
    not the person, «la piattaforma è certificata» or the rare «una startup,
    specializzata in pagamenti», is a hit, which the rewrite is told to change and an
    admin's «Rigenera scheda» asks again for."""
    found: list[tuple[int, str]] = []
    for value in (card.ruolo, card.sintesi, *card.competenze, *card.settori):
        spots = [(hit.start(), hit.group()) for hit in _HER.finditer(value)]
        spots += [(hit.start(1), hit.group(1)) for hit in _HER_PARTICIPLE.finditer(value)]
        found += sorted(spots)
    words: dict[str, None] = {}
    for _, word in found:
        words.setdefault(re.sub(r"\s+", " ", word.lower()), None)
    return list(words)


def known_identifiers(
    cognome: str, nome: str, links: list[str], sources: SourceSet, facts: HubFacts
) -> KnownIdentifiers:
    """What could name this person beyond their name: the account their links name and
    the hostname of every link (the person's own, whether or not a source is read), the
    repositories they own from the GitHub source as stored, and the names of the clients
    the hub's engagements were for. The employers the model reports are added by the
    caller for the one call."""
    repos: list[str] = []
    for item in sources.readable():
        if item.kind == "github":
            repos.extend(owned_repo_names(item.text))
    return KnownIdentifiers(
        cognome=cognome,
        nome=nome,
        github_login=github_login_of(links),
        repo_names=repos,
        hostnames=link_hostnames(links),
        employers=list(facts.clients),
    )


def persona_of(response: LlmResponse) -> tuple[CardDraft, CardPersonaRead] | None:
    """The draft the seam parsed and its cleaned persona, whether or not the card in it was
    accepted: a refusal, a cut answer and a body that is not a `CardDraft` have none. What
    the model read of the person's contacts is kept even when the guard refuses the card
    (REB-855)."""
    draft = response.parsed
    if response.stop_reason in ("refusal", "max_tokens") or not isinstance(draft, CardDraft):
        return None
    return draft, clean_persona(draft.persona, draft.employers)


def fill_persona(new: CardPersonaRead, old: CardPersonaRead) -> CardPersonaRead:
    """`new`, with every field it leaves empty taken from `old`: a rewrite of the card
    that forgot a phone the first answer had read does not lose it; the companies of both
    are kept."""
    return CardPersonaRead(
        **{
            name: getattr(new, name) or getattr(old, name)
            for name in ("nome", "cognome", "email", "telefono", "citta", "linkedin_url")
        },
        altri_link=list(dict.fromkeys([*new.altri_link, *old.altri_link])),
        aziende=list(dict.fromkeys([*new.aziende, *old.aziende])),
    )


def with_persona(
    known: KnownIdentifiers, draft: CardDraft, persona: CardPersonaRead
) -> KnownIdentifiers:
    """`known` plus what the CV itself says of the person (REB-855): its spelling of the
    name, the email address and phone number, and the hostnames and GitHub account of its
    links, so the anonymous card is checked against them too. The model's own words for an
    email or a phone are read as well as the cleaned ones: a value dropped from the named
    card for its shape is still a contact that must not reach the anonymous one."""
    links = [link for link in (persona.linkedin_url, *persona.altri_link) if link]
    raw = draft.persona
    phones = {phone_digits(value) for value in (persona.telefono, raw.telefono)}
    return replace(
        known,
        cv_cognome=persona.cognome or "",
        cv_nome=persona.nome or "",
        emails=[email for email in {persona.email, raw.email} if email and "@" in email],
        phones=[digits for digits in phones if digits],
        github_login=known.github_login or github_login_of(links),
        hostnames=list(dict.fromkeys([*known.hostnames, *link_hostnames(links)])),
    )
