"""REB-392, spec § 4; REB-1107: the member area's «Contratti», a freelancer's own documents and
nobody else's, read from the CRM through its door by the hub's freelancer id. The CRM's side is
PigroCRM's `test_freelancer_door_api.py`; this file is the hub's arrangement of what it answers
(the page's two framework places, the previous ones, the letters), the vocabulary mapping, the
signed PDF, and what an unreachable CRM does to the page (an error, never an empty list)."""

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fakes_crm_door import FakeCrmDoor, crm_document
from fakes_pigro import TOKEN
from match_fixtures import TABLES, _admin, _card, _second_card
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import MemberContracts
from rebase_core.errors import NotFound
from rebase_core.member_contracts import MemberContractService
from rebase_core.models import Freelancer
from rebase_core.pigro_door import PigroMisconfigured, PigroUnavailable

FRAMEWORK = UUID("0192e0a0-0000-7000-8000-0000000000f1")
OCCASIONAL = UUID("0192e0a0-0000-7000-8000-0000000000f2")
PREVIOUS = UUID("0192e0a0-0000-7000-8000-0000000000f3")
LETTER = UUID("0192e0a0-0000-7000-8000-0000000000a1")
SIGNED_PDF = b"%PDF-1.4 sealed by the signing site"
SIGNED = {
    "state": "signed",
    "signed_at": "2026-09-30T23:30:00Z",
    "signing_url": None,
    "has_signed_copy": True,
    "filename": "contratto-quadro-v1.0.pdf",
}


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


def _service(session: Session, crm: FakeCrmDoor) -> MemberContractService:
    return MemberContractService(session, crm.door())


def test_the_freelancer_sees_what_reached_them_and_the_link_to_sign(clean: Session) -> None:
    freelancer_id = _card(clean)
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [
                crm_document(
                    id=str(LETTER),
                    kind="engagement_letter",
                    number="2026-001",
                    state="pending",
                    customer="ACME S.r.l.",
                    start="1° ottobre 2026",
                    signing_url=None,
                ),
                crm_document(id=str(FRAMEWORK)),
            ]
        }
    )

    mine = _service(clean, crm).for_user(_user_of(clean, freelancer_id))

    assert mine.framework is not None
    assert (mine.framework.id, mine.framework.status) == (FRAMEWORK, "sent")
    assert mine.framework.signing_url == "https://firma.example.test/sign/abc"
    assert (mine.framework.kind, mine.framework.regime) == ("framework", "vat_registered")
    assert mine.framework.label == "Contratto quadro"
    [letter] = mine.letters
    assert (letter.kind, letter.number, letter.status, letter.customer) == (
        "letter",
        "2026-001",
        "pending",
        "ACME S.r.l.",
    )
    assert (letter.label, letter.start, letter.signing_url) == (
        "Lettera di incarico",
        "1° ottobre 2026",
        None,
    )
    assert mine.previous_frameworks == []
    # The CRM is asked by the card's id, with the bearer, as a GET.
    assert crm.calls == [("GET", f"/api/rebase/freelancers/{freelancer_id}/contracts")]
    assert crm.bearers == [f"Bearer {TOKEN}"]


def test_a_freelancer_the_crm_has_no_documents_for_sees_none(clean: Session) -> None:
    freelancer_id = _card(clean)
    assert _service(clean, FakeCrmDoor()).for_user(
        _user_of(clean, freelancer_id)
    ) == MemberContracts(framework=None, letters=[])


def test_a_link_is_never_shown_for_a_document_that_is_not_waiting(clean: Session) -> None:
    """Whatever the CRM sends, the signer's link is shown only while the document waits."""
    freelancer_id = _card(clean)
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [
                crm_document(
                    id=str(FRAMEWORK), state="cancelled", signing_url="https://firma.example.test/x"
                )
            ]
        }
    )

    mine = _service(clean, crm).for_user(_user_of(clean, freelancer_id))

    assert mine.framework is not None
    assert (mine.framework.status, mine.framework.signing_url) == ("cancelled", None)


def test_member_letter_carries_the_pigro_url(clean: Session) -> None:
    """spec § 3.6: «Le tue ore su Pigro» points at the engagement's deal in the freelancer's
    own space, once the CRM wrote it."""
    freelancer_id = _card(clean)
    url = "https://pigro.letsrebase.com/ada-lovelace/app/deal/123"
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [crm_document(id=str(LETTER), kind="engagement_letter", pigro_url=url)]
        }
    )

    [letter] = _service(clean, crm).for_user(_user_of(clean, freelancer_id)).letters

    assert letter.pigro_url == url


def test_a_signed_framework_shows_its_dates_its_copy_and_no_link(clean: Session) -> None:
    freelancer_id = _card(clean)
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [
                crm_document(
                    id=str(FRAMEWORK),
                    active=True,
                    renewal="2027-10-01",
                    last_notice_day="2027-09-01",
                    **SIGNED,
                )
            ]
        },
        pdfs={FRAMEWORK: SIGNED_PDF},
    )
    user_id = _user_of(clean, freelancer_id)

    mine = _service(clean, crm).for_user(user_id)

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
    copy = _service(clean, crm).signed_pdf(user_id, FRAMEWORK)
    assert copy.content == SIGNED_PDF
    assert copy.filename == "contratto-quadro-v1.0.pdf"
    # The list was asked first (so a document that is not theirs is refused here too), then
    # the copy, both by the card's id.
    assert [
        call[1].removeprefix(f"/api/rebase/freelancers/{freelancer_id}/") for call in crm.calls
    ][-2:] == ["contracts", f"contracts/{FRAMEWORK}/pdf"]


def test_a_notice_moves_the_signed_framework_under_the_new_one_instead_of_hiding_it(
    clean: Session,
) -> None:
    """REB-392: a notice on the first framework agreement, then a new one signed. The person
    still sees the first, signed copy and all, moved under the current one, not gone."""
    freelancer_id = _card(clean)
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [
                # Newest first, as the CRM sends them.
                crm_document(id=str(FRAMEWORK), active=True, renewal="2027-11-03", **SIGNED),
                crm_document(
                    id=str(PREVIOUS), **{**SIGNED, "state": "notice_served"}, active=False
                ),
            ]
        },
        pdfs={PREVIOUS: SIGNED_PDF},
    )
    user_id = _user_of(clean, freelancer_id)

    mine = _service(clean, crm).for_user(user_id)

    assert mine.framework is not None and mine.framework.id == FRAMEWORK
    [previous] = mine.previous_frameworks
    assert (previous.id, previous.status, previous.has_pdf_signed) == (
        PREVIOUS,
        "notice_served",
        True,
    )
    assert _service(clean, crm).signed_pdf(user_id, PREVIOUS).content == SIGNED_PDF


def test_an_occasional_framework_shows_beside_the_ordinary_one_with_its_own_link(
    clean: Session,
) -> None:
    """REB-687: an active ordinary framework agreement and an occasional one out for
    signature both show on top, the occasional one with its signing link, and neither is
    filed under «precedenti»."""
    freelancer_id = _card(clean)
    crm = FakeCrmDoor(
        documents={
            freelancer_id: [
                crm_document(
                    id=str(LETTER),
                    kind="engagement_letter",
                    letter_type="occasional",
                    state="pending",
                    signing_url=None,
                    number="2026-002",
                ),
                crm_document(id=str(OCCASIONAL), letter_type="occasional"),
                crm_document(id=str(FRAMEWORK), active=True, **SIGNED),
            ]
        }
    )

    mine = _service(clean, crm).for_user(_user_of(clean, freelancer_id))

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
    assert [(letter.label, letter.status) for letter in mine.letters] == [
        ("Lettera di incarico occasionale", "pending")
    ]


def test_someone_elses_document_and_an_unsigned_copy_are_not_found(clean: Session) -> None:
    ada_id = _card(clean)
    grace_id = _second_card(clean)
    crm = FakeCrmDoor(
        documents={ada_id: [crm_document(id=str(FRAMEWORK))]},
        pdfs={FRAMEWORK: SIGNED_PDF},
    )
    grace, ada = _user_of(clean, grace_id), _user_of(clean, ada_id)

    # Grace has no document by that id; Ada's is not signed yet, so it has no copy.
    with pytest.raises(NotFound):
        _service(clean, crm).signed_pdf(grace, FRAMEWORK)
    with pytest.raises(NotFound):
        _service(clean, crm).signed_pdf(ada, FRAMEWORK)
    assert _service(clean, crm).for_user(grace) == MemberContracts(framework=None, letters=[])
    # Never asked for a copy the list did not show as signed.
    assert not any(path.endswith("/pdf") for _, path in crm.calls)


def test_a_copy_the_crm_refuses_is_the_same_not_found(clean: Session) -> None:
    """The list says signed, the CRM then says it has no such copy (a document deleted in
    between): not found, never a 502."""
    ada_id = _card(clean)
    crm = FakeCrmDoor(documents={ada_id: [crm_document(id=str(FRAMEWORK), **SIGNED)]})

    with pytest.raises(NotFound):
        _service(clean, crm).signed_pdf(_user_of(clean, ada_id), FRAMEWORK)


def test_a_person_without_a_card_has_no_contracts_and_the_crm_is_not_asked(
    clean: Session,
) -> None:
    admin_id = _admin(clean)
    crm = FakeCrmDoor()
    with pytest.raises(NotFound, match="card"):
        _service(clean, crm).for_user(admin_id)
    assert crm.calls == []


def test_an_unreachable_crm_is_an_error_never_an_empty_page(clean: Session) -> None:
    """A list built from nothing would tell a person with a signed contract that they have
    none: the CRM's failure reaches the route, which shows the page's error line."""
    ada_id = _card(clean)
    user_id = _user_of(clean, ada_id)
    crm = FakeCrmDoor(documents={ada_id: [crm_document(id=str(FRAMEWORK))]})

    crm.down(ConnectionRefusedError(), ConnectionRefusedError())
    with pytest.raises(PigroUnavailable):
        _service(clean, crm).for_user(user_id)

    crm.down((500, b""))
    with pytest.raises(PigroUnavailable):
        _service(clean, crm).for_user(user_id)

    crm.down((200, b"not json"))
    with pytest.raises(PigroUnavailable):
        _service(clean, crm).for_user(user_id)


def test_a_transient_failure_is_asked_again_once(clean: Session) -> None:
    ada_id = _card(clean)
    crm = FakeCrmDoor(documents={ada_id: [crm_document(id=str(FRAMEWORK))]})
    crm.down((503, b'{"detail": "not now"}'))

    mine = _service(clean, crm).for_user(_user_of(clean, ada_id))

    assert mine.framework is not None
    assert len(crm.calls) == 2


def test_an_environment_without_the_token_or_https_cannot_ask(clean: Session) -> None:
    ada_id = _card(clean)
    user_id = _user_of(clean, ada_id)
    crm = FakeCrmDoor(documents={ada_id: [crm_document(id=str(FRAMEWORK))]})

    for door in (crm.door(token=""), crm.door(api_url="http://pigro.test")):
        with pytest.raises(PigroMisconfigured):
            MemberContractService(clean, door).for_user(user_id)
    # Nothing left this process, so nothing could carry the bearer in clear.
    assert crm.calls == []


def test_a_kind_or_type_the_hub_does_not_know_is_not_silently_dropped(clean: Session) -> None:
    ada_id = _card(clean)
    crm = FakeCrmDoor(documents={ada_id: [crm_document(id=str(FRAMEWORK), kind="nda")]})

    with pytest.raises(PigroUnavailable):
        _service(clean, crm).for_user(_user_of(clean, ada_id))
