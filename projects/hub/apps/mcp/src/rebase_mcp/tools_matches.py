"""The matches and the contracts as actions (REB-478) and the Pigro hours report (REB-501):
each tool runs the guard the admin API runs, then the service it calls, with the calling
admin as the actor, so the trail reads the same whichever door was used."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import MatchCreate
from rebase_core.contracts.fields import signer_data
from rebase_core.errors import ValidationFailed
from rebase_core.match_rows import ENTITY
from rebase_core.match_words import send_report_sentence
from rebase_core.matches import MatchService, require_live_document, require_live_match
from rebase_core.pigro_door import PigroUnavailable
from rebase_core.regimes import OCCASIONAL, Regime
from rebase_core.signing import SigningService
from rebase_mcp.tools_common import (
    Deps,
    EngagementsFactory,
    day,
    field_refusal,
    not_configured,
    to_uuid,
)

# `MatchDrafts.proposal` names the letter's fields as the admin API's body does
# (`letter.fee`); the tools take them as `terms`, and a refusal says so.
LETTER_PART, CONDITIONS_PART = "letter.", "terms."


def _proposal(
    service: MatchService,
    freelancer_id: UUID,
    company_id: UUID,
    customer: dict[str, Any] | None,
    terms: dict[str, Any] | None,
    match_id: UUID | None = None,
    regime: Regime | None = None,
) -> MatchCreate:
    """The hub's proposal with the given fields laid over it, a refusal naming a letter's
    field by the parameter it came in (`terms.fee`). `regime` `None` takes the
    letter type the hub proposes (REB-687)."""
    try:
        return service.drafts.proposal(freelancer_id, company_id, customer, terms, match_id, regime)
    except ValidationFailed as exc:
        field = str(exc.details["field"])
        if not field.startswith(LETTER_PART):
            raise
        raise ValidationFailed(
            ENTITY, CONDITIONS_PART + field.removeprefix(LETTER_PART), str(exc.details["reason"])
        ) from exc


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""
    hub = urlsplit(deps.settings.hub_url) if deps.settings is not None else None
    # The PDFs are links an admin opens with their own session, never bytes in an
    # agent's context: the API's origin is the SPA's (`REBASE_HUB_URL`), or none at all.
    api_origin = f"{hub.scheme}://{hub.netloc}" if hub is not None and hub.netloc else ""

    def _document_links(document: dict[str, Any]) -> None:
        path = f"/api/hub/contract-documents/{document['id']}/pdf"
        document["pdf_url"] = f"{api_origin}{path}"
        if document.get("has_pdf_signed"):
            document["pdf_signed_url"] = f"{api_origin}{path}?signed=true"

    def _on_match(match_id: str, action: Callable[[Session, UUID], BaseModel]) -> dict[str, Any]:
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            return action(session, key).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body["letter"])
        return body

    def _on_document(
        document_id: str, action: Callable[[SigningService, UUID], BaseModel]
    ) -> dict[str, Any]:
        key = to_uuid(document_id, "document_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_document(session, key)
            return action(deps.contracts(session), key).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body)
        return body

    def _require_engagements() -> EngagementsFactory:
        """The sentence «Consuntivo» shows when this environment has no token for the
        CRM's engagements door: without a token an admin's action is refused plainly,
        unlike the sweep's own quiet wait."""
        if (
            deps.engagements is None
            or deps.settings is None
            or not deps.settings.pigro_engagements_token
        ):
            raise not_configured("Il consuntivo di Pigro", "REBASE_PIGRO_ENGAGEMENTS_TOKEN")
        return deps.engagements

    @mcp.tool()
    def list_matches(freelancer_id: str) -> dict[str, Any]:
        """I match e i contratti di un freelance, per id della scheda, come li mostra la
        pagina «Match e contratti»: `framework` è il contratto quadro (quello attivo,
        altrimenti l'ultimo ancora da inviare o partito per la firma, anche se poi
        annullato o rifiutato; una bozza annullata prima di partire non conta) con
        stato, data di firma, prossimo rinnovo, ultimo giorno per la disdetta e
        versione del testo; `frameworks` li elenca tutti; `occasional_framework` è il contratto
        quadro per prestazioni occasionali, con la stessa regola (un freelance può averne
        uno per tipo); `matches` sono i
        match dal più recente, ognuno con l'azienda, lo stato e la lettera di incarico
        con il suo numero. Ogni documento porta `pdf_url`, il link al PDF da aprire con
        l'accesso admin: mai i byte, mai i dati fiscali. Ogni match e ogni documento
        porta anche `situation`, a che punto è, e `next_action` e `other_actions`, i
        passi che ha davanti. Su un match `send` è `send_match_for_signature`, `cancel`
        è `cancel_match` e `close` è `close_match`, mentre `resend_email` e
        `status_refresh` agiscono sulla sua lettera (`letter.id`) con
        `resend_signing_mail` e `refresh_contract`; su un contratto quadro gli stessi due
        strumenti prendono il suo id, `cancel` è `cancel_contract` e `record_notice`
        è `record_notice`. Solo lettura; un match nuovo si prepara con `preview_match` e
        si salva con `create_match`."""
        body = deps.run(
            lambda s: deps.reading(s).for_freelancer(to_uuid(freelancer_id, "freelancer_id"))
        )
        body.pop("tax", None)
        for document in (body["framework"], body["occasional_framework"], *body["frameworks"]):
            if document is not None:
                _document_links(document)
        for match in body["matches"]:
            _document_links(match["letter"])
        return body

    @mcp.tool()
    def get_match(match_id: str) -> dict[str, Any]:
        """Un match, per id: l'azienda e la figura richiesta, i dati del cliente come li
        stampa la lettera, lo stato (draft, awaiting_signature, active, concluded, cancelled), la
        lettera di incarico con numero, stato e `label` («Lettera di incarico» o «Lettera
        di incarico occasionale»), e in `framework` il contratto quadro del freelance dello
        stesso tipo della lettera. Ogni documento porta `pdf_url`, mai i byte. Il match e
        i documenti portano `situation`, `next_action` e `other_actions`, e quale strumento fa
        ogni azione lo dice `list_matches`. Solo lettura."""
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            # A soft-deleted freelancer's match reads as «match not found», the answer
            # the admin API gives (REB-417), rather than `for_freelancer` refusing with
            # the freelancer's own message.
            freelancer_id = require_live_match(session, key).freelancer_id
            service = deps.reading(session)
            body = service.get(key).model_dump(mode="json")
            page = service.for_freelancer(freelancer_id)
            # The framework agreement of the letter's own regime (REB-687).
            framework = (
                page.occasional_framework
                if body["letter"]["regime"] == OCCASIONAL
                else page.framework
            )
            body["framework"] = framework.model_dump(mode="json") if framework is not None else None
            return body

        body = deps.call(call)
        _document_links(body["letter"])
        if body["framework"] is not None:
            _document_links(body["framework"])
        return body

    @mcp.tool()
    def preview_match(
        freelancer_id: str,
        company_id: str,
        customer: dict[str, Any] | None = None,
        terms: dict[str, Any] | None = None,
        regime: Regime | None = None,
    ) -> dict[str, Any]:
        """Cosa farebbe `create_match` con questi dati, senza scrivere nulla: le frasi del
        passo 3 di «Crea match». `recap` è la lettera di incarico in tre o quattro
        frasi, `what_happens` quale documento parte per primo, `required_framework` se
        serve un nuovo contratto quadro, `missing_tax_data` se mancano i dati
        fiscali del freelance (si salvano con `set_freelancer_tax_data`), `regime` il tipo
        di lettera. `regime` è `vat_registered` (lettera di incarico, per chi lavora con
        partita IVA) o `occasional` (lettera di incarico occasionale, prestazione
        occasionale); omesso, quello che propone l'hub: occasionale se i dati fiscali sono
        salvati senza partita IVA. Una lettera con partita IVA senza partita IVA salvata è
        rifiutata. Si parte dalla proposta dell'hub: il cliente come l'ha chiamato l'ultimo
        match della stessa azienda (altrimenti solo il nome dell'azienda), la lettera dalla
        richiesta, dalla scheda e dalle condizioni di rebase. `customer` e `terms` la
        cambiano campo per campo, con i nomi dell'API: per il cliente
        `customer_legal_name`, `customer_vat_number`, `customer_registered_office`; per le
        condizioni i campi
        della lettera, come `role`, `activities`, `start_date` e `end_date` (AAAA-MM-GG),
        `commitment`, `location`, `fee` (IVA esclusa nella lettera con partita IVA, lordo in
        quella occasionale; anche all'italiana: «1.500», «1.234,50»), `unit`,
        `payment_days`, `month_end`. Un campo omesso resta come lo propone l'hub,
        `null` lo svuota. Risponde anche `customer` e `terms` come verrebbero scritti.
        Un campo sconosciuto, o obbligatorio e rimasto vuoto, è rifiutato con il suo nome.
        Solo lettura."""
        freelancer, company = (
            to_uuid(freelancer_id, "freelancer_id"),
            to_uuid(company_id, "company_id"),
        )

        def call(session: Session) -> dict[str, Any]:
            service = MatchService(session)
            proposal = _proposal(service, freelancer, company, customer, terms, regime=regime)
            body = service.drafts.check(freelancer, proposal).model_dump(mode="json")
            body["customer"] = proposal.customer.model_dump(mode="json")
            body["terms"] = proposal.letter.model_dump(mode="json")
            return body

        return deps.call(call)

    @mcp.tool()
    def create_match(
        freelancer_id: str,
        company_id: str,
        customer: dict[str, Any] | None = None,
        terms: dict[str, Any] | None = None,
        match_id: str | None = None,
        expected_days: int | None = None,
        regime: Regime | None = None,
    ) -> dict[str, Any]:
        """Salva il match come bozza, come «Salva senza inviare» al passo 3 di «Crea
        match»: la lettera di incarico con il suo numero e, se il freelance non ha un
        contratto quadro attivo né uno già in firma dello stesso tipo della lettera, un
        contratto quadro nuovo di quel tipo. Non parte nulla: per la firma c'è
        `send_match_for_signature`. `customer` e `terms` come in `preview_match`, e
        così `regime`, da chiamare prima per rileggere le frasi. Servono i dati
        fiscali del freelance (`set_freelancer_tax_data`). `expected_days`, i giorni
        attesi per il consuntivo su Pigro (fra 1 e 366, facoltativo), sta sul match e non
        sulla lettera: il suo testo resta quello di «Impegno». `match_id`, un UUID
        scelto da chi chiama, rende sicuro riprovare: con lo stesso id torna il match già
        scritto e non un secondo, ma solo se la proposta è rimasta identica. Se nel
        frattempo la proposta dell'hub è cambiata (la richiesta, la scheda o l'ultimo
        match dell'azienda), il tentativo è rifiutato: per riprovare passa esplicitamente
        in `customer` e `terms` i valori della prima volta. Risponde il match con
        `situation`, `next_action` e la lettera con `pdf_url`, a nome dell'admin
        dietro il token."""
        freelancer, company = (
            to_uuid(freelancer_id, "freelancer_id"),
            to_uuid(company_id, "company_id"),
        )
        key = to_uuid(match_id, "match_id") if match_id else None

        def call(session: Session) -> dict[str, Any]:
            service = MatchService(session, deps.renderer, signer_data(deps.signer_json))
            proposal = _proposal(service, freelancer, company, customer, terms, key, regime)
            try:
                proposal = MatchCreate.model_validate(
                    {**proposal.model_dump(mode="json"), "expected_days": expected_days}
                )
            except PydanticValidationError as exc:
                raise field_refusal(exc) from None
            return service.create(freelancer, proposal, deps.admin().id).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body["letter"])
        return body

    @mcp.tool()
    def send_match_for_signature(match_id: str) -> dict[str, Any]:
        """«Invia per la firma»: il documento che può partire ora va su Documenso e il
        freelance riceve la mail con il link per firmarlo. Se non ha un contratto quadro
        attivo parte prima quello, e la lettera di incarico parte da sola dopo la sua
        firma. `message` dice cosa è partito, con le parole della pagina; `sent` è
        `framework`, `letter` o `null` (la lettera aspetta un contratto quadro già in
        firma), `mail_sent` se la mail è partita (altrimenti `resend_signing_mail`).
        Non si torna indietro: il documento arriva al freelance, e da lì si può solo
        annullare. Rifiutato per un testo ancora in bozza o un match che non ha più nulla
        da inviare."""
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            report = deps.contracts(session).send_match(key, deps.admin().id)
            return {**report.model_dump(mode="json"), "message": send_report_sentence(report)}

        body = deps.call(call)
        _document_links(body["match"]["letter"])
        return body

    @mcp.tool()
    def resend_signing_mail(document_id: str) -> dict[str, Any]:
        """«Reinvia email»: di nuovo la mail con il link per firmare, lo stesso di prima,
        per un documento che aspetta ancora la firma del freelance. L'id del documento è
        in `list_matches` o `get_match`. Risponde il documento con `pdf_url`."""
        return _on_document(
            document_id, lambda service, key: service.admin.resend_mail(key, deps.admin().id)
        )

    @mcp.tool()
    def refresh_contract(document_id: str) -> dict[str, Any]:
        """«Aggiorna stato»: chiede a Documenso a che punto è il documento e lo applica
        come farebbe la sua notifica, poi fa quello che una firma lascia da fare (salva e
        manda la copia firmata, fa partire le lettere che aspettavano questo contratto
        quadro). Serve quando una notifica non è arrivata. Risponde il documento con
        `situation`, `pdf_url` e, quando c'è la copia firmata, `pdf_signed_url`."""
        return _on_document(document_id, lambda service, key: service.admin.refresh(key))

    @mcp.tool()
    def cancel_contract(document_id: str) -> dict[str, Any]:
        """«Annulla» su un contratto quadro non ancora firmato: se era partito è annullato
        anche su Documenso, e il freelance riceve una mail che il link non vale più. Una
        lettera di incarico si annulla con il suo match (`cancel_match`). Non si torna
        indietro: le lettere che lo aspettavano restano in attesa, e «Invia per la firma»
        sul loro match ne scrive uno nuovo."""
        return _on_document(
            document_id, lambda service, key: service.admin.cancel_document(key, deps.admin().id)
        )

    @mcp.tool()
    def record_notice(document_id: str) -> dict[str, Any]:
        """«Registra disdetta» su un contratto quadro attivo, per una disdetta o un recesso:
        da adesso il freelance non ha un contratto quadro attivo, e il suo prossimo match
        ne scrive uno nuovo. Non si torna indietro."""
        return _on_document(
            document_id, lambda service, key: service.admin.record_notice(key, deps.admin().id)
        )

    @mcp.tool()
    def cancel_match(match_id: str) -> dict[str, Any]:
        """«Annulla» su un match in bozza o in firma: il match e la sua lettera di
        incarico diventano annullati, e il numero della lettera non si riusa. Una lettera
        già partita è annullata anche su Documenso: il freelance riceve una mail e il suo
        link smette di funzionare. Il contratto quadro resta com'è. Non si torna
        indietro."""
        return _on_match(
            match_id,
            lambda session, key: deps.contracts(session).admin.cancel_match(key, deps.admin().id),
        )

    @mcp.tool()
    def close_match(match_id: str) -> dict[str, Any]:
        """«Chiudi match» su un match attivo: l'incarico finisce e il match diventa
        concluso. Il contratto quadro resta com'è. Non si torna indietro."""
        return _on_match(
            match_id, lambda session, key: MatchService(session).close(key, deps.admin().id)
        )

    @mcp.tool()
    def link_match_to_pigro(match_id: str) -> dict[str, Any]:
        """«Riprova su Pigro»: collega ora un match attivo al suo deal su PigroCRM, a
        nome dell'admin dietro il token, o registra perché non ci è riuscito (`error`,
        `rejected` con la frase del CRM) così che si possa riprovare. Rifiutato per un
        match non attivo o non trovato. Risponde un errore anche quando Pigro non è
        configurato su questo ambiente o non risponde."""
        build_engagements = _require_engagements()
        try:
            return _on_match(
                match_id, lambda session, key: build_engagements(session).link(key, deps.admin().id)
            )
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_match_report(
        match_id: str, from_date: str | None = None, to_date: str | None = None
    ) -> dict[str, Any]:
        """Il consuntivo delle ore di un match, come «Consuntivo» nell'area admin: ore e
        giorni totali, l'avanzamento sui giorni previsti quando ce ne sono, il dettaglio
        per giorno con le fatture di ciascuno e le ore su ognuna, i totali per settimana
        e per mese, e le fatture del periodo. `from_date` e `to_date` (AAAA-MM-GG) restringono il
        periodo, l'intero incarico per difetto (dall'inizio della lettera a oggi).
        Rifiutato per un match non collegato a Pigro, con la frase di dove sta il
        collegamento. Risponde un errore anche quando Pigro non è configurato su questo
        ambiente o non risponde. Solo lettura, con un'eccezione: un deal eliminato nello
        spazio del freelance segna il match come rifiutato, finché il deal non torna e
        «Riprova» lo ricollega."""
        build_engagements = _require_engagements()
        key = to_uuid(match_id, "match_id")
        start = day(from_date, "from_date")
        end = day(to_date, "to_date")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            return build_engagements(session).report(key, start, end).model_dump(mode="json")

        try:
            return deps.call(call)
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc
