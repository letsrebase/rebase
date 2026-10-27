from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    kind: str
    actor_id: UUID | None
    # The member's name, read from `users` by `actor_id` when the row is served (never
    # copied into the row, so a renamed member is renamed everywhere), and kept for a
    # deactivated member: «who did this» must outlive the account. `None` for a row
    # with no member behind it (`system`, `rebase`) or whose id no user row holds.
    actor_name: str | None = None
    actor_type: str
    # An automation's entry is written by `system`, with the member whose action set it
    # off in `payload.attivata_da` (§9.5). Their name, resolved like `actor_name`, so the
    # timeline can say «attivata da Mario» and not only that the system did it.
    triggered_by_name: str | None = None
    payload: dict[str, Any]
    occurred_at: datetime
