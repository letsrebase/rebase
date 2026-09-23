"""Whether a space has any work in it yet (spec 2026-09-16 §4.1, REB-222).

The Home of an empty space is the three-block start page instead of the dashboard, and
the browser decides that from four one-row list reads (`useFirstSteps` in the web app).
The API needs the same answer in one place of its own: the Gmail consent flow ends on the
Home when the space is empty, because that is where its «Collega Gmail» door is, and on
the Gmail settings page otherwise (`routers/gmail.py`).

The four tables are the web's four, and nothing else: a customer, a deal, a time entry, a
document. The emitter and the personal token are steps of the start page, not work, so a
space with only its fiscal data saved is still empty. `deleted_at IS NULL` on each,
because every one carries `SoftDeleteMixin`, core applies no global filter, and the lists
the browser reads skip deleted rows: a space whose only customer was deleted reads as
empty on both sides.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.timetracking.models import TimeEntry

_WORK = (Customer, Deal, TimeEntry, Document)


def space_is_empty(session: Session) -> bool:
    """True while none of the four tables holds a live row. One bounded probe per table,
    `LIMIT 1` on the primary key: the question is whether a row exists, not how many."""
    return all(
        session.execute(select(model.id).where(model.deleted_at.is_(None)).limit(1)).first() is None
        for model in _WORK
    )
