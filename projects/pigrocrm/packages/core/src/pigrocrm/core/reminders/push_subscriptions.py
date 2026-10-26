"""The browsers a person receives reminders on (spec 2026-10-26 §6.1, REB-738).

A **subscription** is what a browser hands the page when the person allows notifications:
an endpoint at the browser vendor's push service and the two keys that encrypt a message
for that browser alone. One person has one per browser and device, kept in the space's own
database like every other piece of a person's bookkeeping; the same endpoint is never
stored twice.

**The endpoint is a URL this server will call**, and the person who registers it chooses
it, so it is checked twice: `accepts_endpoint` refuses what no push service has (plain
HTTP, a bare or numeric host, a name that is not on the public internet) when it is
stored, and the sender checks again what the name resolves to when it sends
(`reminders/push.py`), because a name can be pointed somewhere else after it was accepted.

**The endpoint belongs to a browser, not to a person**: when somebody signs in on a
browser another person subscribed from, subscribing again moves the row to them, so the
first person's reminders never reach a screen the second is looking at. Unsubscribing only
ever removes the caller's own row.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.errors import Conflict, ValidationFailed

ENTITY = "push_subscription"
ENDPOINT_MAX_LENGTH = 2048
USER_AGENT_MAX_LENGTH = 300

# A P-256 public key in uncompressed form is 65 bytes, 0x04 first; the authentication
# secret is 16 bytes (RFC 8291 §3.2). Both travel as unpadded base64url.
_P256DH_BYTES = 65
_AUTH_BYTES = 16

# Names that are on no public network, whatever they resolve to on this host.
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class PushSubscription(Base, PrimaryKeyMixin, TimestampMixin):
    """One browser's subscription, owned by the person who last subscribed from it."""

    __tablename__ = "push_subscription"
    __table_args__ = (
        CheckConstraint(
            f"char_length(endpoint) <= {ENDPOINT_MAX_LENGTH}", name="ck_push_subscription_endpoint"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(128), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)
    # What the browser called itself when it subscribed: a person recognises "Chrome su
    # Android", never an endpoint.
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH), default=None)


def _decoded(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("deve essere base64url") from exc


def accepts_endpoint(endpoint: str) -> bool:
    """Whether a push service could plausibly have issued this endpoint: HTTPS, a real
    public-looking DNS name (no address literal, no single label, no private suffix), no
    credentials. The sender resolves the name again before every send."""
    if len(endpoint) > ENDPOINT_MAX_LENGTH:
        return False
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    return "." in host and not host.endswith(_PRIVATE_SUFFIXES)


class PushSubscriptionWrite(BaseModel):
    """`PushSubscription.toJSON()` as the browser makes it: the endpoint and the two keys."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(description="L'indirizzo del servizio push del browser")
    p256dh: str = Field(description="La chiave pubblica del browser, base64url")
    auth: str = Field(description="Il segreto di autenticazione del browser, base64url")

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        if not accepts_endpoint(value):
            raise ValueError("non è l'indirizzo di un servizio push")
        return value

    @field_validator("p256dh")
    @classmethod
    def _p256dh(cls, value: str) -> str:
        raw = _decoded(value)
        if len(raw) != _P256DH_BYTES or raw[0] != 4:
            raise ValueError("non è una chiave pubblica P-256")
        return value

    @field_validator("auth")
    @classmethod
    def _auth(cls, value: str) -> str:
        if len(_decoded(value)) != _AUTH_BYTES:
            raise ValueError("non è un segreto di autenticazione")
        return value


class PushConfigRead(BaseModel):
    """What the app needs to offer push: whether this installation can send it at all and
    the key a browser subscribes with (`applicationServerKey`). No key, no push: the page
    says so and the reminders go by the other channels."""

    enabled: bool = Field(description="Questa installazione può mandare notifiche push")
    public_key: str | None = Field(description="La chiave con cui il browser si iscrive")


class PushSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint: str
    user_agent: str | None
    created_at: datetime


class PushSubscriptionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _user(actor: Actor) -> UUID:
        if actor.id is None:
            # A job has no browser: a subscription is what a person does from theirs.
            raise Conflict(ENTITY, "solo una persona può attivare le notifiche")
        return actor.id

    def mine(self, actor: Actor) -> list[PushSubscriptionRead]:
        user_id = self._user(actor)
        rows = self.session.scalars(
            select(PushSubscription)
            .where(PushSubscription.user_id == user_id)
            .order_by(PushSubscription.created_at, PushSubscription.id)
        )
        return [PushSubscriptionRead.model_validate(row) for row in rows]

    def subscribe(
        self, actor: Actor, data: PushSubscriptionWrite, user_agent: str | None = None
    ) -> PushSubscriptionRead:
        """Keeps this browser's subscription for the caller: new, or the same endpoint
        again with fresh keys, or the endpoint of a browser somebody else subscribed from,
        which is theirs no longer."""
        user_id = self._user(actor)
        agent = user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None
        statement = insert(PushSubscription).values(
            user_id=user_id,
            endpoint=data.endpoint,
            p256dh=data.p256dh,
            auth=data.auth,
            user_agent=agent,
        )
        row = self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[PushSubscription.endpoint],
                set_={
                    "user_id": statement.excluded.user_id,
                    "p256dh": statement.excluded.p256dh,
                    "auth": statement.excluded.auth,
                    "user_agent": statement.excluded.user_agent,
                    "updated_at": func.now(),
                },
            ).returning(PushSubscription)
        ).scalar_one()
        self.session.commit()
        return PushSubscriptionRead.model_validate(row)

    def unsubscribe(self, actor: Actor, endpoint: str) -> None:
        """Removes the caller's own subscription for this endpoint. Nothing to remove is
        not an error: unsubscribing twice, or from a browser that never subscribed here,
        leaves the same state."""
        user_id = self._user(actor)
        if len(endpoint) > ENDPOINT_MAX_LENGTH:
            raise ValidationFailed(ENTITY, "endpoint", "indirizzo troppo lungo")
        row = self.session.scalars(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
            )
        ).one_or_none()
        if row is not None:
            self.session.delete(row)
            self.session.commit()
