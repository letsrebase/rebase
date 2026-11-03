"""The «Talenti» tools: the list an agent reads to rank people (`list_talents`), the one row
(`get_talent`), and the duplicates (`list_duplicates`, `merge_freelancers`)."""

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
from rebase_core.talents import TalentsService, signup_read
from rebase_mcp.talents_view import LIST_LIMIT_DEFAULT as TALENTS_LIMIT_DEFAULT
from rebase_mcp.talents_view import LIST_LIMIT_MAX as TALENTS_LIMIT_MAX
from rebase_mcp.talents_view import compact_result, row_fields, talents_page
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
    for key in ("mergeable", "from_review"):
        for pair in page[key]:
            for side in ("survivor", "absorbed"):
                pair[side].pop("email", None)
                pair[side].pop("user_id", None)
    return page


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    def _freelancer_talent(session: Session, freelancer_id: UUID) -> dict[str, Any]:
        """`get_talent`'s answer for a card: `FreelancerDetail`
        (`FreelancerService.get`) with the row `list_talents` shows for the same person
        laid over it (`TalentsService.row_for_freelancer`, REB-798): the same `origin`
        (how the row was born: `wizard` or `admin`), the card's own fields and
        `has_anonymous_card`, which the detail schema does not carry. What the detail
        called `origin`, the marketing page, is `origin_page` here, as it is a
        filter of the list."""
        detail = FreelancerService(session, deps.settings, deps.http).get(freelancer_id)
        row = TalentsService(session).row_for_freelancer(freelancer_id)
        shown = detail.model_dump(mode="json")
        return {**shown, **row.model_dump(mode="json"), "origin_page": shown["origin"]}

    @mcp.tool()
    def list_talents(
        limit: int = TALENTS_LIMIT_DEFAULT,
        status: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        job_title: str | None = None,
        remote: str | None = None,
        min_rate: str | None = None,
        max_rate: str | None = None,
        origin: str | None = None,
        origin_page: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        with_logins: bool | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        skills: list[str] | None = None,
        location: str | None = None,
        seniority: list[str] | None = None,
        min_years: int | None = None,
        max_years: int | None = None,
        industries: list[str] | None = None,
        languages: list[str] | None = None,
        card: str | None = None,
        sort: str | None = None,
        fields: list[str] | None = None,
    ) -> CallToolResult:
        """I talenti come li vede la schermata «Talenti» dell'area admin: le schede
        freelance e le iscrizioni senza scheda in un'unica lista, `status` `lead` per
        le seconde. Ogni riga è corta (`id`, `nome`, `last_name`, `status` e, dalla scheda
        anonima di Claude, `role`, `seniority`, `experience_years`, `daily_rate`, `location`,
        le prime cinque `skills` e la `profile_summary` accorciata; una chiave senza valore
        manca): per valutare chi fa al caso di una richiesta basta questa lista, e una
        pagina di cinquanta righe pesa circa 25 KB. `seniority` e `experience_years` sono quelli che
        un admin ha corretto (`override_freelancer`) se ci sono; senza `experience_years` e con
        `seniority_uncertain` vera il CV non data nessun ruolo e il livello è solo una
        lettura delle responsabilità. `fields` sceglie i campi per nome e
        dà solo quelli (con `id`), interi: `["email", "settori", "lingue", "fascia"]`,
        con `skills` e `profile_summary` intere se le nomini; `["all"]` dà la riga
        completa, con `email`, `linkedin_url`, `origin`, `utm_source`, `created_at`,
        `vetted_at`, `has_anonymous_card`, `job_title`. Dal più recente, o dal più
        pertinente quando `q` restringe. L'`id` di una riga va a `get_talent`, che per un
        lead risponde la riga stessa; la scheda di un lead si crea con
        `create_freelancer_from_signup`.

        Filtri, tutti insieme: `q` cerca nome, cognome, email e posizione; `skills`
        tiene le schede che nominano ogni termine dato (per esempio `["SQL Server",
        "React"]`) nella posizione o nel ruolo, nelle competenze o nella sintesi della
        scheda anonima; `seniority` quelle con uno dei livelli dati (`junior`, `mid`,
        `senior`, `lead`); `min_years` e `max_years` per anni di esperienza della scheda
        (estremi compresi); `industries` e `languages` per i settori e le lingue della scheda
        (ogni termine deve comparire, come `skills`: `["fintech"]`,
        `["inglese"]`); `location` per il luogo della scheda (`"Milano"`); `job_title` per
        il ruolo dichiarato; `remote` per disponibilità (remoto/ibrido/in_sede);
        `min_rate` e `max_rate` per tariffa a giornata (stringa, «500», «450.50»
        o all'italiana «1.500», «1.234,50»); `origin` per come è nata la riga (`form`
        iscrizione senza scheda, `wizard` compilata dalla persona, `admin` scritta da un
        admin da una ricerca), `origin_page` per la pagina del sito da cui è partita
        (`home`, `pigrocrm`); `utm_source` per campagna; `card` per lo stato della
        scheda anonima (`present`; `failed` se Claude non è riuscito a scriverla;
        `absent` se non c'è e non ha fallito, per esempio senza CV); `has_cv` per le
        schede con o senza CV; `with_logins` per chi è entrato almeno una volta nella sua
        area; `created_from` e `created_to` per data di creazione (ISO 8601, anche solo
        `AAAA-MM-GG`). Un valore non ammesso per `origin`, `seniority`, `card`,
        `sort` o `fields` è un errore, non una lista vuota. I filtri che una riga nuda
        non ha (posizione, competenze, luogo, seniority, anni, settori, lingue, scheda,
        remoto, tariffa, `origin_page`, `origin` `wizard` o `admin`, `has_cv=true`)
        escludono i lead; `origin=form` esclude invece ogni scheda.

        `sort`: `recent` (più nuovi prima, anche se `q` cerca), `rate_asc`,
        `rate_desc`, `experience_years_asc`, `experience_years_desc`. Ordinando per tariffa o per
        anni
        restano solo le schede che quel valore ce l'hanno (niente lead, niente schede
        senza tariffa o senza anni), e `total` e `by_status` contano quelle. Senza
        `sort`: dal più recente, o dal più pertinente quando `q` cerca.

        `limit` è di 50 righe se non lo dai e di 200 al massimo. `by_status` conta con
        ogni filtro tranne `status`, `total` con tutti. `next_cursor` è il cursore opaco
        della pagina successiva, `None` all'ultima: ripassalo in `cursor` insieme agli
        stessi parametri (anche `sort`: un cursore nato con un altro ordine o con
        un'altra `q` è rifiutato). Solo lettura."""
        term = search_term(q)
        skills = skill_terms(skills)
        sectors = skill_terms(industries, "industries")
        languages = skill_terms(languages, "languages")
        levels = skill_terms(seniority, "seniority")
        place = search_term(location)
        for field, value in (
            ("status", status),
            ("job_title", job_title),
            ("remote", remote),
            ("origin", origin),
            ("origin_page", origin_page),
            ("utm_source", utm_source),
            ("card", card),
            ("sort", sort),
            ("cursor", cursor),
        ):
            bounded(value, field)
        created_after = moment(created_from, "created_from")
        created_before = moment(created_to, "created_to")
        rate_from = number(min_rate, "min_rate")
        rate_to = number(max_rate, "max_rate")

        def call(session: Session) -> CallToolResult:
            # The fields first: a misspelt name costs no query.
            selected = row_fields(fields)
            page = TalentsService(session).list_recent(
                limit=max(1, min(limit, TALENTS_LIMIT_MAX)),
                status=status,
                q=term,
                cursor=cursor,
                job_title=job_title,
                remote=remote,
                min_rate=rate_from,
                max_rate=rate_to,
                origin=origin,
                origin_page=origin_page,
                utm_source=utm_source,
                has_cv=has_cv,
                with_logins=with_logins,
                created_from=created_after,
                created_to=created_before,
                skills=skills,
                location=place,
                seniority=levels,
                min_years=min_years,
                max_years=max_years,
                industries=sectors,
                languages=languages,
                card=card,
                sort=sort,
            )
            if selected is not None and "email" in selected:
                deps.record_read(
                    session,
                    READ_ENTITY,
                    read_entity_id("list_talents"),
                    EMAILS_READ,
                    {"tool": "list_talents", "lines": len(page.items)},
                )
            return compact_result(talents_page(page.model_dump(mode="json"), selected))

        return deps.call(call)

    @mcp.tool()
    def get_talent(talent_id: str) -> dict[str, Any]:
        """Un talento, per id: la riga che `list_talents` mostra, letta una per una, con
        tutti i suoi campi (anche `industries`, `languages`, `profile_summary` e `band`, la banda di
        prezzo per il cliente). Per una scheda freelance risponde il dettaglio completo
        della schermata admin (REB-284), lo stesso che legge «Dettaglio talento»: i dati
        della persona con lo stato e il thread delle `note`, l'attribuzione
        dell'iscrizione `signup_utm`, gli ultimi accessi e gli ultimi download della
        guida, e lo spazio Pigro quando l'indirizzo ne ha uno. `origin` è quella della
        lista (`wizard` o `admin`); la pagina del sito da cui è partita la persona
        (`home`, `pigrocrm`) è `origin_page`. Per un'iscrizione senza scheda risponde
        la riga nuda, `status` `lead`, da cui la scheda si crea con
        `create_freelancer_from_signup`; un id la cui email ha ormai una scheda risponde
        la scheda, come la lista. Ogni riga con una scheda porta anche `vetted_at`
        (REB-518, `None` se non verificato) e `has_anonymous_card`, se Claude ne ha
        scritta una da proporre nel team builder. Solo lettura."""
        key = to_uuid(talent_id, "talent_id")

        def call(session: Session) -> dict[str, Any]:
            if session.get(Freelancer, key) is not None:
                return _freelancer_talent(session, key)
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
                return _freelancer_talent(session, holder[0])
            return signup_read(signup).model_dump(mode="json")

        return deps.call(call)

    @mcp.tool()
    def list_duplicates() -> dict[str, Any]:
        """Chi è in lista due volte (REB-800): `mergeable` sono le coppie di schede vive con
        lo stesso profilo LinkedIn, che `merge_freelancers` può unire; `from_review` quelle
        che hanno in comune solo il nome o il telefono, da guardare prima di decidere,
        mai unite da sole; `signups` quante iscrizioni ripetono un profilo (nessuna
        scheda da unire: restano fuori dalle liste dei lead). Per ogni coppia `survivor` è
        la scheda col CV più recente, quella che tiene, e `absorbed` quella che entra
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
