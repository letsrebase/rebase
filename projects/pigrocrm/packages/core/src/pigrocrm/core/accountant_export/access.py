"""Which stored export an actor may reach: the one rule the download, the share links and the
Drive copy all ask (spec accountant export § 5)."""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.models import AccountantExportRow
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound

ENTITY = "accountant_export"


def visible_export(
    session: Session, export_id: UUID, actor: Actor, action: str
) -> AccountantExportRow:
    """The export, for an administrator whose scope covers its legal entity (an unscoped one for
    the export of every entity), or an agent with `full_access` where `action` is classified for
    it. An export the scope hides is not told apart from one that does not exist."""
    actor.require_admin(action)
    row = session.get(AccountantExportRow, export_id)
    if row is None or (
        row.legal_entity_id is not None
        and actor.legal_entities is not None
        and row.legal_entity_id not in actor.legal_entities
    ):
        raise NotFound(ENTITY, export_id)
    if row.scope == "all_entities":
        actor.require_unscoped_admin(action)
    return row
