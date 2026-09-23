"""What `IdentityService.enter` hands back once a click has been verified: the
identity, and nothing about any space -- `pigrocrm.core.identity` never opens one."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IdentityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
