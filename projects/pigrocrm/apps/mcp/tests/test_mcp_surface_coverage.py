"""The complement of `test_mcp_invoice_ban.py`, and the half of the product's central
claim that had no test at all.

That file proves a list of named operations is **not** reachable as MCP tools. Nothing
proved the other direction: that every *other* public service method is. The claim the
product actually makes is "an agent can do anything a user can, minus a deliberate,
named, tested list of exclusions", and a ban list on its own only tests the subtraction.
A service method added without a tool is a silent hole -- the ban list stays honest while
the coverage quietly rots, and nobody finds out, because the only artefact that would
have noticed is a list somebody has to remember to update.

So this is mechanical on both sides. The **inventory** is an AST sweep of every
`*Service` class under `packages/core`, and the **reachability** is an AST walk of every
call in `tools/` and `resources/`, resolved back to the class that receives it. Neither
is a list. What is a list -- deliberately, and in one place -- is the *taxonomy of
exclusions* below: each unexposed method carries the category it belongs to and the
reason it is in that category, because "there is no tool for this" and "there must never
be a tool for this" are different statements and only one of them is a policy.

Adding a public service method therefore fails this file until somebody either writes
the tool or writes down why not. That is the entire point: the decision is forced at the
moment it is cheap, and it is recorded next to the refusals it has to live beside.

Two properties of the walk are worth stating, because they are what makes the result
trustworthy rather than merely green:

  * reachability resolves the *receiver*, not the method name. `.create(` appears in the
    tool modules a dozen times over six different services, so a substring scan would
    call every `create` in the codebase covered. The walk binds `ServiceClass(...)`,
    `x = ServiceClass(...)` and the module-private factories (`_invoices(context) ->
    InvoiceService`) that `tools/invoices.py` and `tools/documents.py` actually use, and
    attributes each call to the class it lands on.
  * it covers `resources/` as well as `tools/`. The ban test scans only `tools/`, which
    is right for its question -- `@mcp.tool()` decorators exist nowhere else -- but wrong
    for this one: a resource read is agent-reachable too, and `PipelineService.get` is
    reachable *only* from there. The same breadth also closes a gap in the ban itself,
    asserted below: a forbidden method reached from a resource template would evade a
    tools-only scan while being just as callable by an agent.
"""

import ast
from pathlib import Path

import pytest
from test_mcp_invoice_ban import FORBIDDEN_QUALIFIED_CALLS, FORBIDDEN_SERVICE_CALLS

MCP_SRC = Path(__file__).resolve().parents[1] / "src" / "pigrocrm_mcp"
TOOLS_DIR = MCP_SRC / "tools"
RESOURCES_DIR = MCP_SRC / "resources"
# Skipped by `_reachable`, so that "reachable" here means "reachable on a default
# installation". Both modules are registered only when `mcp_full_access` is on
# (`tools/drive_privileged.py` needs a configured Google client as well), so counting
# their calls would make the banned operations look reachable everywhere -- including on
# the installations the exclusions below exist to describe.
# `test_mcp_invoice_ban.py` owns the other half: that those calls appear in one of those
# files and nowhere unconditional, and that `server.py` reaches each only behind the
# guard.
PRIVILEGED_MODULES = (TOOLS_DIR / "privileged.py", TOOLS_DIR / "drive_privileged.py")
CORE_DIR = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "pigrocrm" / "core"

Method = tuple[str, str]


# --- the taxonomy ------------------------------------------------------------------
#
# Six categories, because there are six genuinely different reasons a public service
# method has no tool, and collapsing them into one regex or one "unexposed" set would
# hide the only distinction that matters: which of these are decisions and which are
# accidents. Every entry is keyed by `(service class, method)` -- never by the bare
# method name, which `create`, `get`, `list`, `update` and `restore` all share across
# half a dozen services.


# 1. Forbidden. The banned operations of `test_mcp_invoice_ban.py`, which owns the
#    policy; they appear here only so that the sweep's arithmetic accounts for them, and
#    a test below asserts this block names exactly the same methods that file bans. If
#    the two ever disagree, one of them is out of date and neither can be trusted.
#    A ban expressed as a `(service, method)` pair rather than by bare name (as
#    `FiscalProfileService.upsert` was until ORB-188: `EmitterProfileService` has an
#    `upsert` too) is the shape this table has used all along, and the reason the
#    comparison test below compares the qualified bans as pairs and the rest as names.
_VIETATE: dict[Method, str] = {
    ("InvoiceService", "issue"): "atto fiscale irreversibile (slice 3 §11)",
    ("InvoiceService", "annul"): "atto fiscale irreversibile (slice 3 §11)",
    ("InvoiceService", "mark_transmitted_externally"): "atto fiscale (slice 3 §11)",
    ("InvoiceService", "export_xml"): "esiste solo per una fattura emessa (slice 3 §11)",
    (
        "InvoiceService",
        "import_issued",
    ): "scrive nel registro fiscale un numero deciso altrove (slice 9 §3)",
    ("InvoiceService", "declare_gaps"): "dichiara i buchi del registro fiscale (slice 9 §3.2)",
    (
        "InvoiceService",
        "review_import",
    ): (
        "REB-365: legge i byte gia' archiviati di un documento e classifica cosa "
        "succederebbe, senza scrivere nulla -- vietata come le due scritture che "
        "affianca perche' espone gli stessi fatti (fattura di un fornitore, conflitto "
        "col registro) prima che una persona confermi"
    ),
    ("TimeEntryService", "recalculate_rates"): "riscrive il passato (slice 4 §11)",
    ("TimeEntryService", "update_user_rates"): "configurazione tariffaria (slice 4 §11)",
    ("TimeEntryService", "update_deal_rate"): "configurazione tariffaria (slice 4 §11)",
    ("CostCategoryService", "create_cost_category"): "configurazione (slice 4 §11)",
    ("CostCategoryService", "update_cost_category"): "configurazione (slice 4 §11)",
    ("CostCategoryService", "archive_cost_category"): "configurazione (slice 4 §11)",
    ("CostCategoryService", "unarchive_cost_category"): (
        "configurazione: e' `archive_cost_category` nel verso opposto, e slice 4 §11 "
        "non l'aveva elencata per omissione, non per distinzione"
    ),
    ("PeriodLockService", "close_period"): "chiusura di periodo (slice 4 §11)",
    ("PeriodLockService", "reopen_period"): "riapertura di periodo (slice 4 §11)",
    ("AnalyticsService", "bind_time_to_invoice"): "precede l'emissione (slice 4 §11)",
    ("AnalyticsService", "get_fiscal_estimate"): "posizione fiscale del titolare (slice 4 §11)",
    ("GmailSyncService", "discover"): (
        "interroga Gmail sul dominio del cliente a carico della quota e del consenso "
        "del titolare, come `sync` e `backfill`; a differenza loro l'installazione puo' "
        "aprirla con `mcp_full_access`"
    ),
    ("GmailAttachmentService", "attachment_text"): (
        "scarica da Gmail l'allegato che la sincronizzazione non salva mai, a carico "
        "della quota e del consenso del titolare, e ne restituisce il testo senza "
        "archiviare niente; l'installazione puo' aprirla con `mcp_full_access`"
    ),
    ("DocumentService", "import_bytes"): (
        "archivia byte che il CRM non ha prodotto, presi dal Drive del titolare a "
        "carico della sua quota e del suo consenso (slice 9 §4.2: `import_drive_file`); "
        "raggiungibile solo da `tools/drive_privileged.py`, cioe' dietro "
        "`mcp_full_access` **e** un client Google configurato"
    ),
}


# 1b. Forbidden, but on a receiver this file's sweep cannot see. `_service_methods`
#     enumerates classes whose name ends in `Service`, which is this codebase's own rule
#     for "a domain service" and the reason the inventory is mechanical rather than a
#     list. `DriveReader` is not one: it is a confined *capability object* built by
#     `drive_reader_for`, holding a transport and a tuple of roots, with no session and
#     no actor -- and it is deliberately not a service, because whether this credential
#     may be used at all was decided before it was constructed.
#
#     So its methods can be neither swept nor excluded above: `_VIETATE` is keyed by
#     `(service, method)` and `test_every_declared_exclusion_still_names_a_real_method`
#     asserts the service half is really in `SERVICES`, which would fail for every
#     `DriveReader` pair. They are recorded here instead, with the same obligation --
#     each name must be in `FORBIDDEN_SERVICE_CALLS`, and `test_the_forbidden_block_
#     names_exactly_what_the_ban_test_bans` counts these alongside `_VIETATE` so the two
#     files still cannot drift.
#
#     `is_within_roots` is the one `DriveReader` method that is *not* here, and it is not
#     an omission: no tool calls it, so putting it in `FORBIDDEN_SERVICE_CALLS` would
#     fail the ban test's "and it appears in a privileged module" half. It needs no ban
#     of its own regardless -- it is the guard `list_children` and the two `read_*` apply
#     internally, and reaching it would require the very `DriveReader` that
#     `test_no_unconditional_module_can_even_obtain_a_drive_reader` denies.
_FUORI_DAL_SETACCIO: dict[Method, str] = {
    ("DriveReader", "list_children"): (
        "elenca una cartella del Drive personale del titolare (slice 9 §4.2)"
    ),
    ("DriveReader", "read_text"): (
        "restituisce il contenuto di un file del titolare: dato non attendibile, a "
        "carico della sua quota Drive (slice 9 §4.2)"
    ),
    ("DriveReader", "read_bytes"): (
        "come `read_text`, in byte: e' cio' che `import_drive_file` archivia (slice 9 §4.2)"
    ),
    ("DriveReader", "describe_roots"): (
        "nomina le radici configurate, il punto d'ingresso di `list_drive_files` (slice 9 §4.1)"
    ),
}


# 2. Internal. Called by other code inside `packages/core`, not by an adapter: none of
#    them takes an `actor`, which is the mechanical signature of "this is not an
#    operation somebody performs" -- there is no permission to check because there is no
#    caller with a role. `TimeReportService.variables_for` is the one exception to the
#    no-actor rule and is listed for the same reason: it is the intermediate step of
#    `render_pdf`, not an operation of its own.
_INTERNE: dict[Method, str] = {
    ("ActivityService", "record"): "scrive la timeline per conto di un altro servizio",
    ("CostCategoryService", "require_active"): "validazione interna di CostService",
    ("DealService", "set_stage_in_transaction"): (
        "e' la meta' di sola mutazione di `move_stage`, introdotta dalla convenzione "
        "`*_in_transaction` della fetta 6 (§9.3): muta, non registra, non committa e non "
        "controlla l'autorizzazione, perche' gira dentro la transazione del *trigger* che "
        "l'autorizzazione l'ha gia' fatta. Non prende `actor` -- la firma meccanica di "
        "\"non e' un'operazione che qualcuno compie\" -- e non avrebbe niente da "
        "controllare se lo prendesse. L'unico chiamante e' `core/automations/runner.py`, e "
        "`packages/core/tests/test_in_transaction_callers.py` lo impone sull'AST di ogni "
        "sito di chiamata dei tre pacchetti: un tool che la esponesse sarebbe esattamente "
        "il bypass di autorizzazione che quella guardia esiste per impedire. Lo spostamento "
        "di stage resta raggiungibile dall'agente sotto il suo nome vero, `move_deal`, "
        "che autorizza, registra e committa"
    ),
    ("DocumentService", "storage_key_for"): "costruisce una chiave di storage",
    ("EmitterProfileService", "as_template_values"): "alimenta il renderer dei template",
    ("FieldDefinitionService", "specs_for"): "alimenta la validazione dei campi custom",
    ("FiscalProfileService", "snapshot"): "lettura interna del regime, senza actor",
    ("InvoiceService", "undeclared_gaps"): (
        "lettura interna, senza actor: `issue` la chiama per rifiutare l'emissione "
        "nativa finche' un buco del registro non e' stato importato o dichiarato "
        "(slice 9 §3.2 regola 4). Gli altri due chiamanti sono adapter, non "
        "`packages/core`, ma nessuno dei due e' un tool o una resource di questa "
        "scansione: `import_issued_invoice` la usa per comporre "
        "`buchi_non_dichiarati`, e vive in `privileged.py`, esente da questa scansione "
        "come il resto degli atti fiscali; `POST /api/invoices/import` la usa per lo "
        "stesso campo nella sua risposta REST, un adapter che questa scansione non "
        "copre affatto"
    ),
    ("GmailOAuthService", "redirect_uri"): "e' l'URL fisso che Google confronta "
    "carattere per carattere, non un'operazione",
    ("GoogleDriveOAuthService", "redirect_uri"): "stessa ragione di "
    "`GmailOAuthService.redirect_uri`: e' l'URL fisso che Google confronta carattere "
    "per carattere, non un'operazione (slice 9 §5.1)",
    ("PeriodLockService", "assert_writable"): "guardia invocata dagli altri servizi",
    ("PeriodLockService", "is_closed"): "guardia invocata dagli altri servizi",
    ("PipelineService", "default_stage"): "risolve lo stage iniziale di un nuovo deal",
    ("TemplateService", "declared_variables"): "parsing interno usato da describe",
    ("EmailDraftService", "repo_draft"): "restituisce la riga ORM al percorso di invio "
    "(B2-5) e alla riconciliazione (B2-6), che scrivono `send_state` e "
    "`sent_gmail_message_id`: colonne che nessuno schema accetta in ingresso perche' "
    "sono fatti, non input. Non prende `actor`, che e' la firma di \"non e' "
    "un'operazione che qualcuno compie\"",
    ("TimeReportService", "variables_for"): "passo intermedio di render_pdf/build_xlsx",
    ("UserService", "count"): "conta gli utenti per il bootstrap del primo admin",
    # REB-362: none of these three takes an actor -- the mechanical signature this
    # category is named for. `ProposalService` is the real MCP-facing read (spec §10,
    # "agents propose, humans confirm"): a proposal already carries `campi_proposti`/
    # `estratto` for review, and once accepted, `id_risultato` names the row it
    # produced -- so `get_proposal` is what an agent actually calls, never a direct
    # read of the `work_unit`/`approval` row underneath.
    ("WorkUnitService", "get"): "nessun actor: lettura interna dietro get_proposal",
    ("WorkUnitService", "transitions_for"): (
        "nessun actor: la cronologia di un day non ha una superficie propria -- "
        "get_proposal espone l'esito di una proposta accettata, non il registro delle "
        "transizioni del work_unit che ne e' risultato"
    ),
    ("ApprovalService", "get"): "nessun actor: lettura interna dietro get_proposal",
}


# 3. Credentials and accounts. The one category where exposure would be a privilege
#    escalation *by construction* rather than by degree: the agent's own key is a PAT,
#    which inherits its owner's full role and never expires (residuo R10), so a tool that
#    minted or revoked one would let a token extend or destroy its own access. Creating
#    users is the same hole with a longer fuse.
#    Tutto Gmail, tranne le tre letture, sta qui -- e la decisione e' stata presa in
#    B1-14, che possiede la superficie MCP di 5B. La riga che la divide non e'
#    "lettura contro scrittura" ma *di chi e' la risorsa, e di chi la decisione*:
#
#      * quello che l'agente puo' fare e' leggere lo specchio gia' archiviato in
#        `gmail_messages` e lo stato della credenziale (`list_gmail_messages`,
#        `get_gmail_message`, `describe_gmail_account`). Nessuna delle tre chiama
#        Google, nessuna consuma la quota Gmail di qualcuno, nessuna esercita un
#        consenso: leggono righe che questo CRM ha gia' deciso di tenere;
#      * quello che non puo' fare e' *andare a prendere* la posta (`sync`,
#        `backfill`), collegare o scollegare una casella, decidere cosa il CRM
#        conserva, e inviare. In ognuno di questi casi la risorsa o la decisione
#        appartengono alla persona, non all'agente.
#
#    `start` restituisce un URL di consenso che solo un browser umano puo' percorrere
#    -- un agente che lo ricevesse non potrebbe fare altro che passarlo a qualcuno --
#    e `complete` richiede un `code` che esiste solo dentro quel redirect, quindi
#    nessuno dei due e' eseguibile da un canale strumentale. `disconnect` e' il verso
#    opposto: distrugge una credenziale verso un servizio *terzo* e, con
#    `delete_messages`, la corrispondenza archiviata.
#
#    L'assenza dell'invio non e' registrata qui perche' in 5B-1 non esiste ancora un
#    servizio che invii: il divieto strutturale sui nomi degli strumenti vive in
#    `test_mcp_invoice_ban.py`, che possiede i divieti per costruzione.
_CREDENZIALI: dict[Method, str] = {
    ("UserService", "reset_password"): (
        "cambia la credenziale di una persona: e' l'operatore al terminale del server "
        "(`pigrocrm resetpassword`) a farlo, mai un agente, per la stessa ragione per cui "
        "`create` non e' un tool"
    ),
    ("GmailOAuthService", "start"): "il consenso Google si da' da un browser, non da "
    "un tool: l'URL di autorizzazione non e' percorribile da un agente",
    ("GmailOAuthService", "complete"): "il `code` esiste solo dentro il redirect di "
    "Google verso il callback: nessun agente puo' averlo",
    ("GmailOAuthService", "disconnect"): "revocare l'accesso a una casella di terzi (e "
    "cancellarne la corrispondenza) e' una decisione della persona",
    ("GoogleDriveOAuthService", "start"): "stessa ragione di `GmailOAuthService.start` "
    "(spec 9 §5.1): il consenso Google si da' da un browser, non da un tool -- l'URL di "
    "autorizzazione non e' percorribile da un agente",
    ("GoogleDriveOAuthService", "complete"): "stessa ragione di "
    "`GmailOAuthService.complete`: il `code` esiste solo dentro il redirect di Google "
    "verso il callback, nessun agente puo' averlo",
    ("GoogleDriveOAuthService", "disconnect"): "stessa ragione di "
    "`GmailOAuthService.disconnect`: revocare l'accesso a un servizio di terzi e' una "
    "decisione della persona",
    ("GmailSyncService", "sync"): (
        "il consenso a leggere la casella e' della persona, e lo e' anche il momento in "
        "cui viene esercitato: `sync` va a prendere posta da un servizio terzo, sotto "
        "l'autorizzazione OAuth di quella persona e a carico della sua quota Gmail, "
        "quindi un agente che lo invocasse (o lo ritentasse) spenderebbe una risorsa "
        "che non e' sua. Non toglie nulla all'agente: quello che serve leggere e' la "
        "copia gia' archiviata in `gmail_messages`, che il pulsante o il cron della "
        "persona tengono aggiornata, ed e' su quella che B1-14 ha costruito "
        "`list_gmail_messages` e `get_gmail_message`"
    ),
    ("GmailSyncService", "backfill"): (
        "stessa ragione di `sync`, e con una scala diversa: `backfill(full=True)` "
        "rilegge una casella dall'inizio, quindi e' la richiesta piu' costosa che "
        "questa fetta sappia fare sulla quota Gmail di quella persona, e la spec 4.4 la "
        "vuole esplicita e iniziata da un umano proprio per questo. B1-14 aveva "
        "previsto un `backfill_gmail` e ha deciso di non scriverlo: esporre la "
        "richiesta *piu'* costosa mentre `sync` -- la meno costosa, e per la stessa "
        "ragione -- resta chiusa non e' una superficie che qualcuno possa spiegare. "
        "L'esclusione e' quindi permanente come le tre di `GmailOAuthService`, non piu' "
        "provvisoria"
    ),
    ("GoogleAccountService", "usable"): (
        "non e' un'operazione ma un cancello: lo chiamano `sync` e il percorso di invio "
        "*prima* di comporre qualsiasi cosa. Un tool che lo esponesse offrirebbe "
        "all'agente di chiedere un permesso invece di esercitarlo"
    ),
    ("GoogleAccountService", "mark_revoked"): (
        "registra un fatto che comunica Google, non una decisione di qualcuno: la "
        "chiama il solo punto che puo' apprenderlo, il rinnovo del token che riceve "
        "`invalid_grant`. Un agente che potesse marcare revocata una credenziale sana "
        "spegnerebbe Gmail a una persona senza che nulla sia successo"
    ),
    ("GoogleAccountService", "set_store_bodies"): (
        "decide se il CRM conserva il *corpo* della corrispondenza di qualcuno: e' la "
        "stessa famiglia di `disconnect`, una scelta della persona sui propri dati e "
        "non un'operazione che l'agente compie al posto suo"
    ),
    ("GoogleDriveAccountService", "usable"): (
        "stessa ragione di `GoogleAccountService.usable` (spec 9 sec 5.5): non e' "
        "un'operazione ma un cancello, chiamato prima di comporre qualsiasi lettura o "
        "scrittura Drive. Un tool che lo esponesse offrirebbe all'agente di chiedere un "
        "permesso invece di esercitarlo"
    ),
    ("GoogleDriveAccountService", "mark_revoked"): (
        "stessa ragione di `GoogleAccountService.mark_revoked`: registra un fatto che "
        "comunica Google, non una decisione di qualcuno, e lo chiama il solo punto che "
        "puo' apprenderlo -- il rinnovo del token che riceve `invalid_grant`"
    ),
    ("GoogleDriveAccountService", "set_roots"): (
        "quali cartelle il CRM legge e in quale scrive e' una scelta della persona sui "
        "propri dati, stessa famiglia di `GoogleAccountService.set_store_bodies`: non "
        "un'operazione che un agente compie al posto suo. Verificare che la cartella di "
        "scrittura sia davvero scrivibile e' compito di 9D, alla prima chiamata vera"
    ),
    ("EmailSendService", "send"): (
        "**l'invio non sara' mai un tool**, ed e' la decisione permanente di questa "
        "fetta: B2-10 l'ha confermata invece di riaprirla. Un'email che parte "
        "dall'indirizzo del titolare parla in suo nome a un cliente, non si richiama, e "
        "il destinatario e' una persona esterna al CRM: stessa famiglia di "
        "`GmailOAuthService.disconnect`, con in piu' che un agente che tenesse la "
        "*lettura* della posta e l'*invio* sullo stesso canale avrebbe la sorgente di "
        "injection e il canale di esfiltrazione insieme. Il divieto per costruzione sui "
        "nomi degli strumenti vive in `test_mcp_invoice_ban.py`, che possiede i divieti"
    ),
    ("EmailSendService", "reconcile"): (
        "stessa ragione di `sync`, in piccolo, e B2-10 ha deciso di non esporla: va a "
        "chiedere a Gmail, sotto il consenso OAuth della persona e a carico della sua "
        "quota, se un messaggio partito dalla sua casella e' arrivato -- e poi *scrive* "
        "`send_state` su un valore terminale. E' la meta' di riparazione dell'invio -- "
        "il pulsante «verifica» accanto a «esito da verificare» -- quindi appartiene a "
        "chi ha premuto Invia. La domanda che l'esclusione doveva reggere era: perche' "
        "un agente potrebbe interrogare una casella che non puo' sincronizzare? Non "
        "puo': e' la stessa quota e lo stesso consenso, e in piu' qui l'esito di quella "
        "interrogazione decide se una bozza risulta partita o no"
    ),
    ("EmailSendService", "reconcile_all"): (
        "non e' un'operazione che qualcuno compie: la chiama `GmailSyncService._run_cycle` "
        "all'inizio di ogni ciclo, sotto il consenso gia' verificato li'. Esporla "
        "significherebbe offrire all'agente il ciclo di sync per un'altra porta"
    ),
    ("PatService", "create"): "un agente non conia le proprie credenziali",
    ("PatService", "list"): "l'elenco dei token e' materiale di sicurezza",
    ("PatService", "revoke"): "revocare token e' amministrazione dell'account",
    ("PatService", "revoke_all_for"): "e' l'effetto della disattivazione, non un'operazione",
    ("PatService", "resolve"): "e' il passo di autenticazione, non un'operazione",
    ("RefreshTokenService", "issue"): "sessione del browser, non superficie agentica",
    ("RefreshTokenService", "consume"): "sessione del browser, non superficie agentica",
    ("RefreshTokenService", "rotate"): "sessione del browser, non superficie agentica",
    ("RefreshTokenService", "revoke_all"): "sessione del browser, non superficie agentica",
    ("MagicLinkService", "request"): "e' il passo di login via mail, non un'operazione",
    ("MagicLinkService", "enter"): "e' il passo di login via mail, non un'operazione",
    # REB-376 (design 2026-09-23 §1-2): `IdentityService` vive nel registro, non in
    # una singola CRM -- l'MCP server e' avviato per un solo spazio (`ScopedSessionProvider`)
    # e non ha mai una sessione sul database del registro, quindi nessuno di questi
    # metodi e' raggiungibile da un tool anche in linea di principio.
    ("IdentityService", "request"): "e' il passo di login via mail, un layer sopra -- come sopra",
    ("IdentityService", "enter"): "e' il passo di login via mail, un layer sopra -- come sopra",
    ("IdentityService", "upsert_and_issue"): (
        "e' l'effetto collaterale del login (`login`, `enter_with_link`, `accept_invite`), "
        "non un'operazione che qualcuno compie di per se'"
    ),
    ("IdentityService", "revoke_all"): (
        "sessione del browser, non superficie agentica -- come sopra"
    ),
    ("IdentityService", "get_or_create"): (
        "e' il primitivo del backfill da riga di comando (`pigrocrm "
        "rebuild-identity-index`, REB-379), mai una chiamata di un agente: crea una "
        "riga in `identities` per un indirizzo che nessuno ha ancora provato, "
        "l'esatto contrario del principio 'prova prima, elenco poi' che regge tutto "
        "questo modulo (design 2026-09-23 §0) -- oltre a vivere nel registro, come "
        "sopra"
    ),
    ("IdentityService", "resolve"): (
        "e' la verifica di liveness dietro il cookie del chooser (REB-377), sullo stesso "
        "registro senza sessione MCP -- come sopra"
    ),
    # REB-290 (spec 2026-09-17 §6): ogni metodo di `InvitationService` e' dichiarato,
    # non esposto. Invitare persone e' amministrazione dello spazio, la stessa ragione
    # che gia' tiene `UserService` fuori dai tool; in piu' qui c'e' il token grezzo,
    # che `create` e `resend` rispondono solo perche' il chiamante e' la rotaia che
    # lo imbuca in una mail, e un canale agentico lo leggerebbe nel contesto.
    ("InvitationService", "create"): (
        "invitare una persona e' amministrazione dello spazio (come `UserService."
        "create`), e il token grezzo della risposta esiste solo per finire in una "
        "mail: un tool lo porterebbe nel contesto dell'agente"
    ),
    ("InvitationService", "list"): "gli inviti in attesa sono materiale amministrativo",
    ("InvitationService", "resend"): (
        "stessa ragione di `create`: rigenera una credenziale durevole e la manda "
        "via mail, e chi la manda e' una persona"
    ),
    ("InvitationService", "revoke"): "revocare un invito e' amministrare lo spazio",
    (
        "InvitationService",
        "peek",
    ): "e' la pagina d'accettazione che legge l'invito, non un'operazione",
    ("InvitationService", "accept"): "e' il passo d'ingresso dell'invitato, non un'operazione",
    ("UserService", "create"): "creare utenti e' amministrazione dell'account",
    ("UserService", "update"): "cambiare ruoli e' amministrazione dell'account",
    ("UserService", "update_own_digest"): (
        "e' la preferenza di una persona sulla mail che riceve il lunedi' (REB-221), "
        "scelta dal suo profilo: un agente non decide che cosa arriva nella casella di "
        "chi lo ha collegato"
    ),
    ("UserService", "list"): "l'anagrafica utenti non serve a nessun tool",
    ("UserService", "authenticate"): "e' il passo di login, non un'operazione",
}


# 4. Configuration. These change the *shape* of the CRM -- which stages exist, which
#    custom fields exist, which templates exist, who the issuer is -- rather than its
#    content. The line is the same one slice 4 §11 already drew for cost categories and
#    rates: an agent records and reads within a configuration, and a person decides what
#    the configuration is. Exposing them would also mean an agent could rewrite the field
#    definitions its own `describe_schema` output is derived from.
_CONFIGURAZIONE: dict[Method, str] = {
    ("SpaceSettingsService", "read"): (
        "Impostazioni → Spazio: le variabili che uno spazio decide per se' (client Google, "
        "storage, accesso completo dell'agente, soglie dei solleciti). Configurazione "
        "dell'installazione, e per di piu' cio' che decide *se* un agente ha accesso "
        "completo: non puo' essere l'agente a leggerla o a cambiarla"
    ),
    ("SpaceSettingsService", "update"): (
        "stessa ragione di `read`, al quadrato: scrive `mcp_full_access` e il client Google"
    ),
    ("SpaceSettingsService", "overrides"): (
        "lettura interna: e' cio' che `deps.get_request_settings` posa sopra l'ambiente a "
        "ogni richiesta, prima che esista un actor"
    ),
    ("SpaceSettingsService", "effective"): "come `overrides`, gia' applicate a `Settings`",
    ("AutomationConfigService", "update_automation_config"): (
        "decide che cosa il CRM fa **da solo** ai dati futuri, senza nessuno nel mezzo: "
        "e' la stessa famiglia di `PipelineService.update`, un grado piu' seria. Un agente "
        "che potesse spegnere A1 e poi spostare un deal non lascerebbe traccia della "
        "differenza fra «l'automazione non e' scattata» e «l'ho disattivata io». "
        "L'esclusione non e' cautela di questo file: e' l'unico nome di "
        "`MCP_EXCLUDED_SLICE6` (packages/core/tests/test_architecture.py), che la spec "
        "§11.1 fissa a esattamente uno, e la lettura corrispondente -- "
        "`describe_automations` -- ha un tool, quindi all'agente resta visibile tutto "
        "quello che il sistema fa per conto suo. Il divieto e' strutturale e non un "
        "controllo di permesso perche' residuo R10 lascia un PAT con il ruolo pieno del "
        "proprietario: il token di un admin passerebbe qualunque check"
    ),
    ("CostCategoryService", "seed_defaults"): "installa le categorie iniziali",
    ("FieldDefinitionService", "create"): "definisce lo schema, non lo popola",
    ("FieldDefinitionService", "update"): "definisce lo schema, non lo popola",
    ("FieldDefinitionService", "archive"): "definisce lo schema, non lo popola",
    ("FieldDefinitionService", "unarchive"): "definisce lo schema, non lo popola",
    ("FieldDefinitionService", "list"): "describe_schema espone gia' i campi vivi",
    ("PipelineService", "create"): "la forma della pipeline e' una decisione umana",
    ("PipelineService", "update"): "la forma della pipeline e' una decisione umana",
    ("PipelineService", "delete"): "la forma della pipeline e' una decisione umana",
    ("PipelineService", "seed_defaults"): "installa la pipeline iniziale",
    ("TemplateService", "create"): "i template sono configurazione documentale",
    ("TemplateService", "update"): "i template sono configurazione documentale",
    ("TemplateService", "activate"): "i template sono configurazione documentale",
    ("TemplateService", "deactivate"): "i template sono configurazione documentale",
    ("TemplateService", "seed_defaults"): "installa i template iniziali",
}


# 5. Bytes. The MCP surface returns identifiers and URLs, never file content (spec slice
#    2 §7): an agent that could pull a PDF or an XLSX down a tool channel would be moving
#    the artefact out of the storage layer that versions and audits it.
_BYTE: dict[Method, str] = {
    ("DocumentService", "download"): "l'MCP restituisce URL, mai byte",
    ("InvoiceService", "download"): "l'MCP restituisce URL, mai byte",
    ("TimeReportService", "build_xlsx"): "l'MCP restituisce URL, mai byte",
    ("TimeReportService", "render_pdf"): "l'MCP restituisce URL, mai byte",
}


# 6. Reached under another name, or reserved for the person. The residue: operations an
#    existing tool already covers through a different service method, and the handful
#    that are deliberately the human's half of a two-step. They are not forbidden -- a
#    future slice could expose any of them -- which is exactly why they are not in the
#    ban list and why each has to say so out loud.
#
#    Ten entries have left this block, and they are the reason it must stay small.
#    `PeriodLockService.list_locks`, `TemplateService.preview`,
#    `DocumentService.regenerate`, `soft_delete` and `restore` were all recorded here as
#    "non ha ancora un tool" / "e' una decisione della persona" -- and each turned out to
#    be a plain read, or a reversible audited write whose inverse this surface already
#    exposes for customers, deals, people, costs and time entries. `EmailDraftService.
#    create` and `SollecitiService.candidates` left the same way at B2-10, as
#    `draft_email` and `list_payment_reminder_candidates`. `InvoiceService.soft_delete`
#    left at ORB-37 as `discard_proforma`: "a person's decision" had described a draft
#    the same surface already creates and rewrites line by line, and the service plus
#    the table CHECK refuse the delete for anything that consumed a number. It is the
#    one removal on this surface without an inverse, and it is allowed where
#    `EmailDraftService.delete` below is not because nothing is lost with it: a proforma
#    never took a number, its reference sequence tolerates a gap, and `get_invoice`
#    showed everything needed to recreate it. `InvoiceService.update` left at ORB-61 as
#    `update_proforma`: its reason here was "note interne e campi custom, congelati dopo
#    l'emissione: nessuna audience agentica", and it was true of the two fields the
#    method carried when it was written. The method now also moves a proforma's own
#    document date (ORB-63) and its accrual period, the two header facts an agent that
#    composes "la proforma di agosto" has to be able to state, and both are frozen by the
#    service itself once the proforma is consumed. The tool is guarded like
#    `replace_proforma_lines` (a proforma only, never a fattura) and passes only the keys
#    it was given, so the notes and the custom fields the old reason named are still not
#    reachable through it. `InvoiceService.confirm_proforma` left at ORB-132 as
#    `confirm_proforma`: "l'agente prepara, la persona conferma" was the last place this
#    surface stopped an agent short of a proforma it had itself created, rewritten line
#    by line and rendered, and Ivan asked for the whole path on 2026-09-10. Confirming
#    consumes nothing and forecloses nothing: a confirmed proforma stays editable
#    (`_is_editable`) and discardable, so it sits on the default surface with the other
#    proforma tools; the step that does consume a number, `issue_invoice`, is untouched
#    and stays behind `mcp_full_access`.
#    The decision is a row in `docs/design/DECISIONS.md`. A reason that only says
#    "nobody wrote the tool" is a
#    placeholder wearing the clothes of a decision; this category is for the ones that
#    survive being asked why, and the only way to keep that true is to delete the ones
#    that do not the moment the tool is written.
_COPERTE_O_UMANE: dict[Method, str] = {
    ("DigestService", "build"): (
        "compone il resoconto settimanale che `pigrocrm digest` manda per mail il lunedi' "
        "(REB-221): ogni numero che contiene e' gia' un tool o una resource (i tre "
        "cruscotti, le fatture, le ore, la pipeline), e un agente che vuole la settimana "
        "la legge da li' invece di ricevere una pagina di prosa"
    ),
    ("AnalyticsService", "economic_overview"): (
        "la scheda economica della dashboard porta con se' la stima fiscale calcolata su "
        "incassato e proiettato: valgono le ragioni di `get_fiscal_estimate`, che non e' "
        "e non sara' un tool. Raggiungibile solo da `GET /api/analytics/overview`"
    ),
    ("AnalyticsService", "cash_overview"): (
        "la meta' senza fisco della stessa scheda (incassato, da incassare, bozze, costi, "
        "per mese): un agente ha gia' `get_economic_dashboard` e `get_period_pnl` per le "
        "cifre riportabili, e questa vista di cassa esiste per i grafici della pagina"
    ),
    ("TenantService", "provision"): (
        "e' la persona che crea il *proprio* spazio dal login pubblico (spec 2026-09-08): "
        "una scelta su di se', come l'iscrizione a Orbiters, non un'operazione che un "
        "agente compie per conto del titolare. Il registro degli spazi e' un database di "
        "servizio, non il CRM, e l'unico adapter che vi arriva e' `POST /api/tenants`"
    ),
    ("TenantService", "availability"): (
        "la domanda che la pagina di iscrizione fa mentre la persona scrive il nome: senza "
        "actor, sul registro degli spazi, non sul CRM"
    ),
    ("TenantService", "get"): (
        "lettura interna del registro: e' come `deps.py` traduce il prefisso dell'URL nel "
        "database dello spazio, prima che esista un actor"
    ),
    ("TenantService", "list"): (
        "the registry of spaces read by another product, the Orbiters hub, through "
        "`GET /api/tenants/` with `PIGROCRM_REGISTRY_TOKEN` (ORB-142): the installation's "
        "spaces are not the titolare's CRM data, and an agent acting in one space has no "
        "business listing everybody else's"
    ),
    ("TenantService", "count_for_owner"): (
        "il registro degli spazi non e' superficie di uno spazio: lo legge la registrazione "
        "(`POST /api/tenants/member`, ORB-173) per dire a chi torna che ha gia' aperto degli "
        "spazi, prima che esista un actor; un agente dentro uno spazio non ha motivo di leggerlo"
    ),
    ("DocumentService", "create"): "create_document_from_template e' l'unica creazione "
    "che non richieda di caricare byte",
    ("DocumentService", "update"): "titolo e campi custom: nessun agente ha motivo di "
    "riscriverli su un documento gia' reso",
    ("DocumentService", "add_version"): "richiede byte gia' resi, che l'MCP non produce",
    ("FiscalProfileService", "get"): "describe_fiscal_profile espone gia' il regime",
    ("TemplateService", "get"): "describe_template espone gia' il template",
    # Le cinque righe qui sotto sono cio' che resta di 5B-2 dopo che **B2-10 ha deciso
    # la superficie MCP di questa fetta**, come B1-14 aveva deciso quella di 5B-1.
    # `EmailDraftService.create` e `SollecitiService.candidates` erano qui e non ci sono
    # piu': sono `draft_email` e `list_payment_reminder_candidates`. La riga di
    # separazione non e' lettura contro scrittura, ed e' bene dirla per intero perche' e'
    # l'unica cosa che tiene insieme le esclusioni rimaste:
    #
    #   * si espone la *preparazione* di un testo neutro che qualcuno ha chiesto. La
    #     bozza e' inerte -- nessuno strumento di questa superficie puo' spedirla -- e la
    #     meta' mancante e' una persona che la legge prima che parta. Che sia meta'
    #     operazione e' il progetto: la revisione e' cio' che rende accettabile l'invio, e
    #     una bozza e' l'artefatto che la rende economica;
    #   * non si espone niente che *tocchi una bozza che l'agente non ha scritto*, e
    #     niente che scriva una richiesta di denaro a nome del titolare.
    ("EmailDraftService", "update"): "riscrivere una bozza che l'agente non ha scritto "
    "romperebbe l'unica garanzia su cui poggia `draft_email`: che il testo che parte sia "
    "il testo che una persona ha letto. Un agente che potesse riscrivere una bozza gia' "
    "rivista, fra la revisione e Invia, renderebbe la revisione una formalita' -- e la "
    "revisione e' tutta la sicurezza di questa meta' della superficie. Non e' cautela: e' "
    "che `draft_email` restituisce gia' cio' che ha scritto, quindi l'unica capacita' che "
    "`update` aggiungerebbe e' proprio quella che va negata",
    ("EmailDraftService", "get"): "leggere una bozza che l'agente non ha scritto e' "
    "leggere corrispondenza privata non ancora partita. `draft_email` restituisce id, "
    "oggetto e stato di cio' che ha appena preparato, quindi non c'e' niente che un "
    "agente debba rileggere per lavorare; `list_gmail_messages` espone la corrispondenza "
    "gia' archiviata, che e' l'altra meta' di cui ha bisogno",
    ("EmailDraftService", "delete"): "cancellare il testo non spedito di qualcuno e' "
    "una decisione della persona, e non ha inverso: la riga sparisce davvero. E' l'unica "
    "operazione di questa fetta che distrugge qualcosa senza lasciare traccia da cui "
    "tornare indietro",
    ("EmailDraftService", "list"): "e' l'indice della corrispondenza privata non ancora "
    "partita di tutti -- inclusi i solleciti, che hanno l'IBAN del titolare dentro. Un "
    "agente non ne ha bisogno per preparare una bozza, che e' l'unica scrittura che gli "
    "e' concessa qui",
    ("SollecitiService", "create_reminder"): "**preparare un sollecito non e' preparare "
    "un'email**, ed e' qui che passa la riga fra questa esclusione e `draft_email`. Il "
    "testo non e' neutro: e' una richiesta di denaro a nome del titolare, con il suo IBAN "
    "dentro. E prepararlo *consuma* una delle tre posizioni che il registro concede per "
    "fattura (spec 7.3): un agente che preparasse solleciti spenderebbe il tetto che "
    "impedisce a una fattura contestata di diventare una persecuzione automatica, senza "
    "che nessuno abbia deciso di sollecitare quella fattura. Elencare cosa si potrebbe "
    "sollecitare non e' una decisione, e infatti `list_payment_reminder_candidates` "
    "esiste; prepararlo lo e'",
    # REB-362 lands the real "agents propose, humans confirm" surface for the day
    # lifecycle: `propose_day`/`propose_contract` write a `proposals` row,
    # `accept_proposal` is what actually creates the `approval`+`work_unit` pair (or
    # the `contract`+`rate_card` pair), inside one transaction with the proposal's
    # own decision. These four methods stay unreachable on purpose, not because
    # nobody wrote a tool: exposing any of them directly would let an agent write
    # the state-machine row, or manufacture approval evidence, with no proposal and
    # no human decision in between -- exactly what invariant 3 forbids. This
    # replaces the provisional `_IN_ATTESA_DI_REB_362` block below (deleted, not
    # updated, the same discipline `_IN_ATTESA_DI_DECISIONE`/`_IN_ATTESA_DI_DRIVE_T7`
    # were held to).
    ("WorkUnitService", "create"): (
        "would let an agent write a work_unit directly, skipping the proposal a "
        "human has to confirm first (spec §0's invariant 3); `accept_proposal`'s "
        "'giornata' branch is the only legitimate caller, and it writes through "
        "WorkUnitRepository, not this method, so the accept and the approval it "
        "pairs with stay one transaction"
    ),
    ("WorkUnitService", "transition"): (
        "moving a day through the state graph directly is exactly what invariant 3 "
        "denies an agent; nothing in this codebase calls it outside its own tests -- "
        "a future human-facing (non-agent) UI action is the only plausible caller, "
        "and it does not exist yet"
    ),
    ("WorkUnitService", "link_approval"): (
        "same reason as `transition`: linking an approval outside `accept_proposal`'s "
        "own atomic write would let an agent recover a flagged day, or attach "
        "evidence to one, with no proposal behind it"
    ),
    ("ApprovalService", "create"): (
        "recording an approval detached from the day it evidences is exactly what "
        "`accept_proposal`'s 'giornata' branch already does atomically with the "
        "work_unit; a standalone tool would let an agent manufacture approval "
        "evidence with no day and no human review attached"
    ),
}


# Nessuna settima categoria di esclusioni *definitive*. Il task A8 ne aveva aperta una --
# `_IN_ATTESA_DI_DECISIONE`, una sola voce, `SearchService.search_everything` --
# dichiarandola provvisoria e scrivendo che A11 avrebbe dovuto **cancellarla**, non
# aggiornarla. A11 ha registrato il tool `search_everything` (`tools/__init__.py`, sezione
# "shared"), quindi il metodo e' ora raggiungibile e la riga e' sparita insieme alla sua
# categoria. Se fosse rimasta, `test_no_declared_exclusion_is_actually_reachable` sarebbe
# diventato rosso: e' quel test a rendere impossibile lasciare qui un'esclusione che non
# esclude piu' niente.
#
# Vale la pena dirlo perche' una categoria vuota lasciata in piedi "per il prossimo che
# serve" e' esattamente il posto dove una decisione non presa si nasconde: l'elenco qui
# sopra contiene solo scelte definitive, e chi ne aggiunge una provvisoria deve riaprire
# un blocco a se' con il proprio nome, non riempire uno gia' pronto.
#
# Il task 4 di questa stessa fetta (9B) aveva aperto un blocco cosi', `_IN_ATTESA_
# DI_DRIVE_T7`, con una sola voce, `("GoogleDriveAccountService", "health")`: la
# scrittura di `health` esisteva gia' ma il tool che la rende raggiungibile da un
# agente, `describe_drive_account`, non ancora. Il task 7 (`tools/drive.py`) ha
# registrato quel tool, quindi il metodo e' ora raggiungibile e la riga e' sparita
# insieme alla sua categoria -- la stessa cancellazione, non aggiornamento, che A11
# ha applicato a `_IN_ATTESA_DI_DECISIONE`.
# Provisional, like `_IN_ATTESA_DI_DECISIONE`/`_IN_ATTESA_DI_DRIVE_T7` before it, REB-359
# left one: `_IN_ATTESA_DI_REB_362`, seven entries on `WorkUnitService`/`ApprovalService`,
# declaring that REB-362 would either register a tool for each or say why not, and that
# it must **delete** the block, not update it, the same discipline as the two above.
# REB-362 registered `propose_contract`, `propose_day`, `get_proposal`, `list_proposals`,
# `accept_proposal` and `reject_proposal` (`tools/__init__.py`, section "proposals"). Of
# the seven: `WorkUnitService.get`/`transitions_for` and `ApprovalService.get` take no
# `actor` and moved to `_INTERNE` above, next to every other no-actor read; the four
# writes (`WorkUnitService.create`/`transition`/`link_approval`,
# `ApprovalService.create`) moved to `_COPERTE_O_UMANE` above, because REB-362's own
# answer to "why no tool" is a permanent one, not "nobody wrote it yet" -- exposing any
# of them would let an agent write the ledger directly, which is exactly what invariant
# 3 forbids. So the block is gone, the same cancellazione as `_IN_ATTESA_DI_DECISIONE`.


ESCLUSIONI: dict[Method, str] = {
    **_VIETATE,
    **_INTERNE,
    **_CREDENZIALI,
    **_CONFIGURAZIONE,
    **_BYTE,
    **_COPERTE_O_UMANE,
}


# --- the sweep ---------------------------------------------------------------------


def _service_methods() -> dict[str, list[str]]:
    """Every `*Service` class under `packages/core`, with its public methods.

    The naming convention is the enumeration: a class ending in `Service` is a domain
    service by this codebase's own rule, and every adapter reaches the domain through
    one. Methods starting with `_` are private by the same convention and are not part
    of anybody's surface.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and node.name.endswith("Service")):
                continue
            found[node.name] = sorted(
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and not member.name.startswith("_")
            )
    return found


def _produced_by(func: ast.expr, known: set[str], factories: dict[str, str]) -> str | None:
    """Which service class a call expression yields: the class itself when the callee is
    one, and the class a module-private factory declares as its return type otherwise."""
    if not isinstance(func, ast.Name):
        return None
    return func.id if func.id in known else factories.get(func.id)


def _reachable(known: set[str]) -> set[Method]:
    """Every `(service class, method)` an agent can reach through `tools/` or
    `resources/`.

    Resolves the receiver rather than matching the method name, which is what makes the
    answer usable: the three call shapes this package actually uses are
    `ServiceClass(context.session).m(...)`, `svc = ServiceClass(...)` followed by
    `svc.m(...)`, and the module-private factories `_invoices(context)` /
    `_documents(context)` whose return annotation names the class. A name-only scan
    would report `CustomerService.create` as covered because *some* `.create(` exists
    somewhere under `tools/`, which is precisely the false green this file exists to
    avoid.
    """
    found: set[Method] = set()
    for base in (TOOLS_DIR, RESOURCES_DIR):
        for path in sorted(base.rglob("*.py")):
            if path in PRIVILEGED_MODULES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            factories: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    returns = node.returns
                    if isinstance(returns, ast.Name) and returns.id in known:
                        factories[node.name] = returns.id

            bindings: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    produced = _produced_by(node.value.func, known, factories)
                    if produced is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                bindings[target.id] = produced

            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                receiver = node.func.value
                owner = None
                if isinstance(receiver, ast.Call):
                    owner = _produced_by(receiver.func, known, factories)
                elif isinstance(receiver, ast.Name):
                    owner = bindings.get(receiver.id)
                if owner is not None:
                    found.add((owner, node.func.attr))
    return found


SERVICES = _service_methods()
REACHABLE = _reachable(set(SERVICES))
PUBLIC_METHODS = [(cls, method) for cls, methods in SERVICES.items() for method in methods]


# --- guards on the guard -----------------------------------------------------------
#
# Both halves of this file are AST walks over paths built from `__file__`. Either one
# returning an empty set would make every assertion below pass while checking nothing,
# and an empty set is exactly what a moved directory or a renamed convention produces.


def test_the_service_sweep_finds_the_services_it_should() -> None:
    assert {"CustomerService", "InvoiceService", "TimeEntryService"} <= set(SERVICES)
    assert len(SERVICES) > 15
    assert "create" in SERVICES["CustomerService"]
    # Private helpers stay out: `_check_owner` is not part of anybody's surface.
    assert not [m for methods in SERVICES.values() for m in methods if m.startswith("_")]


def test_the_reachability_walk_resolves_all_three_call_shapes() -> None:
    """One assertion per shape, named after the module that uses it. If the resolver
    silently stopped understanding the factory shape, every method of `InvoiceService`
    and `DocumentService` would look unexposed and this file would demand exclusions for
    operations that have working tools."""
    assert ("CustomerService", "create") in REACHABLE  # ServiceClass(...).m(...)
    assert ("CustomerService", "list") in REACHABLE  # svc = ServiceClass(...); svc.m()
    assert ("InvoiceService", "create") in REACHABLE  # _invoices(context).m(...)
    assert ("PipelineService", "get") in REACHABLE  # reached only from resources/


def test_the_walk_attributes_a_call_to_its_own_receiver() -> None:
    """The property that separates this from a substring scan. `.create(` appears under
    `tools/` for customers, people, deals, costs, time entries and invoices -- and for
    none of the four services below, each of which defines a `create` of its own. A
    name-only scan would report all four as covered."""
    for service in ("UserService", "PatService", "PipelineService", "FieldDefinitionService"):
        assert "create" in SERVICES[service]
        assert (service, "create") not in REACHABLE


# --- the claim ---------------------------------------------------------------------


@pytest.mark.parametrize(("service", "method"), PUBLIC_METHODS, ids=lambda p: p)
def test_every_public_service_method_is_a_tool_or_a_named_exclusion(
    service: str, method: str
) -> None:
    """The half of the product's claim that had no test. A new service method fails here
    until it has a tool or a reason -- which is the only moment at which writing the
    reason is cheap."""
    assert (service, method) in REACHABLE or (service, method) in ESCLUSIONI, (
        f"'{service}.{method}' non e' raggiungibile da nessun tool o resource MCP e non "
        "compare fra le esclusioni dichiarate in questo file. Il prodotto promette che "
        "un agente possa fare tutto quello che puo' fare una persona, meno un elenco "
        "deliberato: o esiste un tool, o esiste una riga che dice perche' no. Scegliere "
        "la categoria e' la decisione; lasciarla implicita e' il buco."
    )


@pytest.mark.parametrize(("service", "method"), sorted(ESCLUSIONI), ids=lambda p: p)
def test_every_declared_exclusion_still_names_a_real_method(service: str, method: str) -> None:
    """The other direction, which is what keeps the taxonomy from becoming folklore: a
    renamed or deleted service method must not leave a reason behind explaining why
    something that no longer exists is not exposed."""
    assert service in SERVICES, f"'{service}' non e' piu' un servizio di packages/core"
    assert method in SERVICES[service], (
        f"'{service}.{method}' non esiste piu': l'esclusione va rimossa insieme al metodo"
    )


@pytest.mark.parametrize(("service", "method"), sorted(ESCLUSIONI), ids=lambda p: p)
def test_no_declared_exclusion_is_actually_reachable(service: str, method: str) -> None:
    """An exclusion that is reachable is a false statement, and the most expensive kind:
    it reads as a considered refusal while the operation is in fact exposed. This is what
    catches somebody adding a tool for a method and forgetting the table -- including,
    and especially, one of the forbidden ones."""
    assert (service, method) not in REACHABLE, (
        f"'{service}.{method}' e' dichiarato non esposto ma un tool o una resource lo "
        "chiama davvero"
    )


def test_the_forbidden_block_names_exactly_what_the_ban_test_bans() -> None:
    """The two files must not drift. `test_mcp_invoice_ban.py` owns the policy; this file
    re-states it as a category so that the arithmetic of the sweep adds up, and
    re-stating a list is how lists diverge. Compared the way each half of the ban is
    written: the bare-name bans on names, and the qualified ones as the `(service,
    method)` pairs they are -- comparing those on the name alone would accept
    `EmitterProfileService.upsert` standing in for `FiscalProfileService.upsert`, which
    is the exact confusion the qualified list exists to prevent.

    `_FUORI_DAL_SETACCIO` counts on the same side of the equality as `_VIETATE`, which
    is what keeps it from becoming a place to hide a ban: a name moved there to avoid
    writing a category still has to appear in `FORBIDDEN_SERVICE_CALLS`, and
    `test_nothing_hides_in_the_out_of_sweep_block` checks its receivers really are
    outside the sweep rather than merely declared to be."""
    dichiarate = {method for _, method in _VIETATE} | {method for _, method in _FUORI_DAL_SETACCIO}
    assert dichiarate == set(FORBIDDEN_SERVICE_CALLS) | {
        method for _, method in FORBIDDEN_QUALIFIED_CALLS
    }
    assert set(FORBIDDEN_QUALIFIED_CALLS) <= set(_VIETATE)


def test_nothing_hides_in_the_out_of_sweep_block() -> None:
    """`_FUORI_DAL_SETACCIO` is an escape hatch, so it needs a lock.

    Its whole justification is that the receiver is not a class this file's inventory
    can enumerate. A `*Service` method listed there would be excluded from the
    taxonomy's arithmetic *and* from
    `test_every_declared_exclusion_still_names_a_real_method`'s check that the method
    still exists -- a reason with nothing holding it to reality, which is precisely what
    the six categories above were built to avoid.
    """
    for service, method in _FUORI_DAL_SETACCIO:
        assert service not in SERVICES, (
            f"'{service}' e' un servizio di packages/core: la sua esclusione va in una "
            "delle sei categorie sopra, dove il metodo viene verificato ancora esistente"
        )
        assert (service, method) not in ESCLUSIONI
    # And the block is not vacuous. That each name is a real method of a real class is
    # already proved elsewhere and better: `test_mcp_invoice_ban.py`'s
    # `test_the_privileged_module_is_the_only_place_they_appear` requires every one of
    # them to be *called* from a privileged module, which a deleted or renamed method
    # cannot be. This file stays a pure AST walk with no import from `packages/core`.
    assert ("DriveReader", "read_text") in _FUORI_DAL_SETACCIO


def test_no_qualified_ban_is_reachable_from_a_resource_either() -> None:
    """The same extension for the qualified half, asserted on the pair: a resource that
    called a banned `(service, method)` would be as agent-reachable as a tool that did,
    while the ban test's own scan reads `tools/` only. A loop, not a parametrisation:
    the tuple is empty since ORB-188 and must assert nothing rather than skip."""
    for service, method in FORBIDDEN_QUALIFIED_CALLS:
        assert (service, method) not in REACHABLE, (
            f"'{service}.{method}' e' raggiungibile attraverso tools/ o resources/, ma e' "
            "una delle operazioni escluse per costruzione dalla superficie MCP"
        )


@pytest.mark.parametrize("method", sorted(set(FORBIDDEN_SERVICE_CALLS)))
def test_no_forbidden_operation_is_reachable_from_a_resource_either(method: str) -> None:
    """Extends the ban's guarantee by the one directory the ban test does not read.
    `test_mcp_invoice_ban.py` scans `tools/` only, which is right for its own question --
    `@mcp.tool()` decorators live nowhere else -- but a resource template that called
    `.issue(...)` would be just as reachable by an agent while leaving no trace under
    `tools/` at all. `REACHABLE` covers both directories, so asserting against it closes
    that gap without duplicating the ban."""
    offenders = [service for service, reached in REACHABLE if reached == method]
    assert not offenders, (
        f"'.{method}(' e' raggiungibile da {offenders} attraverso tools/ o resources/, "
        "ma e' una delle operazioni escluse per costruzione dalla superficie MCP"
    )
