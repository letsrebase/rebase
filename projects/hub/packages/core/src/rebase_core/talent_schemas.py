"""The talent: a freelancer card as the admin's search and the MCP tools read it."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from rebase_core.bands import Band
from rebase_core.schema_common import ResponseModel

TalentoOrigine = Literal["form", "wizard", "admin"]


class TalentoRead(ResponseModel):
    """One row of `talenti` (REB-282): every freelancer card and every bare sign-up (a
    `signups` row with no card, ORB-163) as one row, `stato` `lead` for the bare ones
    and the freelancer's own state otherwise. `origine` names how the row came to be --
    `form` for a bare sign-up (the landing's own sign-up form), `wizard` for a card the
    person filled in themselves (`compilata_da == "persona"`), `admin` for one an admin
    drafted from research (`compilata_da == "admin"`, ORB-155). Distinct from
    `Freelancer.origine`/`Signup`'s own UTM columns, which name the page and the
    campaign a submission started from, not the channel that created the row.

    `tariffa_giornaliera` and `remoto` (REB-558) are a card's own two columns, the same
    ones `FreelancerRead` carries; a bare sign-up has neither, so both stay `None` for
    a `lead` row.

    `posizione` is the card's own, and `ruolo`, `seniority`, `anni`, `competenze`,
    `settori`, `lingue`, `luogo` and `sintesi` are read off Claude's anonymous card
    (REB-778, REB-798), so an agent matching a request reads what a person does from the
    list itself rather than one profile at a time; `fascia` is the client's price band
    of the card's own rate (`bands.band_for`), read at the same moment. Those nine are
    empty for a talent with no live anonymous card (`fascia` follows the rate, not the
    card), and all of them for a lead. One builder, `talenti.card_fields`, fills them
    for the list and for the MCP `get_talento`, so the two cannot disagree."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str | None = None
    cognome: str | None = None
    email: str
    linkedin_url: str | None = None
    stato: str
    origine: TalentoOrigine
    utm_source: str | None = None
    created_at: datetime
    tariffa_giornaliera: Decimal | None = None
    remoto: str | None = None
    # REB-518: the «Verificato» pill, and whether Claude has an anonymous card of this
    # talent the team builder can propose (a live one: a retired card is none). A bare
    # sign-up has neither.
    vetted_at: datetime | None = None
    ha_scheda_anonima: bool = False
    posizione: str | None = None
    ruolo: str | None = None
    seniority: str | None = None
    anni: int | None = None
    # The seniority is a guess: the card has no dated years and no admin corrected it
    # (REB-799). `seniority` and `anni` are the admin's when one corrected them.
    seniority_incerta: bool = False
    competenze: list[str] = Field(default_factory=list)
    luogo: str | None = None
    settori: list[str] = Field(default_factory=list)
    lingue: list[str] = Field(default_factory=list)
    sintesi: str | None = None
    fascia: Band | None = None
    # The other live cards that may be this person (REB-800): the same LinkedIn profile,
    # name or telephone number, by freelancer id. Empty for almost everyone, and for a
    # lead. `rebase_core.duplicates` says which signal; an admin merges from there.
    possibile_duplicato_di: list[UUID] = Field(default_factory=list)


class TalentoList(ResponseModel):
    """Newest-or-best-match first across both tables (REB-282; REB-285 adds search and
    the cursor), at most `limit` rows. `totale` counts every row every active filter
    selects, `stato` included, not only the page returned; `per_stato` is the same
    count broken down by state with every filter but `stato` applied -- the numbers
    the admin area's tabs need beside the page itself, answering "how many if I picked
    this one" rather than "how many exist at all". `next_cursor` is `None` on the last
    page."""

    totale: int
    items: list[TalentoRead]
    per_stato: dict[str, int]
    next_cursor: str | None = None
