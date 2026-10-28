"""What «Modelli AI» and the MCP's `list_ai_config` read (REB-830). Nothing here carries
key material: `ProviderRead.key_hint` is the most a key ever shows, and the encrypted
bytes are not a field of any model in this file."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

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


class SourceSwitchRead(BaseModel):
    """The switch of one kind of source the card is written from: `github` or `links`,
    off until the privacy notice covers reading what people linked."""

    kind: Literal["github", "links"]
    enabled: bool
    updated_at: datetime | None
    updated_by_name: str | None


class StaleCardRead(BaseModel):
    """A card that is no longer what the current prompt and inputs would write, and that
    only an admin's start regenerates: `prompt` a newer prompt version, `source` the
    hub's facts, the position or a source changed."""

    freelancer_id: UUID
    nome: str
    reason: Literal["prompt", "source"]
    generated_at: datetime | None


class CardBatchRead(BaseModel):
    """The regeneration running through the Batch API (REB-853): `count` cards sent at
    `submitted_at` (`None` while it is being sent), `done` of them settled. `status`:
    `submitting`, `in_progress` (at the provider), `ended` (applying its results)."""

    status: str
    count: int
    done: int
    submitted_at: datetime | None


class StaleCardsRead(BaseModel):
    """«Schede da aggiornare»: how many cards are stale (`count`, by reason in
    `by_reason`), what regenerating them would cost (`estimated_cost_eur`: the average
    cost of a recorded `card` call times `count`, `None` while none is recorded) and the
    oldest `items`, the ones the next start works through first."""

    count: int
    by_reason: dict[str, int]
    estimated_cost_eur: Decimal | None
    items: list[StaleCardRead]
    # From how many cards a start goes through the Batch API (half price), and the batch
    # that is running now, if one is.
    batch_threshold: int
    batch: CardBatchRead | None = None


class RegenerationStarted(BaseModel):
    """The answer of an admin's start: how many cards were queued; they are written after
    the response, one at a time."""

    queued: int


class AiConfigRead(BaseModel):
    secrets_key_present: bool
    providers: list[ProviderRead]
    tasks: list[TaskRead]
    prices: list[PriceRead]
    spend: SpendRead
    sources: list[SourceSwitchRead]
    # Suggestions for the model field, per provider; free text is still accepted.
    known_models: dict[str, list[str]]
