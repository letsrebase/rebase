"""The «Aziende» tools: the companies' requests, their thread, status and trail, and the CRM
link (`create_company`, `link_company_to_pigro`)."""

import re
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyAdminCreate, CompanyOverride
from rebase_core.notes import NoteService
from rebase_core.service import LIST_LIMIT_DEFAULT
from rebase_mcp.tools_common import (
    EMAILS_READ,
    READ_ENTITY,
    SOURCE,
    Deps,
    bounded,
    day,
    field_refusal,
    integer,
    moment,
    not_configured,
    number,
    read_entity_id,
    search_term,
    supplied_text,
    to_uuid,
)

_DEAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _deal_id(text: str | None) -> str | None:
    """The CRM deal's id out of what an admin gives: the id, or the deal's address."""
    if text is None or not text.strip():
        return None
    found = _DEAL_UUID.search(text)
    if found is None:
        raise ToolError("deal_id: non è l'id di un deal né il suo indirizzo")
    return found.group(0).lower()


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_aziende(
        limit: int = LIST_LIMIT_DEFAULT,
        stato: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        budget_min: str | None = None,
        budget_max: str | None = None,
        periodo_da: str | None = None,
        origine: str | None = None,
        creato_da: str | None = None,
        creato_a: str | None = None,
    ) -> dict[str, Any]:
        """Le aziende che hanno descritto un progetto sull'hub, come la schermata
        «Aziende»: ogni riga porta azienda, referente, email, progetto, da quando e
        per quanto, budget a giornata e stato. Dal più recente, o dal più
        pertinente quando `q` restringe.
        `q` cerca nome azienda, referente, email e progetto; `stato` filtra (nuovo,
        contattato, in_corso, chiuso) e `per_stato` conta con ogni filtro tranne
        `stato`; `budget_min` e `budget_max` sul budget a giornata (stringa, «500»,
        «450.50» o all'italiana «1.500», «1.234,50»), `periodo_da` sull'inizio del progetto (ISO
        8601), `origine` sulla pagina da cui è arrivata la richiesta, `creato_da` e
        `creato_a` sulla data di creazione (ISO 8601, anche solo `AAAA-MM-GG`).
        `next_cursor` è il cursore opaco della pagina successiva, `None` all'ultima:
        ripassalo in `cursor` insieme agli stessi parametri, un cursore nato con
        un'altra `q` è rifiutato. Ogni riga porta lo stato del collegamento al CRM
        (`crm_stato`, `crm_situazione`, `crm_deal_url`). Solo lettura."""
        term = search_term(q)
        bounded(stato, "stato")
        bounded(origine, "origine")
        bounded(cursor, "cursor")
        budget_from = number(budget_min, "budget_min")
        budget_to = number(budget_max, "budget_max")
        period_from = day(periodo_da, "periodo_da")
        created_from = moment(creato_da, "creato_da")
        created_to = moment(creato_a, "creato_a")

        def call(session: Session) -> dict[str, Any]:
            page = CompanyService(session).list_recent(
                limit=limit,
                stato=stato,
                q=term,
                cursor=cursor,
                budget_min=budget_from,
                budget_max=budget_to,
                periodo_da=period_from,
                origine=origine,
                creato_da=created_from,
                creato_a=created_to,
            )
            body = page.model_dump(mode="json")
            # Every row carries the referente's address: a list of them is a read of
            # personal data an agent made, and the trail says so (REB-803).
            deps.record_read(
                session,
                READ_ENTITY,
                read_entity_id("list_aziende"),
                EMAILS_READ,
                {"tool": "list_aziende", "righe": len(page.items)},
            )
            return body

        return deps.call(call)

    @mcp.tool()
    def get_company(company_id: str) -> dict[str, Any]:
        """Una richiesta di un'azienda, per id, con `note`: il thread di chi l'ha
        seguita, dal più recente, ognuna con `id`, autore, data, `fonte` (`web`, `mcp`,
        `persona`, `sistema`), l'ultima modifica e `modificabile`. Le note cancellate non
        ci sono. Porta anche dove sta il collegamento al CRM di rebase: `crm_stato`
        (`da_collegare`, `collegato`, `errore`, `rifiutato`; vuoto per una richiesta nata
        prima del collegamento), `crm_situazione` con la frase del CRM quando non è
        collegata, `crm_deal_url` con l'indirizzo del deal e `crm_attempted_at`."""
        return deps.run(lambda s: CompanyService(s).get(to_uuid(company_id, "company_id")))

    @mcp.tool()
    def set_company_status(company_id: str, stato: str) -> dict[str, Any]:
        """Sposta una richiesta fra nuovo, contattato, in_corso e chiuso. Il cambio finisce
        nel registro di `get_company_audit` (`status_changed`, con prima e dopo): per il
        perché usa `add_company_note`."""
        return deps.run(
            lambda s: CompanyService(s).set_status(
                to_uuid(company_id, "company_id"),
                StatusChange(stato=stato),
                admin_id=deps.admin().id,
                fonte=SOURCE,
            )
        )

    @mcp.tool()
    def add_company_note(company_id: str, testo: str) -> dict[str, Any]:
        """Aggiunge una nota al thread di una richiesta di un'azienda, senza toccare lo
        stato: come è andata la call, cosa hanno chiesto, cosa resta da fare. La nota è già
        firmata (l'admin dietro il token) e datata, con `fonte` `mcp`: il testo non deve
        iniziare con una data o un'ora né nominare chi scrive. Fino a 4000 caratteri, anche
        su più righe. Si corregge con `update_note` e si cancella con `delete_note`, e ogni
        aggiunta finisce nel registro di `get_company_audit`."""
        return deps.run(
            lambda s: NoteService(s).add(
                "company",
                to_uuid(company_id, "company_id"),
                testo,
                autore_id=deps.admin().id,
                fonte=SOURCE,
            )
        )

    @mcp.tool()
    def override_company(
        company_id: str,
        nome: str | None = None,
        cognome: str | None = None,
        linkedin_url: str | None = None,
        nome_azienda: str | None = None,
        figura_richiesta: str | None = None,
        progetto: str | None = None,
        periodo_da: str | None = None,
        durata: str | None = None,
        budget_giornaliero: str | None = None,
        remoto: str | None = None,
        giorni_presenza: str | None = None,
        numero_risorse: str | None = None,
        stato: str | None = None,
    ) -> dict[str, Any]:
        """Scrive o svuota qualsiasi campo della richiesta: nome e
        cognome del referente e il suo profilo LinkedIn (sull'identità condivisa in
        `users`), nome dell'azienda, figura richiesta, progetto, data di inizio, durata,
        budget a giornata, modalità di lavoro, giorni in sede a settimana, numero di
        persone richieste e stato. Le note non sono un campo: stanno nel thread
        (`add_company_note`, `update_note`). Ogni parametro omesso resta com'era; una
        stringa vuota svuota solo dove è ammesso (LinkedIn, giorni in sede),
        altrove è rifiutata perché il campo non può restare senza un valore.
        `giorni_presenza` va indicato solo, e sempre, quando `remoto` è `ibrido` --
        rifiutato altrimenti, dallo stesso `CHECK` del database. Ogni modifica reale
        finisce nel registro di `get_company_audit`, con il valore prima e dopo; per
        annullarla c'è `revert_company_action`."""
        kwargs: dict[str, Any] = {}
        if nome is not None:
            kwargs["nome"] = supplied_text(nome)
        if cognome is not None:
            kwargs["cognome"] = supplied_text(cognome)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = supplied_text(linkedin_url)
        if nome_azienda is not None:
            kwargs["nome_azienda"] = supplied_text(nome_azienda)
        if figura_richiesta is not None:
            kwargs["figura_richiesta"] = supplied_text(figura_richiesta)
        if progetto is not None:
            kwargs["progetto"] = supplied_text(progetto)
        if periodo_da is not None:
            kwargs["periodo_da"] = None if periodo_da == "" else day(periodo_da, "periodo_da")
        if durata is not None:
            kwargs["durata"] = supplied_text(durata)
        if budget_giornaliero is not None:
            kwargs["budget_giornaliero"] = (
                None
                if budget_giornaliero == ""
                else number(budget_giornaliero, "budget_giornaliero")
            )
        if remoto is not None:
            kwargs["remoto"] = supplied_text(remoto)
        if giorni_presenza is not None:
            kwargs["giorni_presenza"] = integer(giorni_presenza, "giorni_presenza")
        if numero_risorse is not None:
            kwargs["numero_risorse"] = integer(numero_risorse, "numero_risorse")
        if stato is not None:
            kwargs["stato"] = supplied_text(stato)
        override = CompanyOverride(**kwargs)
        return deps.run(
            lambda s: CompanyService(s).override(
                to_uuid(company_id, "company_id"), override, deps.admin().id
            )
        )

    @mcp.tool()
    def delete_company(company_id: str) -> dict[str, Any]:
        """Cancella la richiesta: esce dall'elenco aziende. Reversibile, mai una
        cancellazione vera: `restore_company` la fa tornare com'era."""
        return deps.run(
            lambda s: CompanyService(s).soft_delete(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def restore_company(company_id: str) -> dict[str, Any]:
        """Ripristina una richiesta cancellata: torna nell'elenco aziende. Su una
        richiesta già attiva non cambia nulla."""
        return deps.run(
            lambda s: CompanyService(s).restore(to_uuid(company_id, "company_id"), deps.admin().id)
        )

    @mcp.tool()
    def get_company_audit(company_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Chi ha modificato, cancellato o ripristinato questa richiesta, e quando:
        ogni voce con l'amministratore, il tipo di azione e, per una modifica di campo,
        il valore prima e dopo. Funziona anche su una richiesta cancellata. Dalla più
        recente. Solo lettura; per annullare una modifica di campo usa
        `revert_company_action` con l'id della voce."""
        return deps.run_list(
            lambda s: CompanyService(s).audit_timeline(to_uuid(company_id, "company_id"), limit)
        )

    @mcp.tool()
    def revert_company_action(company_id: str, action_id: str) -> dict[str, Any]:
        """Riporta un campo al valore che aveva prima di una modifica passata, letta
        dalla voce del registro `action_id` (da `get_company_audit`). Solo su una voce
        che è una modifica di campo, non su una cancellazione o un ripristino."""
        return deps.run(
            lambda s: CompanyService(s).revert(
                to_uuid(company_id, "company_id"), to_uuid(action_id, "action_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def create_company(
        nome_azienda: str,
        referente_nome: str,
        referente_cognome: str,
        email: str,
        telefono: str,
        figura_richiesta: str,
        progetto: str,
        periodo_da: str,
        durata: str,
        budget_giornaliero: str,
        remoto: str,
        numero_risorse: int = 1,
        giorni_presenza: int | None = None,
        deal_id: str | None = None,
    ) -> dict[str, Any]:
        """Apre la richiesta di un'azienda a nome del referente, per un'azienda incontrata
        direttamente: gli stessi campi del wizard e gli stessi controlli (`periodo_da` in
        AAAA-MM-GG, `budget_giornaliero` in euro al giorno, `remoto` uno fra `remoto`,
        `ibrido`, `in_sede`; `giorni_presenza` solo, e sempre, per l'ibrido). Il referente si
        trova o si crea dall'email. Non parte nessuna mail, né al referente né a rebase. Senza
        `deal_id` è una richiesta come le altre: il CRM la registra come cliente e deal nuovi.
        Con `deal_id` (l'id del deal che il CRM ha già, o il suo indirizzo `.../app/deal/<id>`)
        il CRM collega quel deal e non crea nulla: né un secondo cliente né un secondo deal.
        Risponde la richiesta com'è, con `crm_stato` (`collegato`, o `rifiutato`/`errore` con la
        frase del CRM in `crm_situazione`, per esempio per un deal che il CRM non conosce) e
        `crm_deal_url`. Quando il CRM non è configurato su questo ambiente la richiesta si apre
        comunque e resta `da_collegare`."""
        fields: dict[str, Any] = {
            "nome_azienda": nome_azienda,
            "referente_nome": referente_nome,
            "referente_cognome": referente_cognome,
            "email": email,
            "telefono": telefono,
            "figura_richiesta": figura_richiesta,
            "progetto": progetto,
            "periodo_da": periodo_da,
            "durata": durata,
            "budget_giornaliero": budget_giornaliero,
            "remoto": remoto,
            "numero_risorse": numero_risorse,
            "giorni_presenza": giorni_presenza,
            "deal_id": _deal_id(deal_id),
        }
        try:
            data = CompanyAdminCreate.model_validate(fields)
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None

        crm_on = (
            deps.crm_leads is not None
            and deps.settings is not None
            and bool(deps.settings.pigro_engagements_token)
        )
        if crm_on and data.deal_id is None:
            # A new deal gets the model's draft: counted before the request is written.
            deps.spend_model_call()

        def call(session: Session) -> dict[str, Any]:
            service = CompanyService(session)
            created = service.create_by_admin(data, deps.admin().id, fonte=SOURCE)
            if crm_on and deps.crm_leads is not None:
                deps.crm_leads(session).link(created.id, deps.admin().id)
            return service.get(created.id).model_dump(mode="json")

        return deps.call(call)

    @mcp.tool()
    def link_company_to_pigro(company_id: str, deal_id: str | None = None) -> dict[str, Any]:
        """«Riprova» sulla richiesta di un'azienda: la registra ora nel CRM di rebase (cliente,
        contatto e deal con la bozza AI), a nome dell'admin dietro il token, e risponde la
        richiesta com'è: `crm_stato` (`collegato`, o `errore`/`rifiutato` con la frase del
        CRM in `crm_situazione`) e `crm_deal_url`. Registra anche una richiesta nata prima del
        collegamento. Con `deal_id` (l'id di un deal che il CRM ha già) è «Collega a un altro
        deal»: il CRM collega quel deal e non crea nulla, e un rifiuto lascia il collegamento
        com'era. Rifiutato per una richiesta non trovata o cancellata e quando il CRM non è
        configurato su questo ambiente."""
        if (
            deps.crm_leads is None
            or deps.settings is None
            or not deps.settings.pigro_engagements_token
        ):
            raise not_configured("Il CRM", "REBASE_PIGRO_ENGAGEMENTS_TOKEN")
        build = deps.crm_leads
        key = to_uuid(company_id, "company_id")
        deal = to_uuid(deal_id, "deal_id") if deal_id is not None else None
        if deal is None:
            # Filing a request with no deal named asks the model for a draft.
            deps.spend_model_call()

        def call(session: Session) -> dict[str, Any]:
            service = CompanyService(session)
            service.get(key)
            build(session).link(key, deps.admin().id, deal)
            return service.get(key).model_dump(mode="json")

        return deps.call(call)
