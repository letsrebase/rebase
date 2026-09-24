"""`build_server`: the tools, over a session factory a test can replace.

An admin's tool, by credential since REB-213: every transport resolves a personal token
(`rebase_core.admin_tokens`) to the admin behind it before a tool runs, and hands
`build_server` a callable that answers who that is. Over stdio it is one admin for the
life of the process; over HTTP (`rebase_mcp.http`) it is whoever signed the request,
bound for the duration of the call. Everything a tool can do is what the hub's admin API
does behind its login; the admin's name is what a comment or a drafted card is signed
with, so the thread says who.

One session per tool call, closed whatever happened: the SDK dispatches sync tools on
a thread pool, and a session shared across calls is the defect PigroCRM's MCP server
measured as zero rows written under concurrency.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.context import ServerMiddleware
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.comments import CommentService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.errors import DomainError, NotFound
from rebase_core.freelancers import LEAD_STATE, FreelancerService
from rebase_core.http import HttpCall
from rebase_core.logins import LoginService
from rebase_core.matches import MatchService
from rebase_core.models import Freelancer, Signup, User
from rebase_core.perks import PerkService
from rebase_core.pigro import PigroRegistry, PigroUnavailable
from rebase_core.schemas import (
    CompanyOverride,
    FreelancerDraft,
    FreelancerOverride,
    FreelancerRead,
    StatusChange,
    TalentoRead,
)
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.service import LIST_LIMIT_DEFAULT
from rebase_core.talenti import TalentiService

SessionFactory = sessionmaker[Session]
# Who is calling: resolved by the transport, read by the tools that sign something.
AdminProvider = Callable[[], AdminRead]

INSTRUCTIONS = (
    "rebase, la community di freelance di letsrebase.com. Gli strumenti leggono chi "
    "ha chiesto di entrare (iscrizioni), i freelance che hanno compilato il profilo con il "
    "CV e le aziende che cercano persone; possono cambiare lo stato di una candidatura, "
    "annotarla e lasciare un commento datato nel suo thread; da un'iscrizione possono "
    "creare la scheda freelance con quanto si trova in pubblico su quella persona, che poi "
    "lei completa dalla sua area. Sono dati di altre persone: da usare solo per decidere "
    "quando e cosa scrivere loro, mai da riportare altrove."
)

PIGRO_NOT_CONFIGURED = "Il registro di Pigro non è configurato: manca REBASE_PIGRO_REGISTRY_TOKEN."


def _number(value: str | None, field: str) -> Decimal | None:
    """A decimal passed as a string, the way `create_freelancer_from_signup` already
    takes it: JSON numbers lose the two-decimal exactness a tariffa or a budget needs."""
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        raise ToolError(f"{field}: «{value}» non è un numero") from None


def _integer(value: str | None, field: str) -> int | None:
    """A whole number passed as a string, the same convention `_number` gives a
    decimal one: `numero_risorse` and `giorni_presenza` (REB-380)."""
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» non è un numero intero") from None


def _day(value: str | None, field: str) -> date | None:
    """An ISO 8601 calendar date (`AAAA-MM-GG`), parsed here so the refusal is an
    Italian sentence and not a traceback from the service."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» non è una data ISO 8601 (AAAA-MM-GG)") from None


def _moment(value: str | None, field: str) -> datetime | None:
    """An ISO 8601 timestamp (a plain date is accepted too, at midnight)."""
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» non è una data e ora ISO 8601") from None


def _search_term(value: str | None) -> str | None:
    """`q` bounded the way the API's `SearchQ` bounds it: an unbounded term reaching
    `ILIKE` and `similarity()` on every row is a denial of service with extra steps."""
    if value is not None and len(value) > SEARCH_MAX_LENGTH:
        raise ToolError(f"la ricerca deve stare in {SEARCH_MAX_LENGTH} caratteri")
    return value


def _supplied_text(raw: str) -> str | None:
    """A tool parameter's own contract for `override_freelancer`/`override_company`:
    the caller already checked `is not None` before calling this, so an empty string
    is the one thing left it can mean -- clear the field, the same convention
    `set_freelancer_status`'s own `note` gives a status change. A nullable column
    accepts it; `rebase_core.audit.reject_cleared_columns` refuses it on one that
    is not, naming the field rather than leaving it silently ignored."""
    return raw or None


def build_server(
    factory: SessionFactory,
    admin: AdminProvider,
    *,
    settings: Settings | None = None,
    http: HttpCall | None = None,
    middleware: Sequence[ServerMiddleware[Any]] | None = None,
) -> MCPServer:
    """`admin` answers the admin behind the current call; `settings` and `http` are what
    reaches the CRM, for `list_pigro_spaces` and for the `pigro_slug` of `get_talento`,
    and without them the registry's tools answer the same sentence the admin area shows
    when it is not configured while the slug stays `None`. `middleware`
    is the HTTP transport's way of binding the request's admin around each call."""
    mcp = MCPServer("rebase", instructions=INSTRUCTIONS, middleware=middleware)

    @mcp.tool()
    def create_freelancer_from_signup(
        signup_id: str,
        nome: str,
        cognome: str,
        fonti: list[str],
        linkedin_url: str | None = None,
        posizione: str | None = None,
        tariffa_giornaliera: str | None = None,
        remoto: str | None = None,
        links: list[str] | None = None,
        autore: str | None = None,
    ) -> dict[str, Any]:
        """Crea (o riscrive) la scheda freelance di un'iscrizione con quanto si trova in
        pubblico su quella persona: nome e cognome obbligatori, poi il profilo LinkedIn,
        la posizione come la dichiara lei, altri link (sito, GitHub, portfolio). Tariffa e
        modalità di lavoro solo se una fonte pubblica le dice, altrimenti restano vuote
        con il CV: la persona le completa dalla sua area. `fonti` sono gli indirizzi https
        da cui vengono le informazioni, da una a dieci, e finiscono nel thread della
        scheda firmate da `autore` (l'admin dietro il token, se non dici chi scrive). Una
        scheda che la persona ha già compilato non si tocca: il tool rifiuta."""
        draft = FreelancerDraft(
            nome=nome,
            cognome=cognome,
            linkedin_url=linkedin_url,
            posizione=posizione,
            tariffa_giornaliera=(
                Decimal(tariffa_giornaliera) if tariffa_giornaliera is not None else None
            ),
            remoto=remoto,  # type: ignore[arg-type]
            links=links or [],
            fonti=fonti,
        )
        # `draft_from_signup` returns through `get()`, which now answers a
        # `FreelancerDetail` for the admin HTTP route (REB-284); this tool keeps the
        # plain `FreelancerRead` shape it always had. The card was just drafted, so it
        # has no logins, downloads or signup UTM to report yet, and the thread comes
        # back on the write anyway. `get_talento` is the tool for the full detail.
        return _run(
            lambda s: FreelancerRead.model_validate(
                FreelancerService(s)
                .draft_from_signup(UUID(signup_id), draft, autore or admin().nome)
                .model_dump()
            )
        )

    @mcp.tool()
    def list_talenti(
        limit: int = LIST_LIMIT_DEFAULT,
        stato: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        posizione: str | None = None,
        remoto: str | None = None,
        tariffa_min: str | None = None,
        tariffa_max: str | None = None,
        origine: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        con_accessi: bool | None = None,
        creato_da: str | None = None,
        creato_a: str | None = None,
    ) -> dict[str, Any]:
        """I talenti come li vede la schermata «Talenti» dell'area admin: le schede
        freelance e le iscrizioni senza scheda in un'unica lista, `stato` `lead` per
        le seconde, ognuna con id, nome, cognome, email, LinkedIn, stato, origine e
        data. Dal più recente, o dal più pertinente quando `q` restringe. L'`id` di
        una riga va a `get_talento`, che per un lead risponde la riga stessa; la
        scheda di un lead si crea con `create_freelancer_from_signup`.
        `q` cerca nome, cognome, email e posizione; `posizione` filtra per ruolo,
        `remoto` per disponibilità (remoto/ibrido/in_sede), `tariffa_min` e
        `tariffa_max` per tariffa a giornata (stringa decimale col punto, «500» o
        «450.50»); `origine` per canale della riga (form/wizard/admin), `utm_source` per
        campagna, `has_cv` per le schede con o senza CV, `con_accessi` per chi è
        entrato almeno una volta nella sua area, `creato_da` e `creato_a` per data di
        creazione (ISO 8601, anche solo `AAAA-MM-GG`). I filtri che una riga nuda non
        ha (posizione, remoto, tariffa, origine, `has_cv=true`) escludono i lead.
        `per_stato` conta con ogni filtro tranne `stato`, `totale` con tutti.
        `next_cursor` è il cursore opaco della pagina successiva, `None` all'ultima:
        ripassalo in `cursor` insieme agli stessi parametri, un cursore nato con
        un'altra `q` è rifiutato. Solo lettura."""
        term = _search_term(q)
        created_from = _moment(creato_da, "creato_da")
        created_to = _moment(creato_a, "creato_a")
        rate_from = _number(tariffa_min, "tariffa_min")
        rate_to = _number(tariffa_max, "tariffa_max")
        return _run(
            lambda s: TalentiService(s).list_recent(
                limit=limit,
                stato=stato,
                q=term,
                cursor=cursor,
                posizione=posizione,
                remoto=remoto,
                tariffa_min=rate_from,
                tariffa_max=rate_to,
                origine=origine,
                utm_source=utm_source,
                has_cv=has_cv,
                con_accessi=con_accessi,
                creato_da=created_from,
                creato_a=created_to,
            )
        )

    @mcp.tool()
    def get_talento(talento_id: str) -> dict[str, Any]:
        """Un talento, per id: la riga che `list_talenti` mostra, letta una per una.
        Per una scheda freelance risponde il dettaglio completo della schermata admin
        (REB-284), lo stesso che legge «Dettaglio talento»: i dati della persona con
        lo stato, le note e il thread dei `commenti`, l'attribuzione dell'iscrizione
        `iscrizione_utm`, gli ultimi accessi e gli ultimi download della guida, e lo
        spazio Pigro quando l'indirizzo ne ha uno. Per un'iscrizione senza scheda
        risponde la riga nuda, `stato` `lead`, da cui la scheda si crea con
        `create_freelancer_from_signup`; un id la cui email ha ormai una scheda
        risponde la scheda, come la lista. Solo lettura."""
        key = UUID(talento_id)

        def call(session: Session) -> BaseModel:
            if session.get(Freelancer, key) is not None:
                return FreelancerService(session, settings, http).get(key)
            signup = session.get(Signup, key)
            if signup is None:
                raise NotFound("talento", key)
            # A sign-up whose address gained a card since the list was read is the
            # card's person: answer the card, which is what «Talenti» shows for them.
            holder = session.execute(
                select(Freelancer.id)
                .join(User, User.id == Freelancer.user_id)
                .where(func.lower(User.email) == signup.email.lower())
            ).first()
            if holder is not None:
                return FreelancerService(session, settings, http).get(holder[0])
            return TalentoRead(
                id=signup.id,
                nome=signup.nome,
                cognome=signup.cognome,
                email=signup.email,
                linkedin_url=signup.linkedin_url,
                stato=LEAD_STATE,
                origine="form",
                utm_source=signup.utm_source,
                created_at=signup.created_at,
            )

        return _run(call)

    @mcp.tool()
    def get_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Un freelance, per id, con `commenti`: il thread di chi lo ha seguito, dal più
        recente, ognuno con autore e data."""
        return _run(
            lambda s: FreelancerRead.model_validate(
                FreelancerService(s).get(UUID(freelancer_id)).model_dump()
            )
        )

    @mcp.tool()
    def read_freelancer_cv(freelancer_id: str) -> dict[str, Any]:
        """Il testo del CV di un freelance, per id, come lo legge pypdf: `testo` pagina
        dopo pagina, `pagine` quante ne ha il file, `troncato` se il testo è stato
        tagliato (oltre venti pagine o duecentomila caratteri), `filename` il nome del
        file. Un CV scansionato risponde `testo` vuoto con le sue pagine; una scheda
        senza CV risponde una frase. Solo lettura: serve a confrontare una scheda con
        una richiesta senza uscire dall'MCP, non a riportare il CV altrove."""
        return _run(lambda s: FreelancerService(s).cv_text(UUID(freelancer_id)))

    @mcp.tool()
    def set_freelancer_status(
        freelancer_id: str, stato: str, note: str | None = None
    ) -> dict[str, Any]:
        """Sposta una candidatura fra nuovo, contattato, attivo e scartato, con una nota
        opzionale per chi la rileggerà. Non tocca quello che la persona ha scritto."""
        return _run(
            lambda s: FreelancerService(s).set_status(
                UUID(freelancer_id), StatusChange(stato=stato, note=note)
            )
        )

    @mcp.tool()
    def add_freelancer_comment(
        freelancer_id: str, testo: str, autore: str | None = None
    ) -> dict[str, Any]:
        """Aggiunge un commento al thread di un freelance, senza toccare stato e note: una
        telefonata fatta, un'impressione, una cosa da ricordare. Resta com'è scritto, con
        data e autore; non si modifica e non si cancella. Fino a 4000 caratteri, anche su
        più righe. `autore` è l'admin dietro il token, se non dici chi sta scrivendo."""
        return _run(
            lambda s: CommentService(s).add(
                "freelancer", UUID(freelancer_id), testo, autore or admin().nome
            )
        )

    @mcp.tool()
    def override_freelancer(
        freelancer_id: str,
        nome: str | None = None,
        cognome: str | None = None,
        linkedin_url: str | None = None,
        tariffa_giornaliera: str | None = None,
        posizione: str | None = None,
        remoto: str | None = None,
        links: list[str] | None = None,
        stato: str | None = None,
        note: str | None = None,
        compilata_da: str | None = None,
    ) -> dict[str, Any]:
        """Scrive o svuota qualsiasi campo del profilo oltre a stato e note: nome,
        cognome e profilo LinkedIn (sull'identità condivisa in `users`), tariffa,
        posizione, modalità di lavoro, link, stato, note e chi ha compilato per ultimo
        (persona/admin). Ogni parametro omesso resta com'era; una stringa vuota svuota
        il campo dove è ammesso (tariffa, posizione, LinkedIn, note, modalità di
        lavoro), altrove è rifiutata perché il campo non può restare senza un valore.
        `links` sostituisce l'intera lista, `[]` la svuota. Ogni modifica reale finisce
        nel registro di `get_freelancer_audit`, con il valore prima e dopo; per
        annullarla c'è `revert_freelancer_action`."""
        kwargs: dict[str, Any] = {}
        if nome is not None:
            kwargs["nome"] = _supplied_text(nome)
        if cognome is not None:
            kwargs["cognome"] = _supplied_text(cognome)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = _supplied_text(linkedin_url)
        if tariffa_giornaliera is not None:
            kwargs["tariffa_giornaliera"] = (
                None
                if tariffa_giornaliera == ""
                else _number(tariffa_giornaliera, "tariffa_giornaliera")
            )
        if posizione is not None:
            kwargs["posizione"] = _supplied_text(posizione)
        if remoto is not None:
            kwargs["remoto"] = _supplied_text(remoto)
        if links is not None:
            kwargs["links"] = links
        if stato is not None:
            kwargs["stato"] = _supplied_text(stato)
        if note is not None:
            kwargs["note"] = _supplied_text(note)
        if compilata_da is not None:
            kwargs["compilata_da"] = _supplied_text(compilata_da)
        override = FreelancerOverride(**kwargs)
        return _run(
            lambda s: FreelancerService(s).override(UUID(freelancer_id), override, admin().id)
        )

    @mcp.tool()
    def delete_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Cancella la scheda: esce da Talenti e dall'elenco freelance. Reversibile,
        mai una cancellazione vera: `restore_freelancer` la fa tornare com'era."""
        return _run(lambda s: FreelancerService(s).soft_delete(UUID(freelancer_id), admin().id))

    @mcp.tool()
    def restore_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Ripristina una scheda cancellata: torna in Talenti e nell'elenco freelance.
        Su una scheda già attiva non cambia nulla."""
        return _run(lambda s: FreelancerService(s).restore(UUID(freelancer_id), admin().id))

    @mcp.tool()
    def clear_freelancer_cv(freelancer_id: str) -> dict[str, Any]:
        """Rimuove il CV caricato: il file non entra nel registro delle modifiche, solo
        nome, tipo e dimensione di quello che c'era. Non reversibile da qui: per
        rimetterlo serve che la persona lo carichi di nuovo dalla sua area."""
        return _run(lambda s: FreelancerService(s).clear_cv(UUID(freelancer_id), admin().id))

    @mcp.tool()
    def get_freelancer_audit(freelancer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Chi ha modificato, svuotato, cancellato o ripristinato questa scheda, e
        quando: ogni voce con l'amministratore, il tipo di azione e, per una modifica di
        campo, il valore prima e dopo. Funziona anche su una scheda cancellata. Dalla più
        recente. Solo lettura; per annullare una modifica di campo usa
        `revert_freelancer_action` con l'id della voce."""
        return _run_list(lambda s: FreelancerService(s).audit_timeline(UUID(freelancer_id), limit))

    @mcp.tool()
    def revert_freelancer_action(freelancer_id: str, action_id: str) -> dict[str, Any]:
        """Riporta un campo al valore che aveva prima di una modifica passata, letta
        dalla voce del registro `action_id` (da `get_freelancer_audit`). Solo su una
        voce che è una modifica di campo, non su una cancellazione o un ripristino."""
        return _run(
            lambda s: FreelancerService(s).revert(UUID(freelancer_id), UUID(action_id), admin().id)
        )

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
        per quanto, budget a giornata, stato e note. Dal più recente, o dal più
        pertinente quando `q` restringe.
        `q` cerca nome azienda, referente, email e progetto; `stato` filtra (nuovo,
        contattato, in_corso, chiuso) e `per_stato` conta con ogni filtro tranne
        `stato`; `budget_min` e `budget_max` sul budget a giornata (stringa decimale
        col punto, «500» o «450.50»), `periodo_da` sull'inizio del progetto (ISO
        8601), `origine` sulla pagina da cui è arrivata la richiesta, `creato_da` e
        `creato_a` sulla data di creazione (ISO 8601, anche solo `AAAA-MM-GG`).
        `next_cursor` è il cursore opaco della pagina successiva, `None` all'ultima:
        ripassalo in `cursor` insieme agli stessi parametri, un cursore nato con
        un'altra `q` è rifiutato. Solo lettura."""
        term = _search_term(q)
        budget_from = _number(budget_min, "budget_min")
        budget_to = _number(budget_max, "budget_max")
        period_from = _day(periodo_da, "periodo_da")
        created_from = _moment(creato_da, "creato_da")
        created_to = _moment(creato_a, "creato_a")
        return _run(
            lambda s: CompanyService(s).list_recent(
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
        )

    @mcp.tool()
    def get_company(company_id: str) -> dict[str, Any]:
        """Una richiesta di un'azienda, per id, con `commenti`: il thread di chi l'ha
        seguita, dal più recente, ognuno con autore e data."""
        return _run(lambda s: CompanyService(s).get(UUID(company_id)))

    @mcp.tool()
    def set_company_status(company_id: str, stato: str, note: str | None = None) -> dict[str, Any]:
        """Sposta una richiesta fra nuovo, contattato, in_corso e chiuso, con una nota."""
        return _run(
            lambda s: CompanyService(s).set_status(
                UUID(company_id), StatusChange(stato=stato, note=note)
            )
        )

    @mcp.tool()
    def add_company_comment(
        company_id: str, testo: str, autore: str | None = None
    ) -> dict[str, Any]:
        """Aggiunge un commento al thread di una richiesta di un'azienda, senza toccare
        stato e note: come è andata la call, cosa hanno chiesto, cosa resta da fare. Resta
        com'è scritto, con data e autore; non si modifica e non si cancella. Fino a 4000
        caratteri, anche su più righe. `autore` è l'admin dietro il token, se non dici
        chi scrive."""
        return _run(
            lambda s: CommentService(s).add(
                "company", UUID(company_id), testo, autore or admin().nome
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
        note: str | None = None,
    ) -> dict[str, Any]:
        """Scrive o svuota qualsiasi campo della richiesta oltre a stato e note: nome e
        cognome del referente e il suo profilo LinkedIn (sull'identità condivisa in
        `users`), nome dell'azienda, figura richiesta, progetto, data di inizio, durata,
        budget a giornata, modalità di lavoro, giorni in sede a settimana, numero di
        persone richieste, stato e note. Ogni parametro omesso resta com'era; una
        stringa vuota svuota solo dove è ammesso (LinkedIn, note, giorni in sede),
        altrove è rifiutata perché il campo non può restare senza un valore.
        `giorni_presenza` va indicato solo, e sempre, quando `remoto` è `ibrido` --
        rifiutato altrimenti, dallo stesso `CHECK` del database. Ogni modifica reale
        finisce nel registro di `get_company_audit`, con il valore prima e dopo; per
        annullarla c'è `revert_company_action`."""
        kwargs: dict[str, Any] = {}
        if nome is not None:
            kwargs["nome"] = _supplied_text(nome)
        if cognome is not None:
            kwargs["cognome"] = _supplied_text(cognome)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = _supplied_text(linkedin_url)
        if nome_azienda is not None:
            kwargs["nome_azienda"] = _supplied_text(nome_azienda)
        if figura_richiesta is not None:
            kwargs["figura_richiesta"] = _supplied_text(figura_richiesta)
        if progetto is not None:
            kwargs["progetto"] = _supplied_text(progetto)
        if periodo_da is not None:
            kwargs["periodo_da"] = None if periodo_da == "" else _day(periodo_da, "periodo_da")
        if durata is not None:
            kwargs["durata"] = _supplied_text(durata)
        if budget_giornaliero is not None:
            kwargs["budget_giornaliero"] = (
                None
                if budget_giornaliero == ""
                else _number(budget_giornaliero, "budget_giornaliero")
            )
        if remoto is not None:
            kwargs["remoto"] = _supplied_text(remoto)
        if giorni_presenza is not None:
            kwargs["giorni_presenza"] = _integer(giorni_presenza, "giorni_presenza")
        if numero_risorse is not None:
            kwargs["numero_risorse"] = _integer(numero_risorse, "numero_risorse")
        if stato is not None:
            kwargs["stato"] = _supplied_text(stato)
        if note is not None:
            kwargs["note"] = _supplied_text(note)
        override = CompanyOverride(**kwargs)
        return _run(lambda s: CompanyService(s).override(UUID(company_id), override, admin().id))

    @mcp.tool()
    def delete_company(company_id: str) -> dict[str, Any]:
        """Cancella la richiesta: esce dall'elenco aziende. Reversibile, mai una
        cancellazione vera: `restore_company` la fa tornare com'era."""
        return _run(lambda s: CompanyService(s).soft_delete(UUID(company_id), admin().id))

    @mcp.tool()
    def restore_company(company_id: str) -> dict[str, Any]:
        """Ripristina una richiesta cancellata: torna nell'elenco aziende. Su una
        richiesta già attiva non cambia nulla."""
        return _run(lambda s: CompanyService(s).restore(UUID(company_id), admin().id))

    @mcp.tool()
    def get_company_audit(company_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Chi ha modificato, cancellato o ripristinato questa richiesta, e quando:
        ogni voce con l'amministratore, il tipo di azione e, per una modifica di campo,
        il valore prima e dopo. Funziona anche su una richiesta cancellata. Dalla più
        recente. Solo lettura; per annullare una modifica di campo usa
        `revert_company_action` con l'id della voce."""
        return _run_list(lambda s: CompanyService(s).audit_timeline(UUID(company_id), limit))

    @mcp.tool()
    def revert_company_action(company_id: str, action_id: str) -> dict[str, Any]:
        """Riporta un campo al valore che aveva prima di una modifica passata, letta
        dalla voce del registro `action_id` (da `get_company_audit`). Solo su una voce
        che è una modifica di campo, non su una cancellazione o un ripristino."""
        return _run(
            lambda s: CompanyService(s).revert(UUID(company_id), UUID(action_id), admin().id)
        )

    @mcp.tool()
    def list_pigro_spaces() -> dict[str, Any]:
        """Gli spazi di PigroCRM come li mostra «Istanze Pigro» nell'area admin: slug,
        email di chi lo ha aperto, quando, l'indirizzo dello spazio e il membro dell'hub
        dietro quell'email quando ne ha una scheda. Letti dall'API del CRM con il token
        di registro, mai dal suo database; `totale` li conta. Solo lettura."""
        if settings is None or http is None or not settings.pigro_registry_token:
            raise ToolError(PIGRO_NOT_CONFIGURED)
        registry = PigroRegistry(settings, http)
        session = factory()
        try:
            return registry.list_spaces(session).model_dump(mode="json")
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc
        finally:
            session.close()

    @mcp.tool()
    def guide_stats() -> dict[str, Any]:
        """Quanti hanno scaricato la guida ai primi passi da freelance: `totale` i
        download, `membri` le persone diverse dietro, `membri_totali` quante potevano,
        `ultimi_7_giorni` i download dell'ultima settimana e `recenti` gli ultimi con
        nome ed email. Solo lettura."""
        return _run(lambda s: PerkService(s).guide_stats())

    @mcp.tool()
    def login_stats() -> dict[str, Any]:
        """Chi è entrato nella sua area e quando: `totale` gli accessi con il link via
        email, `membri` le persone diverse dietro, `membri_totali` quante hanno una
        scheda, `ultimi_7_giorni` gli accessi dell'ultima settimana e `recenti` gli
        ultimi con nome ed email. Ogni scheda letta da `get_talento` e da `get_freelancer`
        porta anche `accessi` e `ultimo_accesso`."""
        return _run(lambda s: LoginService(s).stats())

    hub = urlsplit(settings.hub_url) if settings is not None else None
    # The PDFs are links an admin opens with their own session, never bytes in an
    # agent's context: the API's origin is the SPA's (`REBASE_HUB_URL`), or none at all.
    api_origin = f"{hub.scheme}://{hub.netloc}" if hub is not None and hub.netloc else ""

    def _document_links(document: dict[str, Any]) -> None:
        path = f"/api/hub/contract-documents/{document['id']}/pdf"
        document["pdf_url"] = f"{api_origin}{path}"
        if document.get("ha_pdf_firmato"):
            document["pdf_firmato_url"] = f"{api_origin}{path}?firmato=true"

    @mcp.tool()
    def list_matches(freelancer_id: str) -> dict[str, Any]:
        """I match e i contratti di un freelance, per id della scheda, come li mostra la
        pagina «Match e contratti»: `quadro` è il contratto quadro (quello attivo, o
        l'ultimo non annullato) con stato, data di firma, prossimo rinnovo, ultimo giorno
        per la disdetta e versione del testo; `quadri` li elenca tutti; `matches` sono i
        match dal più recente, ognuno con l'azienda, lo stato e la lettera di incarico
        con il suo numero. Ogni documento porta `pdf_url`, il link al PDF da aprire con
        l'accesso admin: mai i byte, mai i dati fiscali. Solo lettura: i match si creano
        dall'area admin."""
        body = _run(lambda s: MatchService(s).for_freelancer(UUID(freelancer_id)))
        body.pop("fiscale", None)
        for document in (body["quadro"], *body["quadri"]):
            if document is not None:
                _document_links(document)
        for match in body["matches"]:
            _document_links(match["lettera"])
        return body

    @mcp.tool()
    def get_match(match_id: str) -> dict[str, Any]:
        """Un match, per id: l'azienda e la figura richiesta, i dati del cliente come li
        stampa la lettera, lo stato (bozza, in_firma, attivo, concluso, annullato), la
        lettera di incarico con numero e stato, e in `quadro` il contratto quadro del
        freelance. Ogni documento porta `pdf_url`, mai i byte. Solo lettura."""
        session = factory()
        try:
            service = MatchService(session)
            match = service.get(UUID(match_id))
            quadro = service.for_freelancer(match.freelancer_id).quadro
            body = match.model_dump(mode="json")
            body["quadro"] = quadro.model_dump(mode="json") if quadro is not None else None
        except DomainError as exc:
            raise ToolError(exc.message) from exc
        finally:
            session.close()
        _document_links(body["lettera"])
        if body["quadro"] is not None:
            _document_links(body["quadro"])
        return body

    def _run(call: Callable[[Session], BaseModel]) -> dict[str, Any]:
        """One session per call, closed whatever happened, and a domain error rendered
        as its own sentence rather than a stack trace."""
        session = factory()
        try:
            return call(session).model_dump(mode="json")
        except DomainError as exc:
            raise ToolError(exc.message) from exc
        finally:
            session.close()

    def _run_list(call: Callable[[Session], Sequence[BaseModel]]) -> list[dict[str, Any]]:
        """`_run`'s own shape for a tool that answers several rows, like
        `get_freelancer_audit`."""
        session = factory()
        try:
            return [item.model_dump(mode="json") for item in call(session)]
        except DomainError as exc:
            raise ToolError(exc.message) from exc
        finally:
            session.close()

    return mcp
