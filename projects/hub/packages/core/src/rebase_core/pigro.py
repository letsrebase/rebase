"""PigroCRM's spaces, as the hub's admin area shows them: which exist, and whose they are.

The hub imports nothing from PigroCRM and never opens its database (DECISIONS.md,
2026-09-09). What it does is ask the CRM's own API, `GET /api/tenants/`, with the token
the CRM reads as `PIGROCRM_REGISTRY_TOKEN`, through the same HTTP seam the mail and the
pixel use. The registry knows who opened a space and when, not what is inside it, and
that is all this module claims to know too.

The one thing the hub adds is the owner: when the address that opened a space is a
`freelancers` row, the space names that member and points at their card (ORB-142).
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.http import MAX_BODY_BYTES, HttpCall
from rebase_core.models import Freelancer, User

REGISTRY_PATH = "/api/tenants/"
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
_CURSOR_ENTITY = "cursor"
_CURSOR_INVALID = "cursore non valido"
# What an admin reads when the CRM does not answer as it should: shared with the
# engagements client (`rebase_core.engagements`, REB-498), so both say the same words.
# `ANSWERED_STATUS` is a format, filled with the status the CRM answered.
NOT_ANSWERING = "Pigro non risponde."
ANSWERED_STATUS = "Pigro non ha risposto ({status})."
TOO_LONG = "Pigro ha risposto qualcosa di troppo lungo."
NOT_THE_SHAPE = "Pigro ha risposto qualcosa che non è un elenco."


class PigroUnavailable(Exception):
    """The CRM did not answer with a list: a status other than 200, or a body that is not
    the registry. The message is the sentence the admin area shows."""


class RegistryRow(BaseModel):
    """One row as PigroCRM's `TenantRead` writes it. `id` is accepted and dropped: the
    hub has no use for the CRM's key."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    owner_email: str
    created_at: datetime


class PigroMember(BaseModel):
    """The hub member who owns a space, enough to name them and link their card."""

    id: UUID
    nome: str
    cognome: str


class PigroSpace(BaseModel):
    slug: str
    owner_email: str
    created_at: datetime
    # Where the space answers, for the link on the row.
    url: str
    # `None` when nobody with that address filled in the hub's wizard.
    membro: PigroMember | None


class PigroSpaceList(BaseModel):
    totale: int
    items: list[PigroSpace]
    next_cursor: str | None = None


def _encode_pigro_cursor(row: RegistryRow) -> str:
    """The registry has no id of its own to pair with `created_at` the way every
    SQL-backed list here does (`rebase_core.pagination`): a space's `slug` is unique
    and stable, so it plays that role, in a matching but separate opaque envelope --
    `pagination.encode_cursor` is typed to a `UUID` row id, which a slug is not."""
    payload = {"v": row.created_at.isoformat(), "s": row.slug}
    body = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _decode_pigro_cursor(raw: str) -> tuple[datetime, str]:
    if not raw or len(raw) > 512:
        raise ValidationFailed(_CURSOR_ENTITY, "cursor", _CURSOR_INVALID)
    padded = raw + "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["v"]), str(payload["s"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise ValidationFailed(_CURSOR_ENTITY, "cursor", _CURSOR_INVALID) from exc


_ROWS = TypeAdapter(list[RegistryRow])


class PigroRegistry:
    """Reads the registry over HTTP and matches each owner to a member. The session is
    the hub's own; the CRM is only ever reached through `http`."""

    def __init__(self, settings: Settings, http: HttpCall) -> None:
        self.settings = settings
        self.http = http

    def _base_url(self) -> str:
        return self.settings.pigro_api_url.rstrip("/")

    def _fetch_rows(self) -> list[RegistryRow]:
        """The registry's raw rows, exactly as `GET /api/tenants/` answers them: what
        `list_spaces` and `find_by_email` (REB-284) both read before matching it to the
        hub's own members, so the request and its error handling are typed once."""
        headers = {
            "Authorization": f"Bearer {self.settings.pigro_registry_token}",
            "Accept": "application/json",
        }
        try:
            status, body = self.http("GET", self._base_url() + REGISTRY_PATH, headers, b"")
        except Exception as exc:  # noqa: BLE001 - a refused connection, a DNS miss, a timeout
            raise PigroUnavailable(NOT_ANSWERING) from exc
        if status != 200:
            raise PigroUnavailable(ANSWERED_STATUS.format(status=status))
        if len(body) > MAX_BODY_BYTES:
            raise PigroUnavailable(TOO_LONG)
        try:
            return _ROWS.validate_python(json.loads(body))
        except (ValueError, ValidationError) as exc:
            raise PigroUnavailable(NOT_THE_SHAPE) from exc

    def list_spaces(
        self,
        session: Session,
        *,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = LIST_LIMIT_DEFAULT,
    ) -> PigroSpaceList:
        """Newest first, `q` matched case-insensitively against the slug or the owner's
        address (REB-313): the registry itself takes no query parameters of its own
        (`GET /api/tenants/` answers everything every time), but nothing requires the
        search and the page to happen upstream -- the hub already holds the full list
        in memory for this one request, and filters, sorts and slices it here before
        any member lookup, so the response stays bounded by `limit` as the registry
        grows instead of relaying its whole size back out unbounded."""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        rows = self._fetch_rows()
        if q:
            needle = q.strip().lower()
            rows = [r for r in rows if needle in r.slug.lower() or needle in r.owner_email.lower()]
        rows.sort(key=lambda r: (r.created_at, r.slug), reverse=True)
        totale = len(rows)
        if cursor:
            after_value, after_slug = _decode_pigro_cursor(cursor)
            rows = [r for r in rows if (r.created_at, r.slug) < (after_value, after_slug)]
        page = rows[:limit]
        next_cursor = _encode_pigro_cursor(page[-1]) if len(rows) > limit else None
        members = self._members({row.owner_email.lower() for row in page}, session)
        return PigroSpaceList(
            totale=totale,
            items=[
                PigroSpace(
                    slug=row.slug,
                    owner_email=row.owner_email,
                    created_at=row.created_at,
                    url=f"{self._base_url()}/{row.slug}/app/",
                    membro=members.get(row.owner_email.lower()),
                )
                for row in page
            ],
            next_cursor=next_cursor,
        )

    def find_by_email(self, email: str, session: Session) -> PigroSpace | None:
        """Whether one address owns a space (REB-284's freelancer detail): the same
        registry `list_spaces` reads, matched case-insensitively to one address
        instead of listed whole. `None` for an address with no space, never an
        error -- the CRM not knowing about somebody is not a hub failure."""
        target = email.strip().lower()
        row = next((r for r in self._fetch_rows() if r.owner_email.lower() == target), None)
        if row is None:
            return None
        members = self._members({target}, session)
        return PigroSpace(
            slug=row.slug,
            owner_email=row.owner_email,
            created_at=row.created_at,
            url=f"{self._base_url()}/{row.slug}/app/",
            membro=members.get(target),
        )

    @staticmethod
    def _members(emails: set[str], session: Session) -> dict[str, PigroMember]:
        """The members behind those addresses, by lowercased address, in one query."""
        if not emails:
            return {}
        rows = session.execute(
            select(Freelancer.id, User.email, User.nome, User.cognome)
            .join(User, User.id == Freelancer.user_id)
            .where(func.lower(User.email).in_(emails))
        ).all()
        return {
            email.lower(): PigroMember(id=freelancer_id, nome=nome, cognome=cognome)
            for freelancer_id, email, nome, cognome in rows
        }
