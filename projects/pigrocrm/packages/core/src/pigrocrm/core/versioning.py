"""The one version check every whole-row settings save makes (REB-622, spec 2026-10-03
§11).

A settings `PUT` replaces a row, so two admins editing it at once would have the second
Salva overwrite the first's fields in silence. The read schemas already carry
`updated_at`; the upsert bodies take it back as «the row I built this draft on», and the
service refuses a mismatch with `StaleRow` before it writes anything. The decision is
taken once, here, and the three services (`LegalEntityService.update`,
`FiscalProfileService.upsert`, `SpaceSettingsService.update`) call it rather than each
comparing timestamps its own way. A body that leaves the key out keeps the old
behaviour: the MCP tools read right before they write, and an older client sends none.
"""

from datetime import UTC, datetime

from pigrocrm.core.errors import StaleRow


def _instant(value: datetime | None) -> datetime | None:
    """Compared as instants: the row's value is timezone-aware, the client's is parsed
    from the ISO string the read sent and may come back naive from a careless caller,
    and comparing a naive to an aware datetime raises instead of refusing."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def require_unchanged(entity: str, *, sent: datetime | None, current: datetime | None) -> None:
    """Refuse the save when the version the caller built its draft on is not the row's.

    Equality, not ordering: a version older *or* newer than the row's means the draft
    and the row disagree, and the only honest answer is to reload. The caller checks
    whenever the key was *sent* (`"updated_at" in data.model_fields_set`), `null`
    included: `null` is the version of a profile not saved yet and of space settings
    with no override row, so a draft built on that state is refused once a row exists,
    and only an absent key means no check. The caller also takes the row's lock before
    comparing (`refresh(with_for_update=True)`, or an advisory lock where there is no
    row), so two saves carrying the same version cannot both pass under READ COMMITTED.
    """
    if _instant(sent) != _instant(current):
        raise StaleRow(entity, updated_at=current)


__all__ = ["require_unchanged"]
