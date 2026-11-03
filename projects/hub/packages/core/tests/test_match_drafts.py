"""A match before it is written: `prefill`, `proposal` and `check` (REB-476), which write nothing.
Helpers: `match_fixtures`."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from match_fixtures import (
    CLIENTE_REST,
    TODAY,
    _admin,
    _body,
    _card,
    _fiscal,
    _framework,
    _framework_now,
    _request,
    _rows,
    _service,
    _setup,
    clean,  # noqa: F401  (fixture)
)
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import (
    ClienteData,
    LetteraFields,
    MatchCheck,
    MatchCreate,
)
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Freelancer


def test_the_client_budget_reaches_neither_the_prefill_nor_the_letter(clean: Session) -> None:
    # The whole amount with its dot: a bare "777" could turn up inside a UUID's hex or a
    # timestamp's microseconds and fail this test for nothing.
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    assert "777.77" not in service.drafts.prefill(freelancer_id, company_id).model_dump_json()
    match = service.create(freelancer_id, _body(company_id), admin_id)
    letter = clean.get(ContractDocument, match.lettera.id)
    assert letter is not None
    assert not any("777.77" in str(value) for value in letter.data.values())
    assert not any("budget" in key for key in letter.data)


def test_the_prefill_suggests_from_the_request_the_card_and_rebases_defaults(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    prefill = _service(clean).drafts.prefill(freelancer_id, company_id)
    lettera = prefill.lettera
    assert (lettera.ruolo, lettera.attivita) == (
        "Backend developer",
        "Le API del prodotto, per tre mesi.",
    )
    assert (lettera.data_inizio, lettera.impegno) == (date(2026, 10, 1), "3 mesi")
    assert lettera.luogo == "in parte da remoto, con 2 giornate a settimana presso il Cliente"
    assert lettera.referente_cliente == "Wile E."
    assert (lettera.modalita, lettera.unita) == ("a giornata", "a giornata")
    assert lettera.compenso == Decimal("450.00")
    assert (lettera.giorni_pagamento, lettera.fine_mese) == (30, True)
    assert prefill.cliente.cliente_ragione_sociale == "ACME Srl"
    assert prefill.cliente.cliente_piva is None
    assert prefill.fiscale is not None and prefill.fiscale.partita_iva == "01234567890"
    assert (prefill.quadro_attivo, prefill.quadro_necessario, prefill.lettera_in_attesa) == (
        None,
        True,
        True,
    )


def test_a_card_without_a_day_rate_prefills_no_fee_and_the_letter_needs_one(clean: Session) -> None:
    """A card drafted from a signup has no rate yet."""
    admin_id, freelancer_id, company_id = _setup(clean)
    card = clean.get(Freelancer, freelancer_id)
    assert card is not None
    card.tariffa_giornaliera = None
    clean.commit()
    assert _service(clean).drafts.prefill(freelancer_id, company_id).lettera.compenso is None
    with pytest.raises(PydanticValidationError, match="compenso"):
        LetteraFields(
            ruolo="Backend developer",
            attivita="API",
            data_inizio=date(2026, 10, 1),
            giorni_pagamento=30,
            fine_mese=True,
        )  # type: ignore[call-arg]


def test_the_client_data_come_back_from_the_same_company_users_last_match(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    # The same referente files a second request: another row, the same client.
    second = _request(clean, nome_azienda="ACME", figura_richiesta="Frontend developer")
    cliente = _service(clean).drafts.prefill(freelancer_id, second).cliente
    assert (cliente.cliente_ragione_sociale, cliente.cliente_piva, cliente.cliente_sede) == (
        "ACME S.r.l.",
        "01234567890",
        "Milano",
    )


def test_a_cancelled_matchs_client_is_never_prefilled(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    _service(clean).cancel(match.id, admin_id)
    # The same referente files a second request: another row, the same user.
    second = _request(clean, nome_azienda="ACME", figura_richiesta="Frontend developer")
    cliente = _service(clean).drafts.prefill(freelancer_id, second).cliente
    assert (cliente.cliente_ragione_sociale, cliente.cliente_piva, cliente.cliente_sede) == (
        "ACME",
        None,
        None,
    )


def test_a_closed_request_can_be_neither_previewed_nor_matched(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).set_status(
        company_id, StatusChange(stato="chiuso"), admin_id=admin_id, fonte="web"
    )
    service = _service(clean)
    for call in (
        lambda: service.create(freelancer_id, _body(company_id), admin_id),
        lambda: service.drafts.preview(freelancer_id, _body(company_id), "lettera"),
    ):
        with pytest.raises(ValidationFailed) as refused:
            call()
        assert refused.value.details["field"] == "company_id"


def test_check_writes_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    before = _rows(clean)
    # No renderer: a check typesets nothing.
    checked = MatchService(clean, today=lambda: TODAY).drafts.check(
        freelancer_id, _body(company_id)
    )
    clean.commit()
    assert checked.quadro_necessario is True
    assert _rows(clean) == before
    assert _service(clean).create(freelancer_id, _body(company_id), admin_id).lettera.numero == (
        "2026-002"
    )


def test_check_says_what_saving_would_do_and_which_document_leaves_first(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    riepilogo = [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
        "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese.",
    ]
    assert service.drafts.check(freelancer_id, _body(company_id)) == MatchCheck(
        riepilogo=riepilogo,
        cosa_succede=(
            "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua "
            "firma."
        ),
        quadro_necessario=True,
        dati_fiscali_mancanti=False,
        regime="partita_iva",
    )
    service.create(freelancer_id, _body(company_id), admin_id)
    _framework_now(clean, freelancer_id, "inviato")
    in_signature = service.drafts.check(freelancer_id, _body(company_id))
    assert (in_signature.quadro_necessario, in_signature.cosa_succede) == (
        False,
        "Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua "
        "firma.",
    )
    _framework(clean, freelancer_id, admin_id)
    active = service.drafts.check(freelancer_id, _body(company_id))
    assert (active.quadro_necessario, active.cosa_succede) == (
        False,
        "Il contratto quadro è già attivo: parte subito la lettera di incarico.",
    )
    assert active.riepilogo == riepilogo


@pytest.mark.parametrize("action", ["create", "check"])
def test_check_refuses_what_create_refuses(clean: Session, action: str) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)

    def attempt(freelancer: UUID, body: MatchCreate) -> object:
        if action == "create":
            return service.create(freelancer, body, admin_id)
        return service.drafts.check(freelancer, body)

    with pytest.raises(NotFound):
        attempt(freelancer_id, _body(uuid4()))
    with pytest.raises(NotFound):
        attempt(uuid4(), _body(company_id))
    CompanyService(clean).set_status(
        company_id, StatusChange(stato="chiuso"), admin_id=admin_id, fonte="web"
    )
    with pytest.raises(ValidationFailed) as refused:
        attempt(freelancer_id, _body(company_id))
    assert refused.value.details["field"] == "company_id"


def test_check_reports_missing_tax_data_that_create_still_refuses(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    service = _service(clean)
    checked = service.drafts.check(freelancer_id, _body(company_id))
    assert checked.dati_fiscali_mancanti is True
    assert checked.riepilogo[-1] == (
        "Mancano i dati fiscali del freelance: servono prima di salvare."
    )
    with pytest.raises(ValidationFailed) as refused:
        service.create(freelancer_id, _body(company_id), admin_id)
    assert refused.value.details["field"] == "fiscale"
    _fiscal(clean, freelancer_id, admin_id)
    assert service.drafts.check(freelancer_id, _body(company_id)).dati_fiscali_mancanti is False


def test_the_proposal_keeps_the_prefill(clean: Session) -> None:
    _admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    proposal = service.drafts.proposal(freelancer_id, company_id, cliente=CLIENTE_REST)
    prefill = service.drafts.prefill(freelancer_id, company_id)
    assert (proposal.id, proposal.company_id) == (None, company_id)
    assert proposal.cliente == ClienteData(cliente_ragione_sociale="ACME Srl", **CLIENTE_REST)
    assert proposal.lettera.model_dump(exclude_none=True) == prefill.lettera.model_dump(
        exclude_none=True
    )


def test_the_proposal_lays_the_given_fields_over_the_prefill(clean: Session) -> None:
    _admin_id, freelancer_id, company_id = _setup(clean)
    match_id = uuid4()
    proposal = _service(clean).drafts.proposal(
        freelancer_id,
        company_id,
        cliente={**CLIENTE_REST, "cliente_ragione_sociale": "ACME S.r.l."},
        lettera={"ruolo": "Staff engineer", "data_fine": "2026-12-31", "impegno": None},
        match_id=match_id,
    )
    assert proposal.id == match_id
    assert proposal.cliente.cliente_ragione_sociale == "ACME S.r.l."
    assert (proposal.lettera.ruolo, proposal.lettera.data_fine, proposal.lettera.impegno) == (
        "Staff engineer",
        date(2026, 12, 31),
        None,
    )
    assert proposal.lettera.attivita == "Le API del prodotto, per tre mesi."
    assert proposal.lettera.compenso == Decimal("450")


@pytest.mark.parametrize(
    ("typed", "fee"),
    [
        ("1.500", Decimal("1500")),
        ("1.234,50", Decimal("1234.50")),
        ("480,50", Decimal("480.50")),
        ("480.50", Decimal("480.50")),
        # A number is already one: it goes to the letter as it is.
        (480, Decimal("480")),
        (Decimal("480.50"), Decimal("480.50")),
    ],
)
def test_the_proposal_reads_a_fee_typed_the_italian_way(
    clean: Session, typed: object, fee: Decimal
) -> None:
    """REB-485: «1.500» is fifteen hundred, where Pydantic alone reads one and a half."""
    _admin_id, freelancer_id, company_id = _setup(clean)
    proposal = _service(clean).drafts.proposal(
        freelancer_id, company_id, cliente=CLIENTE_REST, lettera={"compenso": typed}
    )
    assert proposal.lettera.compenso == fee


@pytest.mark.parametrize(
    "typed", ["1e3", "1_000", "\u0661\u0665\u0660\u0660", "1,000.00", "-1.500", "12,345", "tanto"]
)
def test_the_proposal_refuses_a_fee_that_is_not_an_amount_by_name(
    clean: Session, typed: str
) -> None:
    """REB-485: what Pydantic alone would take («1e3», «1_000», Arabic-Indic digits) is
    refused as the web refuses it, and English notation is never read as cents."""
    _admin_id, freelancer_id, company_id = _setup(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).drafts.proposal(
            freelancer_id, company_id, cliente=CLIENTE_REST, lettera={"compenso": typed}
        )
    assert (refused.value.details["field"], refused.value.details["reason"]) == (
        "lettera.compenso",
        "non valido",
    )


@pytest.mark.parametrize(
    ("cliente", "lettera", "field"),
    [
        ({**CLIENTE_REST, "nome": "ACME"}, None, "cliente.nome"),
        (CLIENTE_REST, {"budget_giornaliero": 777}, "lettera.budget_giornaliero"),
    ],
)
def test_the_proposal_refuses_an_unknown_key_by_name(
    clean: Session, cliente: dict[str, object], lettera: dict[str, object] | None, field: str
) -> None:
    _admin_id, freelancer_id, company_id = _setup(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).drafts.proposal(freelancer_id, company_id, cliente=cliente, lettera=lettera)
    assert refused.value.details["field"] == field


@pytest.mark.parametrize(
    ("cliente", "lettera", "field", "reason"),
    [
        # A client never matched before: the prefill knows only its name.
        (None, None, "cliente.cliente_piva", "manca"),
        (CLIENTE_REST, {"compenso": None}, "lettera.compenso", "manca"),
        # Pydantic's own checks say it in English: the admin reads «non valido».
        (CLIENTE_REST, {"compenso": "abc"}, "lettera.compenso", "non valido"),
        (CLIENTE_REST, {"giorni_pagamento": "trenta"}, "lettera.giorni_pagamento", "non valido"),
        # The hub's own checks already say it in Italian, and say why.
        (CLIENTE_REST, {"impegno": "   "}, "lettera.impegno", "serve un valore, non solo spazi"),
        (
            CLIENTE_REST,
            {"data_fine": "2026-09-01"},
            "lettera.data_fine",
            "la fine prevista viene prima dell'inizio",
        ),
        (
            CLIENTE_REST,
            {"giorni_pagamento": 45, "fine_mese": True},
            "lettera.giorni_pagamento",
            "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)",
        ),
        # `SafeStr`'s own sentence names the field already: said once, not twice.
        (
            CLIENTE_REST,
            {"ruolo": "Backend\x00developer"},
            "lettera.ruolo",
            "il testo contiene un carattere nullo (\\x00), non ammesso",
        ),
    ],
)
def test_the_proposal_refuses_a_field_left_empty_or_invalid_by_name(
    clean: Session,
    cliente: dict[str, object] | None,
    lettera: dict[str, object] | None,
    field: str,
    reason: str,
) -> None:
    _admin_id, freelancer_id, company_id = _setup(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).drafts.proposal(freelancer_id, company_id, cliente=cliente, lettera=lettera)
    assert (refused.value.details["field"], refused.value.details["reason"]) == (field, reason)
