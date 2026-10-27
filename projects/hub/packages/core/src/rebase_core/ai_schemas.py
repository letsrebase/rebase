"""What «Modelli AI» and the MCP's `list_ai_config` read (REB-830). Nothing here carries
key material: `ProviderRead.key_hint` is the most a key ever shows, and the encrypted
bytes are not a field of any model in this file."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ProviderRead(BaseModel):
    provider: str
    configured: bool
    # «configurata, finisce con …abcd», or `None` while there is no key.
    key_hint: str | None
    enabled: bool
    saved_at: datetime | None
    saved_by_name: str | None


class TaskRead(BaseModel):
    task: str
    provider: str
    model: str
    effort: str | None
    max_output_tokens: int
    daily_cap_eur: Decimal | None
    enabled: bool
    spent_today_eur: Decimal
    cap_reached: bool
    # Why the task cannot be called right now, in a sentence a page can show as it
    # stands; `None` when it can.
    reason: str | None


class PriceRead(BaseModel):
    provider: str
    model: str
    input_eur_per_mtok: Decimal
    output_eur_per_mtok: Decimal
    thinking_eur_per_mtok: Decimal | None
    cache_read_eur_per_mtok: Decimal | None


class SpendRow(BaseModel):
    task: str
    provider: str
    cost_eur: Decimal
    calls: int
    # Calls made with no price for the model: counted, and never summed as free.
    unpriced_calls: int


class SpendRead(BaseModel):
    month: str  # «2026-10», the month in Rome
    rows: list[SpendRow]


class AiConfigRead(BaseModel):
    secrets_key_present: bool
    providers: list[ProviderRead]
    tasks: list[TaskRead]
    prices: list[PriceRead]
    spend: SpendRead
    # Suggestions for the model field, per provider; free text is still accepted.
    known_models: dict[str, list[str]]
