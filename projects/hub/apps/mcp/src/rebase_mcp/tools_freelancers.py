"""The freelancer card's tools: create it from a signup, read it with its thread, write what an
admin knows (status, notes, overrides, vetted), delete and restore it, and read or revert
its trail."""

from typing import Any

from mcp.server import MCPServer
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.card_persona import persona_differences, stored_persona
from rebase_core.freelancer_schemas import FreelancerDraft, FreelancerOverride, FreelancerRead
from rebase_core.freelancers import FreelancerService
from rebase_core.notes import NoteService
from rebase_mcp.tools_common import (
    SOURCE,
    Deps,
    as_remote,
    integer,
    number,
    supplied_text,
    to_uuid,
)


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

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
    ) -> dict[str, Any]:
        """Crea (o riscrive) la scheda freelance di un'iscrizione con quanto si trova in
        pubblico su quella persona: nome e cognome obbligatori, poi il profilo LinkedIn,
        la posizione come la dichiara lei, altri link (sito, GitHub, portfolio). Tariffa e
        modalità di lavoro solo se una fonte pubblica le dice, altrimenti restano vuote
        con il CV: la persona le completa dalla sua area. `fonti` sono gli indirizzi https
        da cui vengono le informazioni, da una a dieci, e finiscono come nota nel thread della
        scheda, firmata dall'admin dietro il token. Una
        scheda che la persona ha già compilato non si tocca: il tool rifiuta."""
        draft = FreelancerDraft(
            nome=nome,
            cognome=cognome,
            linkedin_url=linkedin_url,
            posizione=posizione,
            tariffa_giornaliera=number(tariffa_giornaliera, "tariffa_giornaliera"),
            remoto=as_remote(remoto),
            links=links or [],
            fonti=fonti,
        )
        # `draft_from_signup` returns through `get()`, which now answers a
        # `FreelancerDetail` for the admin HTTP route (REB-284); this tool keeps the
        # plain `FreelancerRead` shape it always had. The card was just drafted, so it
        # has no logins, downloads or signup UTM to report yet, and the thread comes
        # back on the write anyway. `get_talento` is the tool for the full detail.
        return deps.run(
            lambda s: FreelancerRead.model_validate(
                FreelancerService(s)
                .draft_from_signup(to_uuid(signup_id, "signup_id"), draft, deps.admin().id, SOURCE)
                .model_dump()
            )
        )

    @mcp.tool()
    def get_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Un freelance, per id, con `note`: il thread di chi lo ha seguito, dal più
        recente, ognuna con `id`, autore, data, `fonte` (`web`, `mcp`, `persona`, `sistema`),
        l'ultima modifica e `modificabile`. Le note cancellate non ci sono. `telefono` è
        quello dell'account; `contatti_cv` è quanto il CV dice della persona, letto da
        Claude insieme alla scheda anonima (nome, cognome, email, telefono, città, LinkedIn,
        altri link, aziende), `None` se la scheda non ne ha ancora (scritta prima della
        versione 3 del prompt o senza CV) e `differenze_cv` dove il CV e il profilo
        dicono cose diverse. Sono dati personali: solo per l'admin, mai per le schede
        anonime, il cloud o il team builder."""

        def call(session: Session) -> dict[str, Any]:
            key = to_uuid(freelancer_id, "freelancer_id")
            read = FreelancerRead.model_validate(FreelancerService(session).get(key).model_dump())
            persona = stored_persona(session, key)
            return {
                **read.model_dump(mode="json"),
                "contatti_cv": persona.model_dump(mode="json") if persona is not None else None,
                "differenze_cv": [
                    difference.model_dump(mode="json")
                    for difference in persona_differences(session, persona, key)
                ],
            }

        return deps.call(call)

    @mcp.tool()
    def set_freelancer_status(freelancer_id: str, stato: str) -> dict[str, Any]:
        """Sposta una candidatura fra nuovo, contattato, attivo e scartato. Non tocca
        quello che la persona ha scritto. Il cambio finisce nel registro di
        `get_freelancer_audit` (`status_changed`, con prima e dopo): per il perché usa
        `add_freelancer_note`."""
        return deps.run(
            lambda s: FreelancerService(s).set_status(
                to_uuid(freelancer_id, "freelancer_id"),
                StatusChange(stato=stato),
                admin_id=deps.admin().id,
                fonte=SOURCE,
            )
        )

    @mcp.tool()
    def add_freelancer_note(freelancer_id: str, testo: str) -> dict[str, Any]:
        """Aggiunge una nota al thread di un freelance, senza toccare lo stato: una
        telefonata fatta, un'impressione, una cosa da ricordare. La nota è già firmata
        (l'admin dietro il token) e datata, con `fonte` `mcp`: il testo non deve iniziare
        con una data o un'ora né nominare chi scrive. Fino a 4000 caratteri, anche su più
        righe. Si può correggere con `update_note` e cancellare con `delete_note`, e ogni
        aggiunta finisce nel registro di `get_freelancer_audit`."""
        return deps.run(
            lambda s: NoteService(s).add(
                "freelancer",
                to_uuid(freelancer_id, "freelancer_id"),
                testo,
                autore_id=deps.admin().id,
                fonte=SOURCE,
            )
        )

    @mcp.tool()
    def update_note(note_id: str, testo: str) -> dict[str, Any]:
        """Sostituisce il testo di una nota di un freelance o di un'azienda, per `id`
        (lo trovi in `note` di `get_freelancer` e `get_company`). Solo le note scritte da
        un admin (`fonte` `web` o `mcp`, o senza fonte): quelle della persona o del sistema
        sono eventi e vengono rifiutate. Resta scritto chi l'ha modificata e quando, e il
        testo di prima è nel registro (`note_edited`). Il testo non deve iniziare con una
        data o un'ora né nominare chi scrive: la nota è già firmata e datata."""
        return deps.run(
            lambda s: NoteService(s).update(
                to_uuid(note_id, "note_id"), testo, admin_id=deps.admin().id, fonte=SOURCE
            )
        )

    @mcp.tool()
    def delete_note(note_id: str) -> dict[str, Any]:
        """Cancella una nota di un freelance o di un'azienda, per `id`. Esce dal thread e
        da `get_freelancer`/`get_company`, ma il registro conserva chi l'ha cancellata e il
        suo testo (`note_deleted`). Valgono le regole di `update_note`: le note della
        persona o del sistema non si cancellano."""

        def call(session: Session) -> dict[str, Any]:
            NoteService(session).delete(
                to_uuid(note_id, "note_id"), admin_id=deps.admin().id, fonte=SOURCE
            )
            return {"nota": note_id, "cancellata": True}

        return deps.call(call)

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
        compilata_da: str | None = None,
        seniority: str | None = None,
        anni: str | None = None,
    ) -> dict[str, Any]:
        """Scrive o svuota qualsiasi campo del profilo: nome,
        cognome e profilo LinkedIn (sull'identità condivisa in `users`), tariffa,
        posizione, modalità di lavoro, link, stato e chi ha compilato per ultimo
        (persona/admin). Le note non sono un campo: stanno nel thread (`add_freelancer_note`,
        `update_note`). Ogni parametro omesso resta com'era; una stringa vuota svuota
        il campo dove è ammesso (tariffa, posizione, LinkedIn, modalità di
        lavoro), altrove è rifiutata perché il campo non può restare senza un valore.
        `links` sostituisce l'intera lista, `[]` la svuota. `seniority` (junior, mid,
        senior o lead) e `anni` (0-60) correggono il livello che la scheda anonima
        dichiara: la parola dell'admin vince su quella di Claude in ogni lettura
        (`list_talenti`, `get_talento`, `get_freelancer_card`, il cloud, il team
        builder) e sopravvive a «Rigenera scheda»; la stringa vuota la toglie. Ogni
        modifica reale finisce nel registro di `get_freelancer_audit`, con il valore
        prima e dopo; per annullarla c'è `revert_freelancer_action`."""
        kwargs: dict[str, Any] = {}
        if nome is not None:
            kwargs["nome"] = supplied_text(nome)
        if cognome is not None:
            kwargs["cognome"] = supplied_text(cognome)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = supplied_text(linkedin_url)
        if tariffa_giornaliera is not None:
            kwargs["tariffa_giornaliera"] = (
                None
                if tariffa_giornaliera == ""
                else number(tariffa_giornaliera, "tariffa_giornaliera")
            )
        if posizione is not None:
            kwargs["posizione"] = supplied_text(posizione)
        if remoto is not None:
            kwargs["remoto"] = supplied_text(remoto)
        if links is not None:
            kwargs["links"] = links
        if stato is not None:
            kwargs["stato"] = supplied_text(stato)
        if compilata_da is not None:
            kwargs["compilata_da"] = supplied_text(compilata_da)
        if seniority is not None:
            kwargs["seniority_admin"] = supplied_text(seniority)
        if anni is not None:
            kwargs["anni_admin"] = integer(anni, "anni")
        override = FreelancerOverride(**kwargs)
        return deps.run(
            lambda s: FreelancerService(s).override(
                to_uuid(freelancer_id, "freelancer_id"), override, deps.admin().id
            )
        )

    @mcp.tool()
    def delete_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Cancella la scheda: esce da Talenti e dall'elenco freelance. Reversibile,
        mai una cancellazione vera: `restore_freelancer` la fa tornare com'era."""
        return deps.run(
            lambda s: FreelancerService(s).soft_delete(
                to_uuid(freelancer_id, "freelancer_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def restore_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Ripristina una scheda cancellata: torna in Talenti e nell'elenco freelance.
        Su una scheda già attiva non cambia nulla."""
        return deps.run(
            lambda s: FreelancerService(s).restore(
                to_uuid(freelancer_id, "freelancer_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def set_freelancer_vetted(freelancer_id: str, vetted: bool) -> dict[str, Any]:
        """«Segna come verificato» (`vetted` vero) o «Togli la verifica» (falso, REB-518):
        il bollino «Verificato da rebase» che il talent cloud mostra accanto al profilo.
        Una marca già messa, o già tolta, non scrive nulla: la data resta quella della
        prima verifica, non dell'ultimo clic."""
        return deps.run(
            lambda s: FreelancerService(s).set_vetted(
                to_uuid(freelancer_id, "freelancer_id"), vetted, deps.admin().id
            )
        )

    @mcp.tool()
    def get_freelancer_audit(freelancer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Chi ha modificato, svuotato, cancellato o ripristinato questa scheda, e
        quando: ogni voce con l'amministratore, il tipo di azione e, per una modifica di
        campo, il valore prima e dopo. Anche ogni volta che la scheda anonima è stata
        riscritta (`card_regenerated`: esito, motivo se non scritta, `fonte`, costo, se in
        blocco). Funziona anche su una scheda cancellata. Dalla più
        recente. Solo lettura; per annullare una modifica di campo usa
        `revert_freelancer_action` con l'id della voce."""
        return deps.run_list(
            lambda s: FreelancerService(s).audit_timeline(
                to_uuid(freelancer_id, "freelancer_id"), limit
            )
        )

    @mcp.tool()
    def revert_freelancer_action(freelancer_id: str, action_id: str) -> dict[str, Any]:
        """Riporta un campo al valore che aveva prima di una modifica passata, letta
        dalla voce del registro `action_id` (da `get_freelancer_audit`). Solo su una
        voce che è una modifica di campo, non su una cancellazione o un ripristino."""
        return deps.run(
            lambda s: FreelancerService(s).revert(
                to_uuid(freelancer_id, "freelancer_id"),
                to_uuid(action_id, "action_id"),
                deps.admin().id,
            )
        )
