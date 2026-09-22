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
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.context import ServerMiddleware
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.comments import CommentService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.errors import DomainError
from rebase_core.freelancers import FreelancerService
from rebase_core.http import HttpCall
from rebase_core.logins import LoginService
from rebase_core.perks import PerkService
from rebase_core.pigro import PigroRegistry, PigroUnavailable
from rebase_core.schemas import FreelancerDraft, FreelancerRead, StatusChange
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.service import LIST_LIMIT_DEFAULT, SignupService
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


def build_server(
    factory: SessionFactory,
    admin: AdminProvider,
    *,
    settings: Settings | None = None,
    http: HttpCall | None = None,
    middleware: Sequence[ServerMiddleware[Any]] | None = None,
) -> MCPServer:
    """`admin` answers the admin behind the current call; `settings` and `http` are what
    `list_pigro_spaces` needs to reach the CRM, and without them the tool answers the
    same sentence the admin area shows when the registry is not configured. `middleware`
    is the HTTP transport's way of binding the request's admin around each call."""
    mcp = MCPServer("rebase", instructions=INSTRUCTIONS, middleware=middleware)

    @mcp.tool()
    def list_signups(limit: int = LIST_LIMIT_DEFAULT) -> dict[str, Any]:
        """Chi ha lasciato nome, cognome ed email su letsrebase.com per entrare nella
        community rebase, dal più recente, con il profilo LinkedIn quando l'ha dato.
        Solo lettura. `nome` e `cognome` sono vuoti solo per le iscrizioni raccolte
        quando il form chiedeva la sola email. `totale` conta tutta la lista anche
        quando `limit` ne restituisce una parte."""
        return _run(lambda s: SignupService(s).list_recent(limit=limit))

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
        # plain `FreelancerRead` shape it always had, since it has neither the
        # `settings`/`http` the Pigro lookup needs nor a documented reason to grow
        # the sign-up/login/download fields the admin's screen alone asked for.
        return _run(
            lambda s: FreelancerRead.model_validate(
                FreelancerService(s)
                .draft_from_signup(UUID(signup_id), draft, autore or admin().nome)
                .model_dump()
            )
        )

    @mcp.tool()
    def list_freelancers(
        limit: int = LIST_LIMIT_DEFAULT, stato: str | None = None
    ) -> dict[str, Any]:
        """I freelance che hanno compilato il profilo sull'hub, dal più recente: nome,
        email, posizione, tariffa a giornata, disponibilità (remoto/ibrido/in_sede), link,
        stato della candidatura (nuovo, contattato, attivo, scartato) e note. Mai i byte
        del CV: il testo lo legge `read_freelancer_cv`, il file si scarica dall'area
        admin. `stato` filtra; `totale` conta tutto. `lead` sono le iscrizioni senza
        scheda (nome se c'è, email, quando), `totale_lead` quante:
        piene senza filtro o con `stato="lead"`, vuote con un altro stato."""
        return _run(lambda s: FreelancerService(s).list_recent(limit=limit, stato=stato))

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
        una scheda va a `get_freelancer`, quello di un lead a
        `create_freelancer_from_signup`.
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
    def list_companies(
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
        ultimi con nome ed email. Ogni scheda in `list_freelancers` e `get_freelancer`
        porta anche `accessi` e `ultimo_accesso`."""
        return _run(lambda s: LoginService(s).stats())

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

    return mcp
