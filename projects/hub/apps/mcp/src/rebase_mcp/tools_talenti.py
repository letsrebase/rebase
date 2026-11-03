"""The «Talenti» tools: the list an agent reads to rank people (`list_talenti`), the one row
(`get_talento`), and the duplicates (`list_duplicati`, `merge_freelancers`)."""

from typing import Any
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import CallToolResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.duplicates import duplicate_list
from rebase_core.errors import NotFound
from rebase_core.freelancers import FreelancerService
from rebase_core.merge import PersonMergeService
from rebase_core.models import Freelancer, Signup, User
from rebase_core.talenti import TalentiService, signup_read
from rebase_mcp.talenti_view import LIST_LIMIT_DEFAULT as TALENTI_LIMIT_DEFAULT
from rebase_mcp.talenti_view import LIST_LIMIT_MAX as TALENTI_LIMIT_MAX
from rebase_mcp.talenti_view import compact_result, row_fields, talenti_page
from rebase_mcp.tools_common import (
    EMAILS_READ,
    READ_ENTITY,
    Deps,
    bounded,
    moment,
    number,
    read_entity_id,
    search_term,
    skill_terms,
    to_uuid,
)


def _without_addresses(page: dict[str, Any]) -> dict[str, Any]:
    """The duplicates' page for an agent: the pairs by name and id, never an address."""
    for key in ("unibili", "da_rivedere"):
        for pair in page[key]:
            for side in ("survivor", "assorbito"):
                pair[side].pop("email", None)
                pair[side].pop("user_id", None)
    return page


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    def _freelancer_talento(session: Session, freelancer_id: UUID) -> dict[str, Any]:
        """`get_talento`'s answer for a card: `FreelancerDetail`
        (`FreelancerService.get`) with the row `list_talenti` shows for the same person
        laid over it (`TalentiService.row_for_freelancer`, REB-798): the same `origine`
        (how the row was born: `wizard` or `admin`), the card's own fields and
        `ha_scheda_anonima`, which the detail schema does not carry. What the detail
        called `origine`, the marketing page, is `pagina_origine` here, as it is a
        filter of the list."""
        detail = FreelancerService(session, deps.settings, deps.http).get(freelancer_id)
        row = TalentiService(session).row_for_freelancer(freelancer_id)
        shown = detail.model_dump(mode="json")
        return {**shown, **row.model_dump(mode="json"), "pagina_origine": shown["origine"]}

    @mcp.tool()
    def list_talenti(
        limit: int = TALENTI_LIMIT_DEFAULT,
        stato: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        posizione: str | None = None,
        remoto: str | None = None,
        tariffa_min: str | None = None,
        tariffa_max: str | None = None,
        origine: str | None = None,
        pagina_origine: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        con_accessi: bool | None = None,
        creato_da: str | None = None,
        creato_a: str | None = None,
        competenze: list[str] | None = None,
        luogo: str | None = None,
        seniority: list[str] | None = None,
        anni_min: int | None = None,
        anni_max: int | None = None,
        settori: list[str] | None = None,
        lingue: list[str] | None = None,
        scheda: str | None = None,
        ordina: str | None = None,
        campi: list[str] | None = None,
    ) -> CallToolResult:
        """I talenti come li vede la schermata «Talenti» dell'area admin: le schede
        freelance e le iscrizioni senza scheda in un'unica lista, `stato` `lead` per
        le seconde. Ogni riga è corta (`id`, `nome`, `cognome`, `stato` e, dalla scheda
        anonima di Claude, `ruolo`, `seniority`, `anni`, `tariffa_giornaliera`, `luogo`,
        le prime cinque `competenze` e la `sintesi` accorciata; una chiave senza valore
        manca): per valutare chi fa al caso di una richiesta basta questa lista, e una
        pagina di cinquanta righe pesa circa 25 KB. `seniority` e `anni` sono quelli che
        un admin ha corretto (`override_freelancer`) se ci sono; senza `anni` e con
        `seniority_incerta` vera il CV non data nessun ruolo e il livello è solo una
        lettura delle responsabilità. `campi` sceglie i campi per nome e
        dà solo quelli (con `id`), interi: `["email", "settori", "lingue", "fascia"]`,
        con `competenze` e `sintesi` intere se le nomini; `["tutti"]` dà la riga
        completa, con `email`, `linkedin_url`, `origine`, `utm_source`, `created_at`,
        `vetted_at`, `ha_scheda_anonima`, `posizione`. Dal più recente, o dal più
        pertinente quando `q` restringe. L'`id` di una riga va a `get_talento`, che per un
        lead risponde la riga stessa; la scheda di un lead si crea con
        `create_freelancer_from_signup`.

        Filtri, tutti insieme: `q` cerca nome, cognome, email e posizione; `competenze`
        tiene le schede che nominano ogni termine dato (per esempio `["SQL Server",
        "React"]`) nella posizione o nel ruolo, nelle competenze o nella sintesi della
        scheda anonima; `seniority` quelle con uno dei livelli dati (`junior`, `mid`,
        `senior`, `lead`); `anni_min` e `anni_max` per anni di esperienza della scheda
        (estremi compresi); `settori` e `lingue` per i settori e le lingue della scheda
        (ogni termine deve comparire, come `competenze`: `["fintech"]`,
        `["inglese"]`); `luogo` per il luogo della scheda (`"Milano"`); `posizione` per
        il ruolo dichiarato; `remoto` per disponibilità (remoto/ibrido/in_sede);
        `tariffa_min` e `tariffa_max` per tariffa a giornata (stringa, «500», «450.50»
        o all'italiana «1.500», «1.234,50»); `origine` per come è nata la riga (`form`
        iscrizione senza scheda, `wizard` compilata dalla persona, `admin` scritta da un
        admin da una ricerca), `pagina_origine` per la pagina del sito da cui è partita
        (`home`, `pigrocrm`); `utm_source` per campagna; `scheda` per lo stato della
        scheda anonima (`presente`; `fallita` se Claude non è riuscito a scriverla;
        `assente` se non c'è e non ha fallito, per esempio senza CV); `has_cv` per le
        schede con o senza CV; `con_accessi` per chi è entrato almeno una volta nella sua
        area; `creato_da` e `creato_a` per data di creazione (ISO 8601, anche solo
        `AAAA-MM-GG`). Un valore non ammesso per `origine`, `seniority`, `scheda`,
        `ordina` o `campi` è un errore, non una lista vuota. I filtri che una riga nuda
        non ha (posizione, competenze, luogo, seniority, anni, settori, lingue, scheda,
        remoto, tariffa, `pagina_origine`, `origine` `wizard` o `admin`, `has_cv=true`)
        escludono i lead; `origine=form` esclude invece ogni scheda.

        `ordina`: `recenti` (più nuovi prima, anche se `q` cerca), `tariffa_asc`,
        `tariffa_desc`, `anni_asc`, `anni_desc`. Ordinando per tariffa o per anni
        restano solo le schede che quel valore ce l'hanno (niente lead, niente schede
        senza tariffa o senza anni), e `totale` e `per_stato` contano quelle. Senza
        `ordina`: dal più recente, o dal più pertinente quando `q` cerca.

        `limit` è di 50 righe se non lo dai e di 200 al massimo. `per_stato` conta con
        ogni filtro tranne `stato`, `totale` con tutti. `next_cursor` è il cursore opaco
        della pagina successiva, `None` all'ultima: ripassalo in `cursor` insieme agli
        stessi parametri (anche `ordina`: un cursore nato con un altro ordine o con
        un'altra `q` è rifiutato). Solo lettura."""
        term = search_term(q)
        skills = skill_terms(competenze)
        sectors = skill_terms(settori, "settori")
        languages = skill_terms(lingue, "lingue")
        levels = skill_terms(seniority, "seniority")
        place = search_term(luogo)
        for field, value in (
            ("stato", stato),
            ("posizione", posizione),
            ("remoto", remoto),
            ("origine", origine),
            ("pagina_origine", pagina_origine),
            ("utm_source", utm_source),
            ("scheda", scheda),
            ("ordina", ordina),
            ("cursor", cursor),
        ):
            bounded(value, field)
        created_from = moment(creato_da, "creato_da")
        created_to = moment(creato_a, "creato_a")
        rate_from = number(tariffa_min, "tariffa_min")
        rate_to = number(tariffa_max, "tariffa_max")

        def call(session: Session) -> CallToolResult:
            # The fields first: a misspelt name costs no query.
            fields = row_fields(campi)
            page = TalentiService(session).list_recent(
                limit=max(1, min(limit, TALENTI_LIMIT_MAX)),
                stato=stato,
                q=term,
                cursor=cursor,
                posizione=posizione,
                remoto=remoto,
                tariffa_min=rate_from,
                tariffa_max=rate_to,
                origine=origine,
                pagina_origine=pagina_origine,
                utm_source=utm_source,
                has_cv=has_cv,
                con_accessi=con_accessi,
                creato_da=created_from,
                creato_a=created_to,
                competenze=skills,
                luogo=place,
                seniority=levels,
                anni_min=anni_min,
                anni_max=anni_max,
                settori=sectors,
                lingue=languages,
                scheda=scheda,
                ordina=ordina,
            )
            if fields is not None and "email" in fields:
                deps.record_read(
                    session,
                    READ_ENTITY,
                    read_entity_id("list_talenti"),
                    EMAILS_READ,
                    {"tool": "list_talenti", "righe": len(page.items)},
                )
            return compact_result(talenti_page(page.model_dump(mode="json"), fields))

        return deps.call(call)

    @mcp.tool()
    def get_talento(talento_id: str) -> dict[str, Any]:
        """Un talento, per id: la riga che `list_talenti` mostra, letta una per una, con
        tutti i suoi campi (anche `settori`, `lingue`, `sintesi` e `fascia`, la banda di
        prezzo per il cliente). Per una scheda freelance risponde il dettaglio completo
        della schermata admin (REB-284), lo stesso che legge «Dettaglio talento»: i dati
        della persona con lo stato e il thread delle `note`, l'attribuzione
        dell'iscrizione `iscrizione_utm`, gli ultimi accessi e gli ultimi download della
        guida, e lo spazio Pigro quando l'indirizzo ne ha uno. `origine` è quella della
        lista (`wizard` o `admin`); la pagina del sito da cui è partita la persona
        (`home`, `pigrocrm`) è `pagina_origine`. Per un'iscrizione senza scheda risponde
        la riga nuda, `stato` `lead`, da cui la scheda si crea con
        `create_freelancer_from_signup`; un id la cui email ha ormai una scheda risponde
        la scheda, come la lista. Ogni riga con una scheda porta anche `vetted_at`
        (REB-518, `None` se non verificato) e `ha_scheda_anonima`, se Claude ne ha
        scritta una da proporre nel team builder. Solo lettura."""
        key = to_uuid(talento_id, "talento_id")

        def call(session: Session) -> dict[str, Any]:
            if session.get(Freelancer, key) is not None:
                return _freelancer_talento(session, key)
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
                return _freelancer_talento(session, holder[0])
            return signup_read(signup).model_dump(mode="json")

        return deps.call(call)

    @mcp.tool()
    def list_duplicati() -> dict[str, Any]:
        """Chi è in lista due volte (REB-800): `unibili` sono le coppie di schede vive con
        lo stesso profilo LinkedIn, che `merge_freelancers` può unire; `da_rivedere` quelle
        che hanno in comune solo il nome o il telefono, da guardare prima di decidere,
        mai unite da sole; `iscrizioni` quante iscrizioni ripetono un profilo (nessuna
        scheda da unire: restano fuori dalle liste dei lead). Per ogni coppia `survivor` è
        la scheda col CV più recente, quella che tiene, e `assorbito` quella che entra
        nell'altra. Solo lettura, senza indirizzi."""
        return deps.call(lambda s: _without_addresses(duplicate_list(s).model_dump(mode="json")))

    @mcp.tool()
    def merge_freelancers(freelancer_id: str, da_id: str) -> dict[str, Any]:
        """Unisce la scheda `da_id` in `freelancer_id` (REB-800): note, accessi,
        match, contratti, righe di campagna, referral e voci del registro passano alla
        scheda che resta; il CV più recente resta lì, con la sua scheda anonima segnata
        da riscrivere (la riscrive `cards-refresh`, oppure `regenerate_freelancer_card`);
        l'altra scheda non si cancella, si nasconde e punta a questa, e l'indirizzo che
        aveva continua a funzionare per entrare. Risponde quante righe sono passate, per
        tipo, e l'id dell'azione. Rifiutata con una frase quando entrambe hanno un
        contratto quadro in corso o firmato, un incarico attivo con la stessa azienda o
        due codici fiscali diversi, o quando la scheda da assorbire è di un amministratore.
        Si annulla da terminale con `rebase talent-duplicates --undo`."""
        return deps.run(
            lambda s: PersonMergeService(s).merge(
                to_uuid(freelancer_id, "freelancer_id"), to_uuid(da_id, "da_id"), deps.admin().id
            )
        )
