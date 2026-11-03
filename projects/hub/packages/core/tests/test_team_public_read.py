"""What a proposal shows to whom (REB-511, REB-520): the public read hides ids and places, the
cloud read names each member, and a place a summary or a reason would give away is withheld.
"""

import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from fakes_cards import CARD
from sqlalchemy.orm import Session
from team_fixtures import (
    DESCRIZIONE,
    NOW,
    a_talent,
    a_user,
    catalogue_positions,
    make_builder,
    proposal_response,
    proposal_rows,
    team_member,
)

from rebase_core.bands import Band
from rebase_core.db import uuid7
from rebase_core.errors import (
    NotFound,
    TeamBuilderBusy,
)
from rebase_core.llm import (
    RecordingCall,
)
from rebase_core.models import Freelancer, FreelancerCard
from rebase_core.team_public import (
    PLACE_WITHHELD_REASON,
    PLACE_WITHHELD_RIASSUNTO,
    PLACE_WITHHELD_SUMMARY,
)
from rebase_core.team_schemas import CardRead, TeamProposalCreate


@pytest.fixture
def logs(clean_hub: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on the engine's logger, after `clean_hub`: Alembic's `fileConfig` disables
    every logger `alembic.ini` does not list, this one among them."""
    logging.getLogger("rebase_core.team_builder").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.team_builder")
    return caplog


# ---- the reads, the row, the event -------------------------------------------------------


def test_public_read_hides_ids_and_luogo(clean_hub: Session) -> None:
    first = a_talent(clean_hub, 1)
    second = a_talent(clean_hub, 2, card={**CARD, "luogo": "Bologna"})
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[first]), team_member(ids[second])])])
    builder = make_builder(clean_hub, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [(m.freelancer_id, m.scheda.luogo) for m in public.team] == [(None, None)] * 2
    body = public.model_dump_json()
    for secret in (str(first), str(second), "Torino", "Bologna"):
        assert secret not in body
    # The admin's read of the same proposal keeps both; the public read by id hides both.
    admin = builder.get(public.id, public=False)
    assert [(m.freelancer_id, m.scheda.luogo) for m in admin.team] == [
        (first, "Torino"),
        (second, "Bologna"),
    ]
    assert builder.get(public.id, public=True) == public
    with pytest.raises(NotFound):
        builder.get(uuid7(), public=True)


def test_the_public_read_by_id_answers_a_fresh_public_proposal_and_nothing_else(
    clean_hub: Session,
) -> None:
    """REB-675: `/hub/team?proposta=<id>` opens on `get_public`, which answers the same
    read the proposal was first handed, what it was asked with included, inside the
    window «Rigenera» gives it, and `NotFound` for a cloud proposal, an attempt or a
    proposal a day old, so the answer never says which proposals exist."""
    first = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[first])])] * 2)
    builder = make_builder(clean_hub, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=1), origine="pubblico", user_id=None
    )
    cloud = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE),
        origine="cloud",
        user_id=a_user(clean_hub, "c@x.it"),
    )

    read = builder.get_public(public.id)
    assert read == public
    assert (read.descrizione, read.persone) == (DESCRIZIONE, 1)
    assert [m.freelancer_id for m in read.team] == [None]
    assert builder.get_public(public.id, now=NOW + timedelta(hours=23, minutes=59)) == public
    for proposal_id, now in (
        (public.id, NOW + timedelta(days=1)),
        (cloud.id, NOW),
        (uuid7(), NOW),
    ):
        with pytest.raises(NotFound):
            builder.get_public(proposal_id, now=now)
    builder.record_refusal(
        TeamProposalCreate(descrizione=DESCRIZIONE),
        origine="pubblico",
        user_id=None,
        error=TeamBuilderBusy("Troppe richieste in questo momento: riprova tra un minuto."),
    )
    attempt = next(row for row in proposal_rows(clean_hub) if row.errore is not None)
    with pytest.raises(NotFound):
        builder.get_public(attempt.id)


def test_the_cloud_read_names_each_member_and_the_public_read_never(clean_hub: Session) -> None:
    """The cloud shows who each person is (spec § 4.2), so its read carries the name
    and the surname beside the id; the public read carries neither, of a public
    proposal or of a cloud one read by id."""
    first = a_talent(clean_hub, 1)
    second = a_talent(clean_hub, 2)
    ids = catalogue_positions(clean_hub)
    user_id = a_user(clean_hub, "referente@acme.it")
    team = [team_member(ids[first]), team_member(ids[second])]
    llm = RecordingCall([proposal_response(team), proposal_response(team)])
    builder = make_builder(clean_hub, llm)

    cloud = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="cloud", user_id=user_id
    )
    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [(m.freelancer_id, m.nome, m.cognome) for m in cloud.team] == [
        (first, "Ada", "Lovelace1"),
        (second, "Ada", "Lovelace2"),
    ]
    assert builder.get(cloud.id, public=False) == cloud
    for anonymous in (public, builder.get(cloud.id, public=True)):
        assert [(m.nome, m.cognome) for m in anonymous.team] == [(None, None)] * 2
        assert "Lovelace" not in anonymous.model_dump_json()


def test_a_public_reason_that_names_the_cards_place_is_withheld(clean_hub: Session) -> None:
    """The prompt forbids a place in a motivazione; one that names the place on the
    member's card anyway would give back what the public read withholds, so that read
    says the hub's sentence, in any case and for any word of the place but its kind;
    the admin's read keeps the model's words, and a place the card does not hold stays."""
    places = ["Provincia di Bergamo", "Torino", "Verona", "Roma", "Alto Adige"]
    ids = {
        a_talent(clean_hub, n, card={**CARD, "luogo": luogo}): luogo
        for n, luogo in enumerate(places, start=1)
    }
    positions = catalogue_positions(clean_hub)
    reasons = [
        "Lavora a BERGAMO, vicino al cliente, e conosce il dominio.",
        "Nove anni di API in Python, quello che serve al gestionale.",
        "Ha già lavorato in provincia con aziende come questa.",
        "Vive a Milano e conosce la logistica.",
        "Competenze di alto livello su Kafka, quello che serve alla pipeline.",
    ]
    llm = RecordingCall(
        [
            proposal_response(
                [
                    team_member(positions[freelancer_id], motivazione=reason)
                    for freelancer_id, reason in zip(ids, reasons, strict=True)
                ]
            )
        ]
    )
    builder = make_builder(clean_hub, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    # «di alto livello» is no place: a word of the place counts only with a capital.
    assert [member.motivazione for member in public.team] == [
        PLACE_WITHHELD_REASON,
        reasons[1],
        reasons[2],
        reasons[3],
        reasons[4],
    ]
    assert PLACE_WITHHELD_REASON == "Profilo adatto al ruolo."
    assert "BERGAMO" not in public.model_dump_json()
    admin = builder.get(public.id, public=False)
    assert [member.motivazione for member in admin.team] == reasons


def test_a_public_summary_that_names_a_members_place_is_withheld(clean_hub: Session) -> None:
    """The prompt lets the summary name the place the description names and no place of
    a person; a summary that names a member's card place anyway would give back what the
    public read withholds, unless that place is the one the description names as the
    client's (`luogo.dove`). The row and the admin's read keep the model's words."""
    torino = a_talent(clean_hub, 1, card={**CARD, "luogo": "Torino"})
    verona = a_talent(clean_hub, 2, card={**CARD, "luogo": "Provincia di Verona"})
    alba = a_talent(clean_hub, 3, card={**CARD, "luogo": "Alba"})
    rho = a_talent(clean_hub, 4, card={**CARD, "luogo": "Rho"})
    positions = catalogue_positions(clean_hub)
    kept = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa lavora da remoto."
    leaked = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a VERONA."
    lowered = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a verona."
    dawn = "Un'azienda cerca un backend developer per un turno all'alba: chi lo fa vive ad Alba."
    copied = "Un'azienda cerca un backend developer in sede dal cliente: chi lo fa vive ad Alba."
    short = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a Rho."
    llm = RecordingCall(
        [
            proposal_response(
                [team_member(positions[torino])], riassunto=kept, locale=True, dove="Torino"
            ),
            proposal_response(
                [team_member(positions[verona])], riassunto=leaked, locale=True, dove="Torino"
            ),
            proposal_response(
                [team_member(positions[verona])], riassunto=lowered, locale=True, dove="Torino"
            ),
            proposal_response(
                [team_member(positions[alba])], riassunto=dawn, locale=False, dove=None
            ),
            proposal_response(
                [team_member(positions[alba])], riassunto=copied, locale=True, dove="Alba"
            ),
            proposal_response(
                [team_member(positions[rho])], riassunto=short, locale=True, dove="Torino"
            ),
        ]
    )
    builder = make_builder(clean_hub, llm)
    descrizione = "Cerchiamo un backend developer in sede a torino due giorni a settimana."

    named = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld_lower = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld_dawn = builder.propose(
        TeamProposalCreate(
            descrizione="Cerchiamo un backend developer per le integrazioni che partono all'alba."
        ),
        origine="pubblico",
        user_id=None,
    )
    withheld_copied = builder.propose(
        TeamProposalCreate(descrizione="Cerchiamo un backend developer in sede dal cliente."),
        origine="pubblico",
        user_id=None,
    )
    withheld_short = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )

    # «Torino» is the place the description names as the client's, whatever case the
    # visitor typed it in; «Verona» is the catalogue's alone, and the summary is read in
    # any case, unlike a card's field; «all'alba» in a description is no pass for Alba,
    # and neither is an «Alba» the model wrote into `dove` on its own.
    assert named.riassunto == kept
    assert withheld.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_lower.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_dawn.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_copied.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_short.riassunto == PLACE_WITHHELD_RIASSUNTO
    # The public `luogo` keeps a `dove` the visitor wrote and drops one the model made up.
    assert named.luogo.model_dump() == {"locale": True, "dove": "Torino"}
    assert withheld_copied.luogo.model_dump() == {"locale": True, "dove": None}
    assert builder.get(withheld_copied.id, public=False).luogo.model_dump() == {
        "locale": True,
        "dove": "Alba",
    }
    assert PLACE_WITHHELD_RIASSUNTO == (
        "Il riassunto di questa proposta non è pubblico: la modalità di lavoro di ogni "
        "persona proposta è sulla sua scheda."
    )
    assert "VERONA" not in withheld.model_dump_json()
    assert "Alba" not in withheld_dawn.model_dump_json()
    assert "Alba" not in withheld_copied.riassunto
    assert builder.get(withheld.id, public=False).riassunto == leaked
    assert builder.get(withheld.id, public=True).riassunto == PLACE_WITHHELD_RIASSUNTO


def test_a_public_card_names_its_place_nowhere(clean_hub: Session) -> None:
    """The public read withholds `luogo`, so a card that names the place anywhere else
    (one written against the prompt, or before its rule) gives none of it back: the
    role reads as the role in this team, the summary as the hub's sentence, and a skill
    or a sector that names it is left out. The admin's read keeps the card whole."""
    card = {
        **CARD,
        "ruolo": "Backend developer a Bergamo",
        "competenze": ["Python", "Rete Bergamo Smart City"],
        "settori": ["fintech", "turismo bergamasco", "logistica di Bergamo"],
        "luogo": "Bergamo",
        "sintesi": "Backend developer senior di base a Bergamo, nove anni di fintech.",
    }
    freelancer_id = a_talent(clean_hub, 1, card=card)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall(
        [proposal_response([team_member(ids[freelancer_id], ruolo="Backend lead")])]
    )
    builder = make_builder(clean_hub, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    [scheda] = [member.scheda for member in public.team]
    assert scheda.luogo is None
    assert scheda.ruolo == "Backend lead"
    assert scheda.sintesi == PLACE_WITHHELD_SUMMARY
    assert PLACE_WITHHELD_SUMMARY == "La sintesi di questo profilo non è pubblica."
    assert scheda.competenze == ["Python"]
    # «bergamasco» is another word: the guard reads the place's own words.
    assert scheda.settori == ["fintech", "turismo bergamasco"]
    assert "Bergamo" not in public.model_dump_json()
    [admin] = [member.scheda for member in builder.get(public.id, public=False).team]
    assert admin == CardRead.model_validate(card)


@pytest.mark.parametrize("change", ["deleted", "scartato"])
def test_a_read_leaves_out_who_left_the_catalogue(clean_hub: Session, change: str) -> None:
    staying = a_talent(clean_hub, 1)
    leaving = a_talent(clean_hub, 2, tariffa=Decimal("300.00"))
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[staying]), team_member(ids[leaving])])])
    builder = make_builder(clean_hub, llm)
    proposal = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )
    row = clean_hub.get(Freelancer, leaving)
    assert row is not None
    if change == "deleted":
        row.deleted_at = NOW
    else:
        row.stato = "scartato"
    clean_hub.commit()

    read = builder.get(proposal.id, public=False)

    assert [(m.posizione, m.freelancer_id) for m in read.team] == [(1, staying)]
    assert read.economia.giorno == Band(min=500, max=650)  # the one left, alone
    [stored] = proposal_rows(clean_hub)
    assert len(stored.team) == 2  # the row keeps them


def test_a_read_skips_a_card_that_no_longer_validates(
    clean_hub: Session, logs: pytest.LogCaptureFixture
) -> None:
    first = a_talent(clean_hub, 1)
    second = a_talent(clean_hub, 2)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[first]), team_member(ids[second])])])
    builder = make_builder(clean_hub, llm)
    proposal = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )
    card = clean_hub.get(FreelancerCard, second)
    assert card is not None
    card.card = {"ruolo": "Ruolo segreto", "anni": "nove"}
    clean_hub.commit()

    read = builder.get(proposal.id, public=True)

    assert [m.posizione for m in read.team] == [1]
    assert f"{proposal.id}: member 2" in logs.text and "not a card" in logs.text
    assert "Ruolo segreto" not in logs.text
