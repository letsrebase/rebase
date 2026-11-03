"""REB-392, spec § 4: the member area's «Contratti», a freelancer's own documents and
nobody else's. The admin side is `test_signing.py`, whose helpers this file borrows."""

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import FRAMEWORK_VERSION, TABLES, _body, _second_card, _setup
from signing_fixtures import (
    SIGNED_AT,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _matches,
    _sign,
    _signing,
    _webhook,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import MemberContracts
from rebase_core.errors import NotFound
from rebase_core.mail import RecordingSender
from rebase_core.member_contracts import MemberContractService
from rebase_core.models import Freelancer, Match

# The day after the signature: the next renewal is a year on.
AFTER = date(2026, 10, 2)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _user_of(session: Session, freelancer_id: UUID) -> UUID:
    freelancer = session.get(Freelancer, freelancer_id)
    assert freelancer is not None
    return freelancer.user_id


def _service(session: Session) -> MemberContractService:
    return MemberContractService(session, today=lambda: AFTER)


def test_the_freelancer_sees_what_reached_them_and_the_link_to_sign(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    framework = _framework_of(clean, freelancer_id)
    assert mine.framework is not None
    assert (mine.framework.id, mine.framework.status) == (framework.id, "sent")
    assert mine.framework.signing_url == framework.signing_url
    [letter] = mine.letters
    assert (letter.number, letter.status, letter.customer) == (
        match.letter.number,
        "pending",
        "ACME S.r.l.",
    )
    assert letter.start == "1° ottobre 2026"
    assert letter.signing_url is None
    assert mine.previous_frameworks == []


def test_a_draft_match_shows_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _draft(clean, FakeRenderer(draft=False), freelancer_id, company_id, admin_id)
    assert _service(clean).for_user(_user_of(clean, freelancer_id)) == MemberContracts(
        framework=None, letters=[]
    )


def test_member_letter_carries_the_pigro_url(clean: Session) -> None:
    """spec § 3.6: «Le tue ore su Pigro» has somewhere to point once, and only once, the
    match is `linked`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _draft(clean, FakeRenderer(draft=False), freelancer_id, company_id, admin_id)
    letter = _letter_of(clean, match.id)
    letter.sent_at = SIGNED_AT
    row = clean.get(Match, match.id)
    assert row is not None
    row.pigro_status = "to_link"
    clean.commit()
    user_id = _user_of(clean, freelancer_id)

    [not_yet] = _service(clean).for_user(user_id).letters
    assert not_yet.pigro_url is None

    row.pigro_status = "linked"
    row.pigro_url = "https://pigro.letsrebase.com/ada-lovelace/app/deal/123"
    clean.commit()

    [linked] = _service(clean).for_user(user_id).letters
    assert linked.pigro_url == "https://pigro.letsrebase.com/ada-lovelace/app/deal/123"


def test_a_signed_framework_shows_its_dates_its_copy_and_no_link(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.recovery.finish(signed)
    user_id = _user_of(clean, freelancer_id)

    mine = _service(clean).for_user(user_id)

    assert mine.framework is not None
    assert (mine.framework.status, mine.framework.active, mine.framework.signing_url) == (
        "signed",
        True,
        None,
    )
    assert mine.framework.has_pdf_signed is True
    assert (mine.framework.renewal, mine.framework.last_notice_day) == (
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    copy = _service(clean).signed_pdf(user_id, signed)
    assert copy.content == fake.signed_pdf(envelope)
    assert copy.filename == f"framework-agreement-v{FRAMEWORK_VERSION}-signed.pdf"
    [letter] = mine.letters
    assert letter.status == "sent" and letter.signing_url is not None


def test_a_cancelled_document_says_so_and_offers_no_link(clean: Session) -> None:
    """Probe § 11.10: Documenso's page still opens a cancelled document and fails only at
    the click. The member area is where the person reads that it is not to be signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "Il domicilio è sbagliato")
    signing.apply(_webhook(fake, envelope, "DOCUMENT_REJECTED"))

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.framework is not None
    assert (mine.framework.status, mine.framework.signing_url) == ("cancelled", None)


def test_a_notice_moves_the_signed_framework_under_the_new_one_instead_of_hiding_it(
    clean: Session,
) -> None:
    """REB-392: a notice on the first framework agreement, then a second match writes
    and signs a new one. The person still sees the first, signed copy and all -- moved
    under the current one, not gone."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    first_match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(first_match.id, admin_id)
    first_envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(first_envelope, SIGNED_AT)
    first_signed = signing.apply(_webhook(fake, first_envelope, "DOCUMENT_COMPLETED"))
    assert first_signed is not None
    signing.recovery.finish(first_signed)
    first_framework = _framework_of(clean, freelancer_id)
    signing.admin.record_notice(first_framework.id, admin_id)

    second_match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(second_match.id, admin_id)
    second_envelope = _envelope_of(_framework_of(clean, freelancer_id))
    second_signed_at = SIGNED_AT.replace(month=11)
    fake.sign(second_envelope, second_signed_at)
    second_signed = signing.apply(_webhook(fake, second_envelope, "DOCUMENT_COMPLETED"))
    assert second_signed is not None
    signing.recovery.finish(second_signed)
    second_framework = _framework_of(clean, freelancer_id)
    assert second_framework.id != first_framework.id

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.framework is not None and mine.framework.id == second_framework.id
    [previous] = mine.previous_frameworks
    assert previous.id == first_framework.id
    assert previous.status == "notice_served"
    assert previous.has_pdf_signed is True
    copy = _service(clean).signed_pdf(_user_of(clean, freelancer_id), first_framework.id)
    assert copy.content == fake.signed_pdf(first_envelope)


def test_someone_elses_document_and_an_unsigned_copy_are_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    grace_id = _second_card(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    grace, ada = _user_of(clean, grace_id), _user_of(clean, freelancer_id)

    with pytest.raises(NotFound):
        _service(clean).signed_pdf(grace, framework.id)
    with pytest.raises(NotFound):
        _service(clean).signed_pdf(ada, framework.id)
    assert _service(clean).for_user(grace) == MemberContracts(framework=None, letters=[])


def test_a_person_without_a_card_has_no_contracts(clean: Session) -> None:
    admin_id, _freelancer_id, _company_id = _setup(clean)
    with pytest.raises(NotFound, match="card"):
        _service(clean).for_user(admin_id)


def test_an_occasional_framework_shows_beside_the_ordinary_one_with_its_own_link(
    clean: Session,
) -> None:
    """REB-687: an active ordinary framework agreement and an occasional one out for
    signature both show on top, the occasional one with its signing link, and neither is
    filed under «precedenti»; nor is either once both are signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    signing = _signing(clean, renderer, fake, RecordingSender())
    ordinary = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(ordinary.id, admin_id)
    _sign(signing, fake, _framework_of(clean, freelancer_id))
    occasional = _matches(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasional"), admin_id
    )
    signing.send_match(occasional.id, admin_id)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.framework is not None and mine.occasional_framework is not None
    assert (mine.framework.regime, mine.framework.label, mine.framework.active) == (
        "vat_registered",
        "Contratto quadro",
        True,
    )
    assert (mine.occasional_framework.regime, mine.occasional_framework.status) == (
        "occasional",
        "sent",
    )
    assert mine.occasional_framework.label == "Contratto quadro per prestazioni occasionali"
    assert mine.occasional_framework.signing_url is not None
    assert mine.previous_frameworks == []
    assert sorted((letter.label, letter.status) for letter in mine.letters) == [
        ("Lettera di incarico", "sent"),
        ("Lettera di incarico occasionale", "pending"),
    ]

    _sign(signing, fake, _framework_of(clean, freelancer_id))

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))
    assert mine.framework is not None and mine.occasional_framework is not None
    assert (mine.framework.active, mine.occasional_framework.active) == (True, True)
    assert mine.previous_frameworks == []
