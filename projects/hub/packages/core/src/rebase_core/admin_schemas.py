"""Small admin request bodies that do not belong to one family: a status change, the vetted
flag and the CRM link."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CrmLinkBody(BaseModel):
    """`POST /api/hub/companies/{id}/crm/link` (REB-868): «Riprova» sends nothing; «Collega a
    un altro deal» sends the CRM deal's id, and the door links that deal and creates
    nothing."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID | None = None


class StatusChange(BaseModel):
    """What an admin changes on a row: where it stands. A remark about it is a note
    (`NoteCreate`), not a field of this. `extra="forbid"`: a client still sending `note`
    is told, not silently dropped."""

    model_config = ConfigDict(extra="forbid")

    stato: str = Field(min_length=1, max_length=20)


class VettedChange(BaseModel):
    """«Segna come verificato» (`true`) and «Togli la verifica» (`false`), REB-518. A
    strict bool: a word that is not one of the two is a 422, not a guess."""

    model_config = ConfigDict(extra="forbid")

    vetted: StrictBool
