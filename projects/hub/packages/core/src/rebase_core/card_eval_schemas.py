"""What the evaluation set's admin pages and `rebase cards-eval` read (REB-835). All of it
is personal data (the expected card and the card a run wrote are derived from a CV), so
it is for an admin's eyes only and no public or cloud read builds on it."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from rebase_core.team_schemas import Card


class EvalProfileRead(BaseModel):
    freelancer_id: UUID
    nome: str
    cognome: str
    label: str
    status: Literal["bozza", "accettata"]
    expected: Card | None
    corrected_by_nome: str | None
    created_at: datetime
    updated_at: datetime


class EvalResultRead(BaseModel):
    freelancer_id: UUID
    label: str
    anni_ok: bool | None
    seniority_ok: bool | None
    guard_clean: bool | None
    # The kind of failure (`cards.Failure`) when no card came out; the measures are then
    # `None`.
    failure: str | None
    latency_ms: int | None
    cost_eur: Decimal | None
    card: Card | None
    # «ruolo», «competenze», «sintesi» to 1..3 once an admin rated them.
    ratings: dict[str, int] | None


class EvalRunRead(BaseModel):
    id: UUID
    task: str
    provider: str
    model: str
    effort: str | None
    prompt_version: int
    created_at: datetime
    created_by_nome: str | None
    # `RunSummary.model_dump()`: totals, and the change against the previous run.
    summary: dict[str, Any]
    results: list[EvalResultRead]


class CardEvalRead(BaseModel):
    profiles: list[EvalProfileRead]
    runs: list[EvalRunRead]
