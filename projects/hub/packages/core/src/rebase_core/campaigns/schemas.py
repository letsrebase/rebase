"""Every shape campaigns read and answer, API and service alike."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from rebase_core.search import SEARCH_MAX_LENGTH


class TalentiFiltri(BaseModel):
    """The Talenti list's own filters (`GET /api/hub/talent`, `talenti.py:191`), stored
    on the campaign and replayed through `TalentiService` when the list is shown and when
    a mail is about to leave."""

    lista: Literal["talenti"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    posizione: str | None = Field(default=None, max_length=160)
    remoto: str | None = Field(default=None, max_length=10)
    tariffa_min: Decimal | None = None
    tariffa_max: Decimal | None = None
    origine: str | None = Field(default=None, max_length=40)
    utm_source: str | None = Field(default=None, max_length=200)
    has_cv: bool | None = None
    con_accessi: bool | None = None
    creato_da: datetime | None = None
    creato_a: datetime | None = None


class AziendeFiltri(BaseModel):
    """The company list's own filters (`companies.py:160`)."""

    lista: Literal["aziende"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    periodo_da: date | None = None
    origine: str | None = Field(default=None, max_length=40)
    creato_da: datetime | None = None
    creato_a: datetime | None = None


Filtri = Annotated[TalentiFiltri | AziendeFiltri, Field(discriminator="lista")]
