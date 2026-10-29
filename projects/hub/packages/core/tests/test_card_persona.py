"""The named card (REB-855): who the CV says the person is, read by the call that writes the
anonymous card, kept beside it and shown to admins alone. The cleaning of what the model
answers, the disagreements with the profile, the anonymity guard reading the contacts, what
happens to the persona when the card fails or the CV changes, and the readers that must
never carry it."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, NO_PERSONA, PERSONA, card_response, text_pdf
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.card_draft import CardPersona, clean_persona, differenze, phone_digits
from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.cards import IDENTIFYING_AGAIN, CardWriter, stored_persona
from rebase_core.cloud import CloudTalentService
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import RecordingCall
from rebase_core.members import MemberService
from rebase_core.models import FreelancerCard
from rebase_core.schemas import FreelancerCreate
from rebase_core.talenti import TalentiService
from rebase_core.team_schemas import Card, CardPersonaRead, CloudTalentQuery

NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS.")
CV_2 = text_pdf("Charles Babbage, analyst, 2026: Python.")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in ("admin_actions", "ai_usage", "notes", "matches", "companies", "freelancers"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _apply(session: Session, telefono: str | None = None) -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        cognome="Lovelace",
        email="ada@studio.it",
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
        links=[],
    )
    read, _ = FreelancerService(session).apply(data, CV, "cv.pdf", "")
    if telefono is not None:
        session.execute(
            text("UPDATE users SET telefono = :t WHERE email = 'ada@studio.it'"), {"t": telefono}
        )
        session.commit()
    return read.id


def _writer(session: Session, llm: RecordingCall) -> CardWriter:
    return CardWriter(session, llm, now=lambda: NOW)


def _persona(**fields: Any) -> CardPersona:
    return CardPersona.model_validate({**NO_PERSONA, **fields})


# ---- what the model answers, as it is kept ------------------------------------------------


def test_the_persona_is_cleaned_and_what_does_not_pass_is_dropped() -> None:
    read = clean_persona(
        _persona(
            nome="  Ada \n",
            cognome="Lovelace",
            email=" Ada.Lovelace@Example.ORG ",
            telefono="+39 333 123 4567",
            citta="Torino",
            linkedin_url="linkedin.com/in/Ada-Lovelace/?utm_source=share",
            altri_link=[
                "ada.example.org",
                "http://ada.example.org/",
                "javascript:alert(1)",
                "mailto:ada@example.org",
                "https://linkedin.com/in/ada-lovelace",
                "not a link",
            ],
        ),
        ["Initech", " initech ", "Globex"],
    )

    assert read == CardPersonaRead(
        nome="Ada",
        cognome="Lovelace",
        email="ada.lovelace@example.org",
        telefono="+39 333 123 4567",
        citta="Torino",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace",
        altri_link=["https://ada.example.org", "http://ada.example.org/"],
        aziende=["Initech", "Globex"],
    )


def test_an_invalid_value_is_dropped_and_never_an_error() -> None:
    read = clean_persona(
        _persona(
            nome="x" * 200,
            email="ada at example dot org",
            telefono="Tel. +39 333 1234567",
            linkedin_url="https://example.com/in/ada",
        ),
        [],
    )
    assert read == CardPersonaRead()
    # A number longer than the column is not kept truncated.
    assert clean_persona(_persona(telefono="1" * 41), []).telefono is None
    assert clean_persona(_persona(telefono="12 34"), []).telefono is None
    # A LinkedIn address among the links is the profile when no other was given.
    only = clean_persona(_persona(altri_link=["https://www.linkedin.com/in/ada"]), [])
    assert (only.linkedin_url, only.altri_link) == ("https://www.linkedin.com/in/ada", [])


def test_the_links_kept_are_a_short_list() -> None:
    links = [f"https://site{n}.example.org" for n in range(12)]
    assert len(clean_persona(_persona(altri_link=links), []).altri_link) == 5


def test_the_differences_are_where_both_sides_say_something_else() -> None:
    persona = CardPersonaRead(
        nome="ada",
        cognome="LOVELACE",
        email="Ada.Other@example.org",
        telefono="333 1234567",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace",
    )
    found = differenze(
        persona,
        nome=" Ada ",
        cognome="Lovelace",
        email="ada@studio.it",
        telefono="+39 333 123 4567",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace",
    )
    # Names without case or spacing, the phone by its digits, LinkedIn by its stored shape.
    assert [(d.campo, d.cv, d.profilo) for d in found] == [
        ("email", "Ada.Other@example.org", "ada@studio.it")
    ]
    assert [
        d.campo
        for d in differenze(
            persona, nome="Ada", cognome="Byron", email="", telefono="02 5550199", linkedin_url=None
        )
    ] == [
        "cognome",
        "telefono",
    ]
    assert (
        differenze(
            CardPersonaRead(),
            nome="Ada",
            cognome="X",
            email="a@b.it",
            telefono=None,
            linkedin_url=None,
        )
        == []
    )
    assert phone_digits("+39 (333) 123-4567") == "393331234567"


# ---- the anonymous card is read against the persona ---------------------------------------


def _card(**fields: str) -> Card:
    return Card.model_validate({**CARD, **fields})


def test_the_guard_reads_the_name_the_cv_spells_the_contacts_and_the_links() -> None:
    known = KnownIdentifiers(
        cognome="Zz",
        cv_cognome="Babbage",
        cv_nome="Charles",
        emails=["charles@analytical.example"],
        phones=["393331234567"],
        hostnames=["difference-engine.example"],
    )
    assert identifying(_card(), known) == []
    assert identifying(_card(sintesi="Profilo di Charles Babbage."), known) == ["sintesi"]
    assert identifying(_card(ruolo="Senior Babbage"), known) == ["ruolo"]
    assert identifying(_card(sintesi="Scrivere a CHARLES@analytical.example."), known) == [
        "sintesi"
    ]
    # The number as the card wrote it, without the country prefix and with other separators.
    assert identifying(_card(sintesi="Telefono 333-123-4567."), known) == ["sintesi"]
    assert identifying(_card(sintesi="Sito: Difference Engine."), known) == ["sintesi"]
    # Digits that are not the number stay a card's own (a year, a version).
    assert identifying(_card(sintesi="Dal 2016 al 2024 con Python 3.12."), known) == []


def test_a_card_that_carries_the_persons_phone_is_asked_again_then_failed(clean: Session) -> None:
    person = _apply(clean)
    leaky = {**CARD, "sintesi": "Backend developer, chiamare il 333 123 4567."}
    llm = RecordingCall(
        [card_response(leaky, persona=PERSONA), card_response(leaky, persona=PERSONA)]
    )

    read = _writer(clean, llm).write(person)

    assert read.card is None and read.error is not None and "un recapito" in read.error
    assert len(llm.requests) == 2
    assert IDENTIFYING_AGAIN in llm.requests[1].messages[2].text
    assert "Campi da correggere: sintesi." in llm.requests[1].messages[2].text
    # The contacts are read all the same: the card was refused, not the persona.
    persona = stored_persona(clean, person)
    assert persona is not None and persona.telefono == "+39 333 123 4567"


def test_a_rewrite_without_the_phone_is_written(clean: Session) -> None:
    person = _apply(clean)
    leaky = {**CARD, "sintesi": "Backend developer, chiamare il 333 123 4567."}
    llm = RecordingCall([card_response(leaky, persona=PERSONA), card_response(persona=PERSONA)])

    read = _writer(clean, llm).write(person)

    assert read.error is None and read.card is not None
    assert "333" not in read.card.sintesi


# ---- what is stored, read and retired ------------------------------------------------------


def test_the_admin_read_has_the_persona_and_the_differences(clean: Session) -> None:
    person = _apply(clean, telefono="02 5550199")
    employers = ["Initech Labs"]
    writer = _writer(clean, RecordingCall([card_response(persona=PERSONA, employers=employers)]))

    writer.write(person)
    admin = CardWriter(clean, None).read_admin(person)

    assert admin.persona == CardPersonaRead(
        nome="Ada",
        cognome="Lovelace",
        email="ada.lovelace@example.org",
        telefono="+39 333 123 4567",
        citta="Torino",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace",
        altri_link=["https://ada.example.org", "https://github.com/ada"],
        aziende=employers,
    )
    assert [(d.campo, d.cv, d.profilo) for d in admin.differenze] == [
        ("email", "ada.lovelace@example.org", "ada@studio.it"),
        ("telefono", "+39 333 123 4567", "02 5550199"),
    ]
    # The anonymous card's own read has no such fields.
    plain = CardWriter(clean, None).read(person).model_dump()
    assert "persona" not in plain and "differenze" not in plain


def test_a_card_without_a_persona_reads_as_none(clean: Session) -> None:
    person = _apply(clean)
    CardWriter(clean, RecordingCall([card_response()])).write(person)
    clean.execute(text("UPDATE freelancer_cards SET persona = NULL"))
    clean.commit()

    admin = CardWriter(clean, None).read_admin(person)

    assert admin.persona is None and admin.differenze == []


def test_the_persona_is_kept_when_the_guard_refuses_the_card_and_retired_with_the_cv(
    clean: Session,
) -> None:
    person = _apply(clean)
    gendered = {**CARD, "ruolo": "Sviluppatrice backend"}
    first = RecordingCall([card_response(gendered, persona=PERSONA)] * 2)
    _writer(clean, first).write(person)
    row = clean.get(FreelancerCard, person, populate_existing=True)
    assert row is not None and row.card is None and row.error is not None
    assert stored_persona(clean, person) is not None

    # A new CV whose answer is refused outright brings no persona of its own: the old
    # one, read from the CV that is gone, goes.
    MemberService(clean).replace_cv(person, CV_2, "cv 2026.pdf", "application/pdf")
    refused = RecordingCall([card_response(stop_reason="refusal")])
    _writer(clean, refused).write(person)

    assert stored_persona(clean, person) is None


def test_a_new_cvs_card_replaces_the_persona(clean: Session) -> None:
    person = _apply(clean)
    _writer(clean, RecordingCall([card_response(persona=PERSONA)])).write(person)
    MemberService(clean).replace_cv(person, CV_2, "cv 2026.pdf", "application/pdf")
    other = {**NO_PERSONA, "nome": "Charles", "cognome": "Babbage"}

    _writer(clean, RecordingCall([card_response(persona=other)])).write(person)

    persona = stored_persona(clean, person)
    assert persona is not None and (persona.nome, persona.email) == ("Charles", None)


def test_a_persona_read_from_another_cv_is_never_shown(clean: Session) -> None:
    """Whatever left it behind (a merge, a replaced CV the card never caught up with), a
    persona tagged with another CV's hash reads as none: on the card route, and so on the
    MCP tool that reads it through the same function."""
    person = _apply(clean)
    _writer(clean, RecordingCall([card_response(persona=PERSONA)])).write(person)
    assert CardWriter(clean, None).read_admin(person).persona is not None

    clean.execute(
        text("UPDATE freelancer_cards SET persona = jsonb_set(persona, '{cv_sha256}', '\"x\"')")
    )
    clean.commit()

    admin = CardWriter(clean, None).read_admin(person)
    assert admin.persona is None and admin.differenze == []
    assert stored_persona(clean, person) is None


def test_a_failed_regeneration_of_the_same_cv_keeps_the_persona(clean: Session) -> None:
    person = _apply(clean)
    writer = _writer(clean, RecordingCall([card_response(persona=PERSONA)]))
    writer.write(person)
    again = _writer(clean, RecordingCall([card_response(stop_reason="refusal")]))

    again.write(person, force=True)

    persona = stored_persona(clean, person)
    assert persona is not None and persona.email == "ada.lovelace@example.org"


def test_deleting_the_cv_deletes_the_persona(clean: Session) -> None:
    person = _apply(clean)
    _writer(clean, RecordingCall([card_response(persona=PERSONA)])).write(person)

    CardWriter(clean, None).delete(person)
    clean.commit()

    assert stored_persona(clean, person) is None


def test_a_card_of_the_previous_prompt_waits_for_an_admin_and_is_never_written_by_the_loop(
    clean: Session,
) -> None:
    person = _apply(clean)
    _writer(clean, RecordingCall([card_response()])).write(person)
    clean.execute(text("UPDATE freelancer_cards SET prompt_version = 2, persona = NULL"))
    clean.commit()
    silent = RecordingCall([])

    assert CardWriter(clean, silent).refresh_stale().written == 0
    assert silent.requests == []
    assert [item.reason for item in CardWriter(clean, None).stale_cards()] == ["prompt"]


# ---- where the persona must never be ------------------------------------------------------


def test_no_other_reader_of_a_card_carries_the_contacts(clean: Session) -> None:
    person = _apply(clean)
    _writer(
        clean, RecordingCall([card_response(persona=PERSONA, employers=["Initech Labs"])])
    ).write(person)
    clean.execute(
        text("UPDATE freelancers SET vetted_at = now(), stato = 'attivo' WHERE id = :f"),
        {"f": person},
    )
    clean.commit()

    reads = {
        "cloud": CloudTalentService(clean).list(CloudTalentQuery()).model_dump(),
        "catalogue": TalentiService(clean).list_recent().model_dump(),
        "card": CardWriter(clean, None).read(person).model_dump(),
    }
    for name, read in reads.items():
        body = json.dumps(read, default=str).lower()
        for secret in ("example.org", "333 123", "3331234567", "initech", "persona"):
            assert secret not in body, (name, secret)
