"""Resend's webhook, core side (spec § 6.1): a Svix signature, then the first delivery,
the first click, a hard bounce or a complaint on the recipient row. The row is found by
the `r` tag the mail left with, so an event that beats the tick's commit still lands;
`resend_id` is the fallback. Every write keeps the first moment, so a repeated event
changes nothing."""

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.campaigns.optouts import OptoutService
from rebase_core.models import CampaignRecipient

TOLERANCE_SECONDS = 300
HANDLED = ("email.delivered", "email.bounced", "email.clicked", "email.complained")
Outcome = Literal["applicato", "ignorato", "da_riprovare"]


def verify_signature(
    secret: str, svix_id: str, svix_timestamp: str, svix_signature: str, body: bytes, now: float
) -> bool:
    if not (secret.startswith("whsec_") and svix_id and svix_timestamp and svix_signature):
        return False
    try:
        stamp = int(svix_timestamp)
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, binascii.Error):
        return False
    if abs(now - stamp) > TOLERANCE_SECONDS:
        return False
    expected = base64.b64encode(
        hmac.new(key, f"{svix_id}.{svix_timestamp}.".encode() + body, hashlib.sha256).digest()
    )
    for entry in svix_signature.split(" "):
        version, _, signature = entry.partition(",")
        if version == "v1" and hmac.compare_digest(signature.encode(), expected):
            return True
    return False


def _moment(raw: object) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _find(session: Session, tag: object, email_id: object) -> CampaignRecipient | None:
    if isinstance(tag, str):
        try:
            found = session.get(CampaignRecipient, UUID(tag))
        except ValueError:
            found = None
        if found is not None:
            return found
    if isinstance(email_id, str) and email_id:
        return session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.resend_id == email_id)
        )
    return None


def _as_mapping(raw: object) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def apply_event(session: Session, event: Mapping[str, Any]) -> Outcome:
    kind = event.get("type")
    data = _as_mapping(event.get("data"))
    tags = _as_mapping(data.get("tags"))
    if tags.get("kind") == "test" or kind not in HANDLED:
        return "ignorato"
    row = _find(session, tags.get("r"), data.get("email_id"))
    if row is None:
        return "da_riprovare" if tags.get("campaign") else "ignorato"
    at = _moment(event.get("created_at"))
    if kind == "email.delivered":
        row.consegnata_at = row.consegnata_at or at
    elif kind == "email.bounced":
        bounce = _as_mapping(data.get("bounce"))
        if bounce.get("type") != "Permanent":
            return "ignorato"
        row.rimbalzata_at = row.rimbalzata_at or at
    elif kind == "email.clicked":
        click = _as_mapping(data.get("click"))
        clicked = _moment(click.get("timestamp"))
        row.primo_clic_at = min(row.primo_clic_at, clicked) if row.primo_clic_at else clicked
    else:
        row.reclamo_at = row.reclamo_at or at
    email_id = data.get("email_id")
    if row.resend_id is None and isinstance(email_id, str):
        row.resend_id = email_id
    session.commit()
    if kind == "email.complained":
        OptoutService(session).record(row.email, "reclamo", row.campaign_id)
    return "applicato"
