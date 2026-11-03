"""The team requests and the talent cloud (REB-520, spec § 8): read the requests and the
proposals, correct a summary, write to the talents the admin asks to, open and close the
cloud."""

import logging
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.cloud import TalentCloudService
from rebase_core.team_availability import TeamAvailabilityService
from rebase_core.team_builder import (
    LIST_LIMIT_DEFAULT as TEAM_PROPOSAL_LIST_LIMIT_DEFAULT,
)
from rebase_core.team_builder import TeamBuilder
from rebase_core.team_requests import (
    LIST_LIMIT_DEFAULT as TEAM_REQUEST_LIST_LIMIT_DEFAULT,
)
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    TeamRequestNote,
    TeamRequestRead,
    TeamRequestSummary,
)
from rebase_mcp.tools_common import (
    Deps,
    field_refusal,
    to_uuid,
)

logger = logging.getLogger(__name__)


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_team_requests(
        stato: str | None = None,
        origine: str | None = None,
        limit: int = TEAM_REQUEST_LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """«Richieste team» (spec § 3.5), dalla più recente: azienda, origine, stato,
        quando è arrivata, e quanti talenti sono stati chiesti e quanti hanno già
        risposto sì. `stato` e `origine` filtrano sulle parole che quei campi usano; una
        parola che non è una di quelle è rifiutata nominando il campo. `next_cursor` è
        il cursore opaco della pagina successiva, `None` all'ultima. Solo lettura."""
        return deps.run(
            lambda s: TeamRequestService(s, settings=deps.team_settings()).list_recent(
                stato=stato, origine=origine, limit=limit, cursor=cursor
            )
        )

    @mcp.tool()
    def list_team_proposals(
        origine: str | None = None,
        esito: str | None = None,
        limit: int = TEAM_PROPOSAL_LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """«Proposte»: ogni «Proponi il team» chiesto dalla pagina pubblica o dal talent
        cloud (e, nelle righe più vecchie, da un admin), dalla più recente, anche quando
        nessuna richiesta è poi stata inviata: la descrizione del progetto, quante
        persone sono state chieste, la nota di un «Rigenera», chi l'ha chiesto, il
        riassunto, quanti membri aveva la proposta e chi erano (`team`: posizione,
        `freelancer_id`, ruolo proposto, nome e cognome, vuoti per un talento nel
        frattempo eliminato), e `request_id` della richiesta «Assumi team» se c'è.
        `errore` è vuoto su una
        proposta che ha risposto e il codice del rifiuto (`llm_unavailable`,
        `team_builder_busy`) su un tentativo senza proposta. `origine` (`pubblico`,
        `cloud`, `admin`) ed `esito` (`ok`, `errore`) filtrano; una parola che non è
        una di quelle è rifiutata nominando il campo. `next_cursor` è il cursore opaco
        della pagina successiva, `None` all'ultima. Solo lettura."""
        return deps.run(
            lambda s: TeamBuilder(s, None, deps.team_settings()).list_recent(
                origine=origine, esito=esito, limit=limit, cursor=cursor
            )
        )

    @mcp.tool()
    def get_team_request(request_id: str) -> dict[str, Any]:
        """Una richiesta team, per id (spec § 3.5): la proposta con i talenti per nome
        e la loro tariffa e fascia, il riassunto e il luogo che i talenti leggeranno, i
        contatti dell'azienda, lo stato, la nota dell'admin, e la risposta di ciascun
        talento con il suo orario. Solo lettura."""
        key = to_uuid(request_id, "request_id")
        return deps.run(lambda s: TeamRequestService(s, settings=deps.team_settings()).get(key))

    @mcp.tool()
    def set_team_request_summary(request_id: str, riassunto: str) -> dict[str, Any]:
        """«Salva il riassunto» (spec § 3.5): il testo che i talenti leggeranno nella
        mail di disponibilità, riscritto qui dall'admin prima di scrivere loro.
        Rifiutato con la stessa frase di `contact_team_talents` se nomina ancora
        l'azienda: il riassunto va corretto dove è scritto."""
        key = to_uuid(request_id, "request_id")
        change = TeamRequestSummary(riassunto=riassunto)
        return deps.run(
            lambda s: TeamRequestService(s, settings=deps.team_settings()).set_summary(
                key, change.riassunto, deps.admin().id
            )
        )

    @mcp.tool()
    def contact_team_talents(request_id: str, only_silent: bool = False) -> dict[str, Any]:
        """«Contatta i talenti» (`only_silent` falso, la prima volta) o «Rimanda a chi
        non ha risposto» (vero, spec § 3.6), per una richiesta team arrivata dal sito o
        dal talent cloud: una mail a ciascun talento del team con il riassunto, il ruolo
        proposto e la propria tariffa, e due link, «Sono disponibile» e «Non sono
        disponibile», buoni per trenta giorni. Scrive davvero alle persone: da usare
        solo quando lo chiede l'admin, mai come passo di una ricerca. Rifiutato, con la
        sua frase, per una richiesta chiusa, un riassunto che nomina ancora l'azienda,
        una seconda prima volta, o nessun talento rimasto da scrivere; senza una chiave
        di invio configurata risponde che l'invio non è attivo qui."""
        key = to_uuid(request_id, "request_id")
        return deps.run(
            lambda s: TeamAvailabilityService(
                s, settings=deps.team_settings(), sender=deps.sender
            ).contact_talents(key, deps.admin().id, only_silent=only_silent)
        )

    @mcp.tool()
    def set_team_request_status(
        request_id: str, stato: str, note: str | None = None
    ) -> dict[str, Any]:
        """«Segna come contattata», «Chiudi» (spec § 3.5), con una nota facoltativa per
        chi la rileggerà; `note` omesso lascia la nota com'era. Una nota rifiutata
        (oltre 4000 caratteri, o con un carattere nullo) lascia anche lo stato com'era."""
        key = to_uuid(request_id, "request_id")
        # The note is checked before anything is written: refused after `set_status`,
        # it would leave the state moved and the call answering an error.
        try:
            cleaned = TeamRequestNote(note=note) if note is not None else None
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None

        def call(session: Session) -> TeamRequestRead:
            service = TeamRequestService(session, settings=deps.team_settings())
            read = service.set_status(key, stato, deps.admin().id)
            if cleaned is not None:
                read = service.set_note(key, cleaned.note, deps.admin().id)
            return read

        return deps.run(call)

    @mcp.tool()
    def grant_talent_cloud(company_id: str) -> dict[str, Any]:
        """«Apri il talent cloud» (spec § 4.1): apre alla referente di questa richiesta
        l'accesso al cloud privato, e le manda «Il talent cloud di rebase è aperto per
        …» quando è una concessione nuova; su una già viva per questa azienda e questa
        persona risponde quella, senza mandare altro. `mail_inviata` dice se la mail è
        partita; senza una chiave di invio configurata il cloud si apre comunque e
        nessuno la riceve."""

        def call(session: Session) -> dict[str, Any]:
            read, mail = TalentCloudService(session, deps.team_settings()).grant(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
            mail_inviata = False
            if mail is not None:
                if deps.sender is None:
                    logger.info("talent cloud grant %s: no mail sender, not mailed", read.id)
                else:
                    mail_inviata = deps.sender.send(mail)
                    if not mail_inviata:
                        logger.warning(
                            "talent cloud grant %s: the mail was refused by the provider",
                            read.id,
                        )
            return {**read.model_dump(mode="json"), "mail_inviata": mail_inviata}

        return deps.call(call)

    @mcp.tool()
    def revoke_talent_cloud(company_id: str) -> dict[str, Any]:
        """«Revoca il talent cloud» (spec § 4.1): chiude la concessione viva di questa
        richiesta, e solo questa; un'altra azienda della stessa persona resta com'era e
        il suo cloud resta aperto. Rifiutato, con la sua frase, quando non c'è nulla di
        vivo da chiudere."""
        return deps.run(
            lambda s: TalentCloudService(s).revoke(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def list_talent_cloud_grants() -> list[dict[str, Any]]:
        """Ogni concessione del talent cloud, viva o chiusa, dalla più recente, fino a
        200 (spec § 4.1, non paginate perché sono poche per un pezzo): per ciascuna,
        chi l'ha aperta e quando, e chi l'ha eventualmente chiusa e quando. Solo
        lettura."""
        return deps.run_list(lambda s: TalentCloudService(s).list())
