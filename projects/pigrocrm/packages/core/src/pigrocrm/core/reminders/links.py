"""Where a reminder opens, for the channels that leave the app (REB-737, REB-738).

The web has its own table, `apps/web/src/features/reminders/links.ts`, typed against the
route tree; a mail or a notification cannot read it, so this is the same table as paths. A
kind with no page of its own (a connection, a job, a closed period) opens the list of
reminders, which is where the person can read it, and never a path nobody checked.
`test_reminder_links.py` holds the two tables to the entities the detectors emit.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import UUID

# The list of reminders: where a reminder with no record of its own is read.
AVVISI_PATH = "/app/avvisi"
# The person's own settings, where every channel is switched.
NOTIFICATIONS_PATH = "/app/settings/notifications"

_PATHS: dict[str, Callable[[UUID], str]] = {
    # An attività has no page of its own: the list opens it in a dialog.
    "attivita": lambda id_: f"/app/activities?apri={id_}",
    "deal": lambda id_: f"/app/deal/{id_}",
    "customer": lambda id_: f"/app/customers/{id_}",
    "invoice": lambda id_: f"/app/invoices/{id_}",
    # A proforma is an invoice row of type `proforma`, opened by the same page.
    "proforma": lambda id_: f"/app/invoices/{id_}",
    "contract": lambda id_: f"/app/contracts/{id_}",
    "fiscal": lambda _: "/app?tab=anno",
    "proposal": lambda id_: f"/app/proposals/{id_}",
    # The timer is the caller's own and lives in the hours page, which has no id to open.
    "timer": lambda _: "/app/hours",
}

# What the detectors emit and the app opens nothing for: shown in the list, never linked.
WITHOUT_PAGE = frozenset({"google_connection", "job", "period"})


def record_path(entity: str, entity_id: UUID) -> str:
    """The path inside the app that opens what a reminder is about, `/app/...`; the list of
    reminders for a kind with no page."""
    build = _PATHS.get(entity)
    return build(entity_id) if build is not None else AVVISI_PATH


def known_entities() -> frozenset[str]:
    return frozenset(_PATHS) | WITHOUT_PAGE


def url_prefix(public_url: str) -> str:
    """The path a space is served under (`/acme`, or empty for the root installation), so a
    link stays on the origin the person is already on."""
    return urlsplit(public_url).path.rstrip("/")


def absolute(public_url: str, path: str) -> str:
    """`path` under the space's public URL, for a mail, which has no origin of its own."""
    return f"{public_url.rstrip('/')}{path}"
