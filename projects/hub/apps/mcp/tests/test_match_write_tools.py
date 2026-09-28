"""REB-478: every action «Crea match» and «Match e contratti» take, over MCP. Each tool
calls the service the admin API calls, with the calling admin as the actor, and answers
links to the PDFs: never their bytes, the tax data or the client's budget. Nothing here
runs pandoc, reaches Documenso or sends a mail (`FakeRenderer`, `FakeDocumenso`,
`RecordingSender`)."""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_match_tools import _wipe
from test_tools import IVAN, PDF, _payload

from rebase_core.admin_tokens import AdminRead
from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import FiscalData
from rebase_core.contracts.fields import Value
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import RecordingSender
from rebase_core.models import (
    AdminAction,
    ContractDocument,
    FreelancerFiscal,
    LetterCounter,
    Match,
    User,
)
from rebase_core.schemas import CompanyCreate, FreelancerCreate
from rebase_core.signing import SigningService
from rebase_mcp.server import build_server

# A second admin: the one who calls a tool is who the trail names, not who made the match.
GRACE = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000002"),
    email="grace@rebase.it",
    nome="Grace",
    attivo=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)
# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER: dict[str, Value] = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}
CONTRACTS_MAIL = "contratti@rebase.test"
SIGNED_AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
# The freelancer's tax data and the client's budget: values no answer may carry, each
# unlike anything else the flow prints (the client's own VAT number is another).
TAX = {
    "codice_fiscale": "LVLDAA85T50H501Z",
    "partita_iva": "09876543210",
    "domicilio": "Via dei Segreti 7, Torino",
    "pec": "ada.lovelace@pec.it",
}
BUDGET = Decimal("733.33")
SECRETS = (*TAX.values(), "733.33", "733,33", "%PDF")
FORBIDDEN_KEYS = {*TAX, "budget_giornaliero", "fiscale", "pdf", "signed_pdf", "data"}
CLIENTE = {
    "cliente_ragione_sociale": "ACME S.r.l.",
    "cliente_piva": "01234567890",
    "cliente_sede": "Milano",
}
# What a saved match writes; the seed's own tax data already left one trail entry.
WRITTEN = (Match, ContractDocument, LetterCounter, AdminAction)


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node).union(*(_keys(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(item) for item in node))
    return set()


def _clean(body: Any) -> Any:
    """Every new tool's answer, checked the same way: no key that would carry a tax
    identifier, the client's budget, a document's bytes or its `data`, and none of the
    seeded values anywhere in its JSON."""
    assert not _keys(body) & FORBIDDEN_KEYS
    dumped = json.dumps(body, ensure_ascii=False)
    assert not [secret for secret in SECRETS if secret in dumped]
    return body


@dataclass
class World:
    factory: sessionmaker[Session]
    freelancer_id: str
    company_id: str
    renderer: FakeRenderer
    documenso: FakeDocumenso
    sender: RecordingSender

    def server(self, admin: AdminRead = IVAN) -> MCPServer:
        def signing(session: Session) -> SigningService:
            return SigningService(
                session,
                renderer=self.renderer,
                documenso=self.documenso.client(),
                sender=self.sender,
                signer=SIGNER,
                contracts_mail=CONTRACTS_MAIL,
            )

        return build_server(self.factory, lambda: admin, renderer=self.renderer, signing=signing)

    def document(self, document_id: str) -> ContractDocument:
        session = self.factory()
        try:
            document = session.get(ContractDocument, UUID(document_id))
            assert document is not None
            session.expunge(document)
            return document
        finally:
            session.close()

    def count(self, model: type[Any]) -> int:
        session = self.factory()
        try:
            return session.scalar(select(func.count()).select_from(model)) or 0
        finally:
            session.close()

    def written(self) -> list[int]:
        return [self.count(model) for model in WRITTEN]

    def trail(self, entity: str, entity_id: str) -> list[tuple[str, UUID]]:
        """Who did what to `entity`, newest first."""
        session = self.factory()
        try:
            entries = AdminActionService(session).timeline(entity, UUID(entity_id))
            return [(entry.kind, entry.admin_id) for entry in entries]
        finally:
            session.close()


def _seed(factory: sessionmaker[Session], *, fiscal: bool = True) -> tuple[str, str]:
    session = factory()
    try:
        for admin in (IVAN, GRACE):
            session.add(
                User(id=admin.id, email=admin.email, nome=admin.nome, cognome="", role="admin")
            )
        session.commit()
        card, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        request, _ = CompanyService(session).request(
            CompanyCreate(
                nome_azienda="ACME Srl",
                referente_nome="Wile",
                referente_cognome="E.",
                email="wile@acme.it",
                telefono="+39 345 1234567",
                figura_richiesta="Backend developer",
                progetto="Un backend developer per tre mesi.",
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=BUDGET,
                remoto="remoto",
                numero_risorse=1,
            )
        )
        if fiscal:
            FiscalService(session).save(card.id, FiscalData.model_validate(TAX), IVAN.id)
        return str(card.id), str(request.id)
    finally:
        session.close()


@pytest.fixture
def world(factory: sessionmaker[Session]) -> Iterator[World]:
    freelancer_id, company_id = _seed(factory)
    yield World(
        factory,
        freelancer_id,
        company_id,
        FakeRenderer(draft=False),
        FakeDocumenso(),
        RecordingSender(),
    )
    _wipe(factory)


async def _create(client: Client, world: World, **given: Any) -> dict[str, Any]:
    arguments = {"freelancer_id": world.freelancer_id, "company_id": world.company_id, **given}
    result = await client.call_tool("create_match", arguments)
    assert not result.is_error, result.content
    return _clean(_payload(result))


async def _call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return _clean(_payload(result))


async def _refused(client: Client, tool: str, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    assert result.is_error
    text = str(result.content[0].text)  # type: ignore[union-attr]
    assert not [secret for secret in SECRETS if secret in text]
    return text.removeprefix(f"Error executing tool {tool}: ")


def _link(document: dict[str, Any]) -> str:
    return f"/api/hub/contract-documents/{document['id']}/pdf"


# ---- preview and create ---------------------------------------------------------------


async def test_preview_match_answers_the_sentences_and_writes_nothing(world: World) -> None:
    before = world.written()
    async with Client(world.server()) as client:
        body = await _call(
            client,
            "preview_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"data_fine": "2026-12-31"},
        )
    assert body["riepilogo"][0] == (
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, da remoto, dal 1° ottobre "
        "2026 al 31 dicembre 2026."
    )
    assert body["cosa_succede"] == (
        "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma."
    )
    assert (body["quadro_necessario"], body["dati_fiscali_mancanti"]) == (True, False)
    # What would be written: the client as given, the letter as the hub proposes it with
    # the one field laid over it.
    assert body["cliente"] == CLIENTE
    assert body["condizioni"]["data_fine"] == "2026-12-31"
    assert body["condizioni"]["ruolo"] == "Backend developer"
    assert Decimal(body["condizioni"]["compenso"]) == Decimal("450")
    assert world.written() == before


@pytest.mark.parametrize(("typed", "fee"), [("1.500", "1500"), ("1.234,50", "1234.50")])
async def test_preview_match_reads_the_fee_the_italian_way(
    world: World, typed: str, fee: str
) -> None:
    """REB-485: an agent passing «1.500» as the fee means fifteen hundred."""
    async with Client(world.server()) as client:
        body = await _call(
            client,
            "preview_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"compenso": typed},
        )
        garbage = await _refused(
            client,
            "preview_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"compenso": "tanto"},
        )
    assert Decimal(body["condizioni"]["compenso"]) == Decimal(fee)
    assert "condizioni.compenso: non valido" in garbage


@pytest.mark.parametrize("typed", ["1e3", "1,000.00"])
async def test_preview_match_refuses_a_fee_pydantic_alone_would_misread(
    world: World, typed: str
) -> None:
    """REB-485: «1e3» is not an amount on the web, and «1,000.00» is not 1.00."""
    async with Client(world.server()) as client:
        refused = await _refused(
            client,
            "preview_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"compenso": typed},
        )
    assert "condizioni.compenso: non valido" in refused


async def test_create_match_saves_the_draft_with_the_fields_given(world: World) -> None:
    async with Client(world.server()) as client:
        body = await _create(client, world, cliente=CLIENTE, condizioni={"ruolo": "Staff engineer"})
    assert (body["stato"], body["cliente_ragione_sociale"]) == ("bozza", "ACME S.r.l.")
    assert body["situazione"] == (
        f"La lettera n. {body['lettera']['numero']} è pronta: il freelance non ha ancora ricevuto "
        "nulla."
    )
    assert (body["prossima_azione"], body["altre_azioni"]) == ("invia", ["annulla"])
    assert body["lettera"]["pdf_url"] == _link(body["lettera"])
    assert body["created_by"] == str(IVAN.id)
    assert world.document(body["lettera"]["id"]).data["ruolo"] == "Staff engineer"


async def test_create_match_saves_the_expected_days_beside_the_letter(world: World) -> None:
    """`giorni_previsti` (REB-501, spec § 3.4) sits on the match, forwarded into
    `MatchCreate` beside `cliente`/`condizioni` rather than inside them: the letter's own
    fields and text (the summary this asserts against) do not change from this number."""
    async with Client(world.server()) as client:
        body = await _create(
            client,
            world,
            cliente=CLIENTE,
            condizioni={"ruolo": "Staff engineer"},
            giorni_previsti=40,
        )
    assert body["giorni_previsti"] == 40
    assert world.document(body["lettera"]["id"]).data["ruolo"] == "Staff engineer"
    assert "giorni_previsti" not in world.document(body["lettera"]["id"]).data


async def test_create_match_with_no_giorni_previsti_leaves_it_unset(world: World) -> None:
    async with Client(world.server()) as client:
        body = await _create(client, world, cliente=CLIENTE)
    assert body["giorni_previsti"] is None


async def test_create_match_refuses_an_out_of_range_giorni_previsti(world: World) -> None:
    before = world.written()
    async with Client(world.server()) as client:
        refused = await _refused(
            client,
            "create_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            giorni_previsti=0,
        )
    # The core's own sentence (REB-502), not Pydantic's English and no longer «non valido».
    assert refused == "giorni_previsti: I giorni previsti vanno da 1 a 366."
    assert world.written() == before


async def test_create_match_with_no_fields_takes_the_hubs_proposal(world: World) -> None:
    """The client as the same company's last match named it, the letter from the request,
    the card and rebase's own terms: a second match for a known client needs nothing."""
    async with Client(world.server()) as client:
        first = await _create(client, world, cliente=CLIENTE)
        second = await _create(client, world)
    assert second["id"] != first["id"]
    assert second["lettera"]["numero"] != first["lettera"]["numero"]
    assert (
        second["cliente_ragione_sociale"],
        second["cliente_piva"],
        second["cliente_sede"],
    ) == tuple(CLIENTE.values())
    letter = world.document(second["lettera"]["id"]).data
    assert (letter["ruolo"], letter["compenso"]) == ("Backend developer", 450)


async def test_create_match_refuses_a_field_by_the_name_the_tool_takes(world: World) -> None:
    before = world.written()
    async with Client(world.server()) as client:
        unknown = await _refused(
            client,
            "create_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"stipendio": 1},
        )
        emptied = await _refused(
            client,
            "create_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"compenso": None},
        )
        stranger = await _refused(
            client,
            "preview_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente={**CLIENTE, "nome": "ACME"},
        )
    assert "condizioni.stipendio: non è un campo del match" in unknown
    assert "condizioni.compenso: manca" in emptied
    assert "cliente.nome: non è un campo del match" in stranger
    assert world.written() == before


async def test_create_match_with_the_same_match_id_is_written_once(world: World) -> None:
    match_id = str(uuid4())
    async with Client(world.server()) as client:
        first = await _create(client, world, cliente=CLIENTE, match_id=match_id)
        again = await _create(client, world, cliente=CLIENTE, match_id=match_id)
        changed = await _refused(
            client,
            "create_match",
            freelancer_id=world.freelancer_id,
            company_id=world.company_id,
            cliente=CLIENTE,
            condizioni={"ruolo": "Staff engineer"},
            match_id=match_id,
        )
    assert first["id"] == again["id"] == match_id
    assert first["lettera"]["numero"] == again["lettera"]["numero"]
    assert "già stato salvato con dati diversi" in changed
    assert world.count(Match) == 1


# ---- signature ------------------------------------------------------------------------


async def test_send_match_for_signature_answers_the_pages_sentence(world: World) -> None:
    async with Client(world.server()) as client:
        match = await _create(client, world, cliente=CLIENTE)
        report = await _call(client, "send_match_for_signature", match_id=match["id"])
    numero = match["lettera"]["numero"]
    assert report["messaggio"] == (
        f"Partito il contratto quadro: la lettera n. {numero} partirà da sola dopo la sua firma."
    )
    assert (report["inviato"], report["mail_inviata"]) == ("quadro", True)
    assert (report["match"]["stato"], report["match"]["lettera"]["stato"]) == (
        "in_firma",
        "in_attesa",
    )
    assert report["match"]["lettera"]["pdf_url"] == _link(report["match"]["lettera"])
    [mail] = world.sender.sent
    assert mail.subject == "Da firmare: contratto quadro rebase"


async def test_send_over_mcp_audits_the_calling_admin(world: World) -> None:
    """The trail names whoever called the tool, in the same rows the admin API leaves:
    Ivan saves the draft, Grace sends it."""
    async with Client(world.server(IVAN)) as client:
        match = await _create(client, world, cliente=CLIENTE)
    async with Client(world.server(GRACE)) as client:
        await _call(client, "send_match_for_signature", match_id=match["id"])
    session = world.factory()
    try:
        trail = AdminActionService(session).timeline("match", UUID(match["id"]))
        framework = session.scalars(
            select(ContractDocument).where(ContractDocument.kind == "quadro")
        ).one()
    finally:
        session.close()
    assert [(entry.kind, entry.admin_id) for entry in trail] == [
        ("documents_sent", GRACE.id),
        ("match_created", IVAN.id),
    ]
    assert trail[0].payload == {"documento": str(framework.id), "kind": "quadro", "mail": True}
    assert (framework.created_by, framework.sent_by) == (IVAN.id, GRACE.id)


async def test_a_match_goes_from_signature_to_closed_and_its_framework_to_notice(
    world: World,
) -> None:
    async with Client(world.server(GRACE)) as client:
        match = await _create(client, world, cliente=CLIENTE)
        await _call(client, "send_match_for_signature", match_id=match["id"])
        contracts = _payload(
            await client.call_tool("list_matches", {"freelancer_id": world.freelancer_id})
        )
        quadro_id = contracts["quadro"]["id"]

        resent = await _call(client, "resend_signing_mail", document_id=quadro_id)
        assert (resent["stato"], resent["prossima_azione"]) == ("inviato", "reinvia_email")
        assert resent["pdf_url"] == _link(resent)
        assert len(world.sender.sent) == 2

        world.documenso.sign(str(world.document(quadro_id).documenso_id), SIGNED_AT)
        signed = await _call(client, "refresh_contract", document_id=quadro_id)
        assert (signed["stato"], signed["attivo"], signed["ha_pdf_firmato"]) == (
            "firmato",
            True,
            True,
        )
        assert signed["pdf_firmato_url"] == f"{_link(signed)}?firmato=true"
        # The letter that waited for this framework agreement left by itself.
        letter_id = match["lettera"]["id"]
        assert world.document(letter_id).stato == "inviato"

        world.documenso.sign(str(world.document(letter_id).documenso_id), SIGNED_AT)
        letter = await _call(client, "refresh_contract", document_id=letter_id)
        assert letter["stato"] == "firmato"

        closed = await _call(client, "close_match", match_id=match["id"])
        assert closed["stato"] == "concluso"
        assert closed["situazione"].startswith(f"Lettera n. {closed['lettera']['numero']}")
        assert closed["lettera"]["pdf_url"] == _link(closed["lettera"])

        notice = await _call(client, "record_notice", document_id=quadro_id)
        assert (notice["stato"], notice["attivo"]) == ("disdetto", False)
    session = world.factory()
    try:
        match_trail = AdminActionService(session).timeline("match", UUID(match["id"]))
        card_trail = AdminActionService(session).timeline("freelancer", UUID(world.freelancer_id))
    finally:
        session.close()
    assert {(entry.kind, entry.admin_id) for entry in match_trail} >= {
        ("match_created", GRACE.id),
        ("match_closed", GRACE.id),
    }
    assert {(entry.kind, entry.admin_id) for entry in card_trail} >= {
        ("mail_resent", GRACE.id),
        ("notice_recorded", GRACE.id),
    }


async def test_cancel_contract_cancels_a_framework_out_for_signature(world: World) -> None:
    async with Client(world.server(IVAN)) as client:
        match = await _create(client, world, cliente=CLIENTE)
        await _call(client, "send_match_for_signature", match_id=match["id"])
        quadro_id = _payload(
            await client.call_tool("list_matches", {"freelancer_id": world.freelancer_id})
        )["quadro"]["id"]
    async with Client(world.server(GRACE)) as client:
        letter = await _refused(client, "cancel_contract", document_id=match["lettera"]["id"])
        cancelled = await _call(client, "cancel_contract", document_id=quadro_id)
    assert letter == "Una lettera di incarico si annulla con il suo match."
    assert (cancelled["stato"], cancelled["cancel_reason"]) == ("annullato", "Annullato da rebase.")
    assert cancelled["pdf_url"] == _link(cancelled)
    [envelope] = world.documenso.envelopes.values()
    assert envelope.status == "CANCELLED"
    # A framework agreement belongs to no match: its entry lands on the card's trail.
    assert world.trail("freelancer", world.freelancer_id)[0] == ("document_cancelled", GRACE.id)


async def test_cancel_match_cancels_a_draft_and_one_in_signature(world: World) -> None:
    async with Client(world.server(IVAN)) as client:
        draft = await _create(client, world, cliente=CLIENTE)
        sent = await _create(client, world, cliente=CLIENTE)
        await _call(client, "send_match_for_signature", match_id=sent["id"])
    async with Client(world.server(GRACE)) as client:
        cancelled = await _call(client, "cancel_match", match_id=draft["id"])
        stopped = await _call(client, "cancel_match", match_id=sent["id"])
        again = await _refused(client, "cancel_match", match_id=sent["id"])
    assert (cancelled["stato"], cancelled["lettera"]["stato"]) == ("annullato", "annullato")
    assert cancelled["lettera"]["pdf_url"] == _link(cancelled["lettera"])
    assert (stopped["stato"], stopped["lettera"]["stato"]) == ("annullato", "annullato")
    assert "questo è annullato" in again
    assert world.trail("match", draft["id"])[0] == ("match_cancelled", GRACE.id)
    assert world.trail("match", sent["id"])[0] == ("match_cancelled", GRACE.id)


@pytest.mark.parametrize(
    ("breaks", "sentence"),
    [
        (
            lambda documenso: documenso.down("create"),
            "Documenso non risponde: riprova tra qualche minuto.",
        ),
        (
            lambda documenso: documenso.fail("create", 400, "Qualcosa non va"),
            "Documenso ha rifiutato la richiesta: Qualcosa non va",
        ),
    ],
)
async def test_a_documenso_failure_answers_its_sentence_and_sends_nothing(
    world: World, breaks: Callable[[FakeDocumenso], None], sentence: str
) -> None:
    """The service's own Italian sentence, never Documenso's stack trace or ours; the
    send rolls back, so the match is still a draft and nobody got a mail."""
    async with Client(world.server()) as client:
        match = await _create(client, world, cliente=CLIENTE)
        breaks(world.documenso)
        refused = await _refused(client, "send_match_for_signature", match_id=match["id"])
        after = await _call(client, "get_match", match_id=match["id"])
    assert refused == sentence
    assert "Traceback" not in refused and "node_modules" not in refused
    assert (world.sender.sent, world.documenso.envelopes) == ([], {})
    assert (after["stato"], after["lettera"]["stato"], after["quadro"]["stato"]) == (
        "bozza",
        "in_attesa",
        "generato",
    )
    assert [kind for kind, _admin in world.trail("match", match["id"])] == ["match_created"]


# ---- tax data -------------------------------------------------------------------------


async def test_set_freelancer_tax_data_saves_them_and_never_answers_them(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id, company_id = _seed(factory, fiscal=False)
    world = World(
        factory, freelancer_id, company_id, FakeRenderer(), FakeDocumenso(), RecordingSender()
    )
    try:
        async with Client(world.server(GRACE)) as client:
            before = await _call(
                client,
                "preview_match",
                freelancer_id=freelancer_id,
                company_id=company_id,
                cliente=CLIENTE,
            )
            refused = await _refused(
                client,
                "set_freelancer_tax_data",
                freelancer_id=freelancer_id,
                codice_fiscale="XYZ987",
                partita_iva=TAX["partita_iva"],
                domicilio=TAX["domicilio"],
            )
            nul = await _refused(
                client,
                "set_freelancer_tax_data",
                freelancer_id=freelancer_id,
                codice_fiscale="LVLDAA85\x00T50H501Z",
                partita_iva=TAX["partita_iva"],
                domicilio=TAX["domicilio"],
            )
            pec = await _refused(
                client,
                "set_freelancer_tax_data",
                freelancer_id=freelancer_id,
                **TAX | {"pec": "no"},
            )
            saved = await _call(
                client, "set_freelancer_tax_data", freelancer_id=freelancer_id, **TAX
            )
            after = await _call(
                client,
                "preview_match",
                freelancer_id=freelancer_id,
                company_id=company_id,
                cliente=CLIENTE,
            )
        assert before["dati_fiscali_mancanti"] is True
        assert (
            refused == "codice_fiscale: il codice fiscale ha 16 caratteri, o 11 cifre per una ditta"
        )
        assert "XYZ987" not in refused
        # `SafeStr`'s sentence already names the field: said once.
        assert nul == "codice_fiscale: il testo contiene un carattere nullo (\\x00), non ammesso"
        assert pec == "pec: non valido"
        assert saved == {"dati_fiscali": "salvati"}
        assert after["dati_fiscali_mancanti"] is False
        session = factory()
        try:
            row = session.scalars(
                select(FreelancerFiscal).where(
                    FreelancerFiscal.freelancer_id == UUID(freelancer_id)
                )
            ).one()
        finally:
            session.close()
        assert (row.codice_fiscale, row.partita_iva, row.domicilio, row.pec) == tuple(TAX.values())
        assert world.trail("freelancer_fiscal", freelancer_id) == [("fiscal_updated", GRACE.id)]
    finally:
        _wipe(factory)


# ---- a deleted card ---------------------------------------------------------------------


async def test_a_soft_deleted_freelancers_match_and_documents_are_not_found(
    world: World,
) -> None:
    async with Client(world.server()) as client:
        match = await _create(client, world, cliente=CLIENTE)
        letter_id = match["lettera"]["id"]
        await client.call_tool("delete_freelancer", {"freelancer_id": world.freelancer_id})
        for tool in ("send_match_for_signature", "cancel_match", "close_match"):
            text = await _refused(client, tool, match_id=match["id"])
            assert f"match {match['id']} non trovato" in text, tool
        for tool in ("resend_signing_mail", "refresh_contract", "cancel_contract", "record_notice"):
            text = await _refused(client, tool, document_id=letter_id)
            assert f"documento {letter_id} non trovato" in text, tool
        card = f"freelancer {world.freelancer_id} non trovato"
        for tool in ("preview_match", "create_match"):
            text = await _refused(
                client,
                tool,
                freelancer_id=world.freelancer_id,
                company_id=world.company_id,
                cliente=CLIENTE,
            )
            assert card in text, tool
        text = await _refused(
            client, "set_freelancer_tax_data", freelancer_id=world.freelancer_id, **TAX
        )
        assert card in text
    assert world.count(Match) == 1
    assert world.sender.sent == []


# ---- what the tools say about themselves --------------------------------------------------

MATCH_TOOLS = (
    "list_matches",
    "get_match",
    "preview_match",
    "create_match",
    "send_match_for_signature",
    "resend_signing_mail",
    "refresh_contract",
    "cancel_contract",
    "record_notice",
    "cancel_match",
    "close_match",
    "set_freelancer_tax_data",
)


async def test_the_actions_that_cannot_be_taken_back_say_so(world: World) -> None:
    async with Client(world.server()) as client:
        tools = {
            tool.name: " ".join((tool.description or "").split())
            for tool in (await client.list_tools()).tools
        }
    for name in (
        "send_match_for_signature",
        "cancel_contract",
        "cancel_match",
        "record_notice",
        "close_match",
    ):
        assert "Non si torna indietro" in tools[name], name
    assert "incarico finisce" in tools["close_match"]
    assert "area admin" not in tools["list_matches"]


async def test_the_match_reads_say_which_tool_each_next_step_is(world: World) -> None:
    async with Client(world.server()) as client:
        tools = {
            tool.name: " ".join((tool.description or "").split())
            for tool in (await client.list_tools()).tools
        }
    listed = tools["list_matches"]
    for field in ("`situazione`", "`prossima_azione`", "`altre_azioni`", "`lettera.id`"):
        assert field in listed, field
    for action, tool in (
        ("invia", "send_match_for_signature"),
        ("reinvia_email", "resend_signing_mail"),
        ("aggiorna_stato", "refresh_contract"),
        ("annulla", "cancel_match"),
        ("chiudi", "close_match"),
        ("annulla", "cancel_contract"),
        ("registra_disdetta", "record_notice"),
    ):
        assert f"`{action}`" in listed, action
        assert f"`{tool}`" in listed, tool
    assert "`list_matches`" in tools["get_match"]
    assert "`prossima_azione`" in tools["get_match"]


async def test_no_match_tool_speaks_of_the_freelancer_as_he_or_she(world: World) -> None:
    async with Client(world.server()) as client:
        tools = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
    for name in MATCH_TOOLS:
        assert not re.search(r"\b(lui|lei)\b", tools[name]), name
