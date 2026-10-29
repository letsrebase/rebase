"""What the model answers when it writes a card from more than the CV (REB-839, prompt
version 2, REB-855, version 3): the `Card`, and beside it three things that are the
writer's and never the card's.

* `fonti`: for each field of the card, the sources the model says supported it. It is the
  model's claim: `verify_fonti` keeps only the sources that exist for this person, and the
  admin reads what is left as «secondo il modello».
* `employers`: every employer, client or organisation the CV names, whether or not the
  card mentions it. The anonymity guard reads them (`card_guard.identifying`), and, since
  version 3, they are stored with the persona for the admin's named card; never in a usage
  record, a log line or a conflict, so a leak cannot start there.
* `persona`: who the person is as the CV states it (name, email, phone, city, links). It
  is stored beside the card and read by the admin alone (`clean_persona`); its identifiers
  also feed the anonymity guard, so the anonymous card is checked against them. It is
  never itself checked by the guard: it is the named card.

`Card` itself does not change: the public shape, the cloud's and the talent list's read
only it. The conflicts the code finds between the CV and another source are computed here
too (`conflicts`), never asked of the model.
"""

import re
from typing import Annotated, Literal, NamedTuple
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, TypeAdapter

from rebase_core.linkedin import (
    LINKEDIN_HOST,
    canonical_linkedin,
    normalise_linkedin,
    reject_control_characters,
)
from rebase_core.models import NAME_MAX_LENGTH, TELEFONO_MAX_LENGTH
from rebase_core.team_schemas import Card, CardPersonaRead, PersonaDifference
from rebase_core.validation import SafeStr

# What the model may write for each field of the persona, far above what is kept: a long
# value is dropped by `clean_persona`, it never fails the card (REB-855).
PERSONA_TEXT_MAX = 300
PERSONA_URL_MAX = 600
EMAIL_MAX_LENGTH = 320
CITY_MAX_LENGTH = 120
LINK_MAX_LENGTH = 300
MAX_OTHER_LINKS = 5
_EMAIL: TypeAdapter[str] = TypeAdapter(EmailStr)
# A phone as it is written: an optional plus, digits, and the separators.
_PHONE = re.compile(r"\+?[\d\s().\-/]+")

# The fields of a card the model names sources for, in the card's own order.
FONTI_FIELDS = (
    "ruolo",
    "anni",
    "seniority",
    "competenze",
    "settori",
    "lingue",
    "luogo",
    "sintesi",
)
CV = "cv"
HUB = "hub"
GITHUB = "github"

# `cv`, `github`, `hub` or `link:<n>`: short on purpose, so a model that writes a sentence
# there is a shape failure, not a stored paragraph.
SourceKey = Annotated[str, StringConstraints(max_length=16)]
SourceKeys = Annotated[list[SourceKey], Field(max_length=10)]


class CardFonti(BaseModel):
    """The sources that supported each field, one list per field of the card. A fixed
    object rather than a free map: every provider's native structured output takes it,
    and a field the model forgot is a shape failure."""

    model_config = ConfigDict(extra="forbid")

    ruolo: SourceKeys
    anni: SourceKeys
    seniority: SourceKeys
    competenze: SourceKeys
    settori: SourceKeys
    lingue: SourceKeys
    luogo: SourceKeys
    sintesi: SourceKeys

    def as_dict(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in FONTI_FIELDS}


class CardPersona(BaseModel):
    """Who the person is, as the CV and the sources state it (REB-855, prompt version 3):
    each field only when a document says it literally, `None` (or an empty list) when none
    does, never a guess. Answered by the same call as the card and read by nobody but the
    admin: `clean_persona` normalises it and drops what does not pass, and it never goes
    through the anonymity guard. The limits here are far above what is kept, so a long
    value is dropped by the cleaning and never fails the card. All fields are required and
    nullable by a union, the shape every provider's strict structured output takes."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(max_length=PERSONA_TEXT_MAX)
    cognome: SafeStr | None = Field(max_length=PERSONA_TEXT_MAX)
    email: SafeStr | None = Field(max_length=PERSONA_TEXT_MAX)
    telefono: SafeStr | None = Field(max_length=PERSONA_TEXT_MAX)
    citta: SafeStr | None = Field(max_length=PERSONA_TEXT_MAX)
    linkedin_url: SafeStr | None = Field(max_length=PERSONA_URL_MAX)
    altri_link: list[Annotated[SafeStr, Field(max_length=PERSONA_URL_MAX)]] = Field(max_length=20)


class CardDraft(BaseModel):
    """The model's whole answer under prompt version 3: `card`, then the private `fonti`,
    `employers` and `persona`. Only `card` ever leaves the writer as the anonymous card.
    `employers` are the anonymity guard's input and, with `persona`, what the admin's named
    card shows (`clean_persona`): the one place a CV's identity and contacts are read."""

    model_config = ConfigDict(extra="forbid")

    card: Card
    fonti: CardFonti
    employers: list[Annotated[SafeStr, Field(max_length=120)]] = Field(max_length=60)
    persona: CardPersona


def _text(value: str | None, limit: int) -> str | None:
    """`value` on one line, trimmed, or `None` when it is empty, longer than `limit` or has
    a control character or a bidi override in it."""
    if value is None:
        return None
    one_line = " ".join(value.split())
    if not one_line or len(one_line) > limit:
        return None
    try:
        return reject_control_characters(one_line)
    except ValueError:
        return None


def _email(value: str | None) -> str | None:
    text = _text(value, EMAIL_MAX_LENGTH)
    if text is None:
        return None
    try:
        return _EMAIL.validate_python(text).lower()
    except ValueError:
        return None


def _telefono(value: str | None) -> str | None:
    """A phone as written, trimmed: digits and the separators a phone is written with,
    at most `TELEFONO_MAX_LENGTH` characters and at least six digits."""
    text = _text(value, TELEFONO_MAX_LENGTH)
    if text is None or not _PHONE.fullmatch(text) or len(phone_digits(text)) < 6:
        return None
    return text


def _linkedin(value: str | None) -> str | None:
    text = _text(value, PERSONA_URL_MAX)
    if text is None:
        return None
    try:
        return normalise_linkedin(text)
    except ValueError:
        return None


def _web_link(value: str) -> str | None:
    """A web address kept as an `http(s)` link: a bare host gets `https://`, anything with
    another scheme (`mailto:`, `javascript:`) or no host is dropped."""
    text = _text(value, LINK_MAX_LENGTH)
    if text is None or " " in text or "\\" in text:
        return None
    if "://" not in text and ":" in text.split("/", 1)[0]:
        return None  # `mailto:`, `javascript:`, `tel:`: not a bare web address
    url = text if "://" in text else f"https://{text}"
    parts = urlsplit(url)
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or "." not in parts.hostname
        or parts.username is not None
    ):
        return None
    return url


def clean_persona(persona: CardPersona, employers: list[str]) -> CardPersonaRead:
    """What the admin may read of the model's `persona` and `employers`: the name and city
    on one line; the email lower case and valid (the hub's `EmailStr` rule); the phone as
    written, trimmed, at most `TELEFONO_MAX_LENGTH`; LinkedIn in its stored shape
    (`normalise_linkedin`); the other links as `http(s)` addresses, without the LinkedIn
    profile already kept, each once, at most `MAX_OTHER_LINKS`; the companies each once.
    A value that does not pass is dropped, never an error: the contacts are a convenience
    beside the card and must not fail it."""
    linkedin = _linkedin(persona.linkedin_url)
    others: list[str] = []
    for link in persona.altri_link:
        url = _web_link(link)
        if url is None:
            continue
        host = (urlsplit(url).hostname or "").lower()
        if host == LINKEDIN_HOST or host.endswith(f".{LINKEDIN_HOST}"):
            # A LinkedIn address in the list is the person's profile when no other was given.
            if linkedin is None:
                linkedin = _linkedin(url)
            continue
        if url not in others:
            others.append(url)
    companies: dict[str, str] = {}
    for employer in employers:
        name = _text(employer, 120)
        if name is not None:
            companies.setdefault(name.casefold(), name)
    return CardPersonaRead(
        nome=_text(persona.nome, NAME_MAX_LENGTH),
        cognome=_text(persona.cognome, NAME_MAX_LENGTH),
        email=_email(persona.email),
        telefono=_telefono(persona.telefono),
        citta=_text(persona.citta, CITY_MAX_LENGTH),
        linkedin_url=linkedin,
        altri_link=others[:MAX_OTHER_LINKS],
        aziende=list(companies.values()),
    )


def phone_digits(value: str | None) -> str:
    """The digits of a phone number, nothing else: what two phones are compared by, and
    what the guard looks for."""
    return re.sub(r"\D", "", value or "")


def _same_phone(a: str, b: str) -> bool:
    """Two numbers are one when the shorter's digits end the longer's (`+39 333 1234567`
    and `333 1234567`) and it has at least six."""
    one, other = sorted((phone_digits(a).lstrip("0"), phone_digits(b).lstrip("0")), key=len)
    return len(one) >= 6 and other.endswith(one)


def _plain(value: str) -> str:
    return " ".join(value.split()).casefold()


def differenze(
    persona: CardPersonaRead,
    *,
    nome: str,
    cognome: str,
    email: str,
    telefono: str | None,
    linkedin_url: str | None,
) -> list[PersonaDifference]:
    """The fields where the CV and the profile both have a value and the values differ,
    in the order `nome`, `cognome`, `email`, `telefono`, `linkedin_url`: names and email
    compared without case and spacing, the phone by its digits, LinkedIn by its stored
    shape. A field one side leaves empty is not listed: the page shows it as a gap."""
    pairs: list[
        tuple[
            Literal["nome", "cognome", "email", "telefono", "linkedin_url"], str | None, str | None
        ]
    ] = [
        ("nome", persona.nome, nome),
        ("cognome", persona.cognome, cognome),
        ("email", persona.email, email),
        ("telefono", persona.telefono, telefono),
        ("linkedin_url", persona.linkedin_url, linkedin_url),
    ]
    found: list[PersonaDifference] = []
    for campo, cv, profilo in pairs:
        if not cv or not profilo:
            continue
        if campo == "telefono":
            same = _same_phone(cv, profilo)
        elif campo == "linkedin_url":
            same = (canonical_linkedin(cv) or "").lower() == (
                canonical_linkedin(profilo) or ""
            ).lower()
        else:
            same = _plain(cv) == _plain(profilo)
        if not same:
            found.append(PersonaDifference(campo=campo, cv=cv, profilo=profilo))
    return found


def verify_fonti(fonti: dict[str, list[str]], available: set[str]) -> dict[str, list[str]]:
    """`fonti` with every source that does not exist for this person dropped (`link:9`
    for a person with two links, `github` for one with no account read), each source once,
    in the order the model gave. Every field is present in the answer."""
    return {
        name: list(dict.fromkeys(key for key in fonti.get(name, []) if key in available))
        for name in FONTI_FIELDS
    }


class Conflict(NamedTuple):
    """A disagreement the code found, stored on the card row as a plain dict.
    `anni_diversi`: the hub's completed engagements add up to more years than the card's
    `anni_scheda` (the CV's dates) by more than one; `anni_hub` is the hub's months in
    whole years. `anni_scartata`: the card gave `anni_scheda` years but no source that
    dates a profession supports them, so the code dropped them; `anni_hub` is `None`."""

    kind: str
    anni_scheda: int | None
    anni_hub: int | None


# One year of slack: the CV's dates and the hub's months are counted differently (a
# month's rounding, a role that ended in the month it started).
YEARS_TOLERANCE = 1


def conflicts(cv_years: int | None, hub_completed_months: int) -> list[Conflict]:
    """What the CV's years and the hub's completed engagements disagree on. The card
    follows the CV's dates, so the only disagreement that says something is the hub
    showing more real work than the card counts: a short engagement against a long career
    is not one. `cv_years` is `None` when the CV dates no role: the hub may then give the
    years, and there is nothing to disagree with."""
    if cv_years is None:
        return []
    hub_years = hub_completed_months // 12
    if hub_years - cv_years > YEARS_TOLERANCE:
        return [Conflict("anni_diversi", cv_years, hub_years)]
    return []


def dated_by(fonti: dict[str, list[str]]) -> bool:
    """Whether the sources named for `anni` include one that dates a profession: the CV
    or the hub's own engagements. GitHub, a page and the wizard never set the years."""
    return bool({CV, HUB} & set(fonti.get("anni", [])))
