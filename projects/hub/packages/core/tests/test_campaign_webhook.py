"""Resend's webhook (spec § 6.1), core side."""

import base64
import hashlib
import hmac
import time

from campaign_fixtures import campaign_row, clean  # noqa: F401
from sqlalchemy.orm import Session

from rebase_core.campaigns.webhook import apply_event, verify_signature
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient

SECRET = "whsec_" + base64.b64encode(b"k" * 24).decode()


def signed(body: bytes, *, svix_id: str = "msg_1", at: int | None = None) -> tuple[str, str, str]:
    stamp = str(at if at is not None else int(time.time()))
    mac = hmac.new(
        base64.b64decode(SECRET.removeprefix("whsec_")),
        f"{svix_id}.{stamp}.".encode() + body,
        hashlib.sha256,
    )
    return svix_id, stamp, "v1,bogus v1," + base64.b64encode(mac.digest()).decode()


def test_a_good_signature_passes_and_a_changed_body_or_a_stale_stamp_does_not() -> None:
    body = b'{"type":"email.delivered"}'
    sid, stamp, sig = signed(body)
    now = time.time()
    assert verify_signature(SECRET, sid, stamp, sig, body, now)
    assert not verify_signature(SECRET, sid, stamp, sig, body + b" ", now)
    old_id, old_stamp, old_sig = signed(body, at=int(now) - 600)
    assert not verify_signature(SECRET, old_id, old_stamp, old_sig, body, now)
    assert not verify_signature("", sid, stamp, sig, body, now)


def row(session: Session, **fields: object) -> CampaignRecipient:
    campaign = campaign_row(session)
    recipient = CampaignRecipient(
        campaign_id=campaign.id,
        email="ada@studio.it",
        tipo="freelancer",
        codice="1",
        prima={},
        disiscrizione_token="tok",
        **fields,
    )
    session.add(recipient)
    session.commit()
    return recipient


def event(kind: str, recipient: CampaignRecipient | None, **data: object) -> dict[str, object]:
    tags = {"campaign": "c-prova-0", "kind": "real"}
    if recipient is not None:
        tags["r"] = str(recipient.id)
    return {
        "type": kind,
        "created_at": "2026-09-25T07:31:00.000Z",
        "data": {"email_id": "re_1", "tags": tags, **data},
    }


def test_delivery_is_found_by_the_tag_before_the_id_is_committed_and_written_once(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    target = row(clean)
    assert apply_event(clean, event("email.delivered", target)) == "applicato"
    first = clean.get(CampaignRecipient, target.id).consegnata_at  # type: ignore[union-attr]
    later = event("email.delivered", target)
    later["created_at"] = "2026-09-25T09:00:00.000Z"
    apply_event(clean, later)
    assert clean.get(CampaignRecipient, target.id).consegnata_at == first  # type: ignore[union-attr]


def test_only_a_permanent_bounce_marks_the_address(clean: Session) -> None:  # noqa: F811  (fixture)
    target = row(clean)
    assert (
        apply_event(clean, event("email.bounced", target, bounce={"type": "Transient"}))
        == "ignorato"
    )
    assert (
        apply_event(clean, event("email.bounced", target, bounce={"type": "Permanent"}))
        == "applicato"
    )
    assert clean.get(CampaignRecipient, target.id).rimbalzata_at is not None  # type: ignore[union-attr]


def test_a_complaint_opts_the_address_out(clean: Session) -> None:  # noqa: F811  (fixture)
    target = row(clean)
    apply_event(clean, event("email.complained", target))
    assert clean.get(CampaignOptout, "ada@studio.it").fonte == "reclamo"  # type: ignore[union-attr]


def test_a_click_keeps_the_first_moment(clean: Session) -> None:  # noqa: F811  (fixture)
    target = row(clean)
    apply_event(
        clean, event("email.clicked", target, click={"timestamp": "2026-09-25T08:00:00.000Z"})
    )
    apply_event(
        clean, event("email.clicked", target, click={"timestamp": "2026-09-25T07:40:00.000Z"})
    )
    moment = clean.get(CampaignRecipient, target.id).primo_clic_at  # type: ignore[union-attr]
    assert moment is not None and moment.isoformat() == "2026-09-25T07:40:00+00:00"


def test_tests_strangers_and_early_events(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 5."""
    test_event = event("email.delivered", None)
    test_event["data"]["tags"] = {"campaign": "c-1", "kind": "test"}  # type: ignore[index]
    assert apply_event(clean, test_event) == "ignorato"
    stranger = {
        "type": "email.delivered",
        "created_at": "2026-09-25T07:31:00Z",
        "data": {"email_id": "re_x", "tags": {}},
    }
    assert apply_event(clean, stranger) == "ignorato"
    early = event("email.delivered", None)
    early["data"]["tags"]["r"] = "00000000-0000-0000-0000-000000000000"  # type: ignore[index]
    assert apply_event(clean, early) == "da_riprovare"


def test_a_delivery_for_a_cancelled_campaign_still_counts(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    target = row(clean, stato="inviata")
    clean.get(Campaign, target.campaign_id).stato = "annullata"  # type: ignore[union-attr]
    clean.commit()
    assert apply_event(clean, event("email.delivered", target)) == "applicato"
