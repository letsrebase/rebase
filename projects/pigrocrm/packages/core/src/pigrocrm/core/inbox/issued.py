"""A sales invoice of the inbox in the issued-invoice review (design 2026-11-09, incoming
documents inbox, § 3, first rule).

The classifier decides an item is a sales invoice and nothing more: importing an invoice
consumes a register number and may create customers, so it is a person's confirmation
(DECISIONS, 2026-10-27, REB-813), and the review and the confirmation are the ones that already
exist (`invoices/importing.py`, `review_upload` and `confirm_upload`), run on the item's own
bytes. A `.p7m` is read as the XML inside it. Once every invoice of the file is on the register
(imported or already there) the item is `filed`, its bytes deleted since the invoice's own
document holds them (design § 1, Filing), and its row, hashes and reason stay.
"""

import contextlib
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict
from pigrocrm.core.inbox.access import ENTITY, visible_item
from pigrocrm.core.inbox.filetypes import basename
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.p7m import open_p7m
from pigrocrm.core.invoices.import_upload import (
    UploadedInvoiceConfirmRead,
    UploadedInvoiceReviewRead,
    UploadedPartyDecision,
)
from pigrocrm.core.invoices.importing import CONFIRM_ACTION, REVIEW_ACTION
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.base import DocumentStorage

FILED_REASON = "Fattura emessa importata nel registro"
ALREADY_REASON = "Fattura emessa gia' presente nel registro"


class InboxIssuedImport:
    def __init__(self, session: Session, storage: DocumentStorage, settings: Settings) -> None:
        self.session = session
        self.storage = storage
        self.invoices = InvoiceService(session, storage, settings)

    def _file(self, item_id: UUID, actor: Actor) -> tuple[InboxItem, str, bytes]:
        item = visible_item(self.session, item_id, actor)
        if item.kind != "sales_invoice" or item.state != "new" or item.storage_key is None:
            raise Conflict(
                ENTITY,
                "solo una fattura emessa in attesa si rivede e si conferma da qui",
                item_id=str(item.id),
            )
        content = self.storage.get(item.storage_key)
        name = basename(item.original_name)
        if item.file_type == "p7m":
            content = open_p7m(content)
            name = name.removesuffix(".p7m").removesuffix(".P7M")
        return item, name, content

    def review(self, item_id: UUID, actor: Actor) -> list[UploadedInvoiceReviewRead]:
        """The review of the issued-invoice import for this item's invoices: read only."""
        actor.require_admin(REVIEW_ACTION)  # before the item: a role is refused, not «not found»
        _, name, content = self._file(item_id, actor)
        return self.invoices.review_upload([(name, content)], actor)

    def confirm(
        self, item_id: UUID, decisions: Mapping[str, UploadedPartyDecision], actor: Actor
    ) -> list[UploadedInvoiceConfirmRead]:
        """A person's confirmation, as `confirm_upload` does it, on this item's file."""
        actor.require_admin(CONFIRM_ACTION)
        item, name, content = self._file(item_id, actor)
        rows = self.invoices.confirm_upload([(name, content)], decisions, actor)
        outcomes = {row.outcome for row in rows}
        if rows and outcomes <= {"imported", "already_present"}:
            key = item.storage_key
            item.state = "filed"
            item.storage_key = None
            item.reason = FILED_REASON if "imported" in outcomes else ALREADY_REASON
            self.session.commit()
            if key is not None:
                with contextlib.suppress(Exception):
                    self.storage.delete(key)
        return rows
