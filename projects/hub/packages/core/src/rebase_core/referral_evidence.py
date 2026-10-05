"""The evidence behind a referral (REB-657), derived in one place.

A referral is taken from a public `rif` code and the email it is posted with, and the
first writer wins an address's attribution (design record 2026-10-05, REB-646). An
admin deciding whether an attribution is wrong or collusive needs the facts around it,
not only who referred whom: the code used, when the referred person signed up, whether
the two email addresses share a company domain, whether the referred person ever
logged in, and where the signup came from.

Everything here is read from rows that already exist -- nothing is stored, nothing is
a migration. The ledger (`ReferralService.list_rewards`) reads it for a page of
referrals with `referral_evidence`; whatever later needs the same facts (counting a
referral only once identity is verified, REB-658) imports `domains_match` and
`referral_evidence` from here instead of deriving them a second time.
"""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.models import Company, Freelancer, Login, Referral, User
from rebase_core.referral_schemas import ReferralEvidence

# Mail providers whose shared domain says nothing about who works where: two people on
# `gmail.com` are not colleagues. A match on one of these is never flagged.
PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "outlook.it",
        "hotmail.com",
        "hotmail.it",
        "live.com",
        "live.it",
        "msn.com",
        "yahoo.com",
        "yahoo.it",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.com",
        "gmx.net",
        "gmx.de",
        "mail.com",
        "libero.it",
        "virgilio.it",
        "tiscali.it",
        "alice.it",
        "tin.it",
        "email.it",
        "fastwebnet.it",
        "inwind.it",
        "iol.it",
        "yandex.com",
        "zoho.com",
        "gmx.it",
        "outlook.fr",
        "yahoo.fr",
        "yahoo.co.uk",
        "hotmail.fr",
        "hotmail.co.uk",
        "live.co.uk",
        "pec.it",
        "legalmail.it",
        "fastweb.it",
        "virgilio.com",
        "libero.com",
        "tele2.it",
        "vodafone.it",
        "kataweb.it",
        "aruba.it",
        "proton.ch",
        "protonmail.ch",
    }
)


def email_domain(email: str) -> str:
    """The part after the last `@`, lower-cased and trimmed; empty when there is none."""
    _, at, domain = email.strip().lower().rpartition("@")
    return domain if at else ""


def domains_match(referrer_email: str, referred_email: str) -> bool:
    """Whether both addresses sit on the same domain that belongs to an organisation:
    a shared public provider (`gmail.com`, `libero.it`, ...) is not a match. The domains
    are compared whole: `studio.it` and `mail.studio.it` are not the same."""
    domain = email_domain(referrer_email)
    return (
        bool(domain)
        and domain == email_domain(referred_email)
        and (domain not in PUBLIC_MAIL_DOMAINS)
    )


class _Referred:
    """What the referred side's own row says: who to look up, when it was made and where
    from."""

    __slots__ = ("created_at", "user_id", "utm_source")

    def __init__(self, user_id: UUID, created_at: datetime, utm_source: str | None) -> None:
        self.user_id = user_id
        self.created_at = created_at
        self.utm_source = utm_source


def referral_evidence(
    session: Session, referrals: Iterable[tuple[Referral, str]]
) -> dict[UUID, ReferralEvidence]:
    """The evidence of each referral, keyed by `Referral.id`. Each item is the referral
    and the referrer's email (a caller listing referrals already has it). Batched: one
    query per referred kind, one for the referred people's emails and one for their
    logins, however many referrals there are."""
    pairs = list(referrals)
    referred: dict[tuple[str, UUID], _Referred] = {}
    for kind, model in (("freelancer", Freelancer), ("company", Company)):
        ids = {r.entity_id for r, _ in pairs if r.kind == kind}
        if not ids:
            continue
        for row in session.execute(
            select(model.id, model.user_id, model.created_at, model.utm_source).where(
                model.id.in_(ids)
            )
        ):
            referred[kind, row.id] = _Referred(row.user_id, row.created_at, row.utm_source)
    user_ids = {entry.user_id for entry in referred.values()}
    emails: dict[UUID, str] = {}
    logged_in: set[UUID] = set()
    if user_ids:
        emails = {
            user_id: email
            for user_id, email in session.execute(
                select(User.id, User.email).where(User.id.in_(user_ids))
            )
        }
        logged_in = set(
            session.scalars(select(Login.user_id).where(Login.user_id.in_(user_ids)).distinct())
        )
    result: dict[UUID, ReferralEvidence] = {}
    for referral, referrer_email in pairs:
        entry = referred.get((referral.kind, referral.entity_id))
        # A referred row hard-deleted from under its referral leaves only the referral's
        # own moment to show; the person's email and logins cannot be looked up, so both
        # flags are unknown (`None`), never a reassuring `False`.
        if entry is None:
            result[referral.id] = ReferralEvidence(
                code=referral.code,
                signed_up_at=referral.created_at,
                utm_source=None,
                same_email_domain=None,
                ever_logged_in=None,
            )
            continue
        result[referral.id] = ReferralEvidence(
            code=referral.code,
            signed_up_at=entry.created_at,
            utm_source=entry.utm_source,
            same_email_domain=domains_match(referrer_email, emails.get(entry.user_id, "")),
            ever_logged_in=entry.user_id in logged_in,
        )
    return result
