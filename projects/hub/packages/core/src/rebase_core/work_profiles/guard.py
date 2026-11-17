"""The anonymity guards over what a company reads of a work profile (REB-1127, spec § 5).

The cloud shows the summary, the domains' labels and the representative projects' abstract domains.
They are read when the profile is stored, and again when an admin approves, by the hub's own
deterministic rules: the identifier rules of the anonymous CV (`anonymous_cv.guard`: links and
profile hosts, emails, phones, tax identifiers) and the card's (`card_guard.identifying`: the
person's name, GitHub account, link hosts and the clients of their engagements). A hit is the
field and the rule, never the matched text, so a log or a row never repeats an identifier.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rebase_core.anonymous_cv.guard import _PROFILE_HOST, _has_tax_id
from rebase_core.card_draft import phone_digits
from rebase_core.card_guard import (
    _ADDRESS,
    PHONE_KEY_DIGITS,
    KnownIdentifiers,
    _has_phone,
    identifying,
)
from rebase_core.hub_facts import hub_facts
from rebase_core.models import Freelancer, User
from rebase_core.sources import github_login_of, link_hostnames
from rebase_core.team_schemas import Card
from rebase_core.work_profiles.view import _dicts, _text


def cloud_fields(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """`(field, text)` of everything a company may read of the profile."""
    fields: list[tuple[str, str]] = []
    if summary := _text(payload.get("summary")):
        fields.append(("summary", summary))
    for index, domain in enumerate(_dicts(payload.get("domains"))):
        if label := _text(domain.get("label")):
            fields.append((f"domains[{index}].label", label))
    for project in _dicts(payload.get("projects")):
        if about := _text(project.get("domain")):
            fields.append((f"{project.get('id')}.domain", about))
    return fields


def guard_hits(
    payload: dict[str, Any], known: KnownIdentifiers, emails: list[str], phones: list[str]
) -> list[dict[str, str]]:
    """The hits over the cloud fields, each `{"field", "rule"}`, in the profile's order."""
    keys = [digits[-PHONE_KEY_DIGITS:] for p in phones if len(digits := phone_digits(p)) >= 7]
    lowered = [email.strip().lower() for email in emails if email.strip()]
    hits: list[dict[str, str]] = []
    for field, value in cloud_fields(payload):
        rules: list[str] = []
        if _ADDRESS.search(value) or _PROFILE_HOST.search(value):
            rules.append("link_or_email")
        if any(email in value.lower() for email in lowered):
            rules.append("email")
        if keys and _has_phone(value, keys):
            rules.append("phone")
        if _has_tax_id(value, []):
            rules.append("tax_id")
        reading = Card.model_construct(
            role="",
            experience_years=None,
            seniority="mid",
            skills=[],
            industries=[],
            languages=[],
            location=None,
            profile_summary=value,
        )
        if identifying(reading, known):
            rules.append("name_account_site_or_client")
        hits.extend({"field": field, "rule": rule} for rule in rules)
    return hits


def guard_hits_for(
    session: Session, freelancer_id: UUID, payload: dict[str, Any]
) -> list[dict[str, str]]:
    """The hits for this person as the hub knows them now: their name, address, phone, links and
    the clients of their engagements."""
    card = session.get_one(Freelancer, freelancer_id)
    user = session.get_one(User, card.user_id)
    links = list(card.links or [])
    known = KnownIdentifiers(
        last_name=user.last_name,
        first_name=user.first_name,
        github_login=github_login_of(links),
        hostnames=link_hostnames(links),
        employers=hub_facts(session, freelancer_id).clients,
    )
    return guard_hits(payload, known, [user.email], [user.phone or ""])
