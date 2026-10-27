"""Members, aziende (the emitters) and the fiscal profile."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.auth.service import UserService
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    invoices,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # The fiscal profile and the emitter are written here, on the default surface
    # (ORB-188, 2026-09-12). The line this surface draws is not "reads yes, writes no":
    # it is "what cannot be undone stays behind the switch". A profile or an emitter is
    # one rewritable row; an issued invoice keeps its own copy of both. A space is born
    # empty and the first thing a person asks the assistant they just connected is to
    # set them, so `update_fiscal_profile` and `update_azienda` sit next to their reads,
    # admin-only through the services' own `require_admin`.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_members() -> list[dict[str, Any]]:
        """Le persone attive dello spazio: `id`, `nome`, `ruolo` e `email` (solo per un
        amministratore, altrimenti `null`). Serve a trovare l'id per `owner_id` (clienti e deal)
        e `assegnata_a` (attività).
        """
        directory = UserService(context.session).directory(context.actor)
        return [member.model_dump(mode="json") for member in directory]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_aziende() -> list[dict[str, Any]]:
        """Le aziende dello spazio, la predefinita per prima: id, nome breve, ragione sociale,
        partita IVA, nazione. Con una sola azienda, ogni tool con `azienda_id` la usa da sé.
        """
        return invoices.list_aziende(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_fiscal_profile(azienda_id: str | None = None) -> dict[str, Any]:
        """Il regime fiscale e i parametri che decidono aliquote, natura e bollo di un'azienda;
        `azienda_id` omesso: predefinita. Utile per capire perché una riga ha una certa IVA.
        """
        return invoices.describe_fiscal_profile(context, azienda_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_azienda(azienda_id: str | None = None) -> dict[str, Any]:
        """Chi emette: ragione sociale, partita IVA, indirizzo e recapiti dell'intestazione di
        fatture e documenti; `azienda_id` omesso: predefinita. Leggilo invece di chiedere questi
        dati all'utente.
        """
        return invoices.describe_azienda(context, azienda_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_fiscal_profile(
        dati: dict[str, Any], azienda_id: str | None = None
    ) -> dict[str, Any]:
        """Riscrive il profilo fiscale di un'azienda: **sostituzione totale**, ogni chiave assente
        torna al default. `azienda_id` omesso: predefinita. Decide aliquota, natura, bollo e
        riferimento normativo delle fatture future, non delle già emesse. Leggi prima
        `describe_fiscal_profile`, rimanda l'oggetto intero con le modifiche e chiedi conferma
        alla persona sul riepilogo. Solo un admin.

        `dati`: `pack_id` (`it-flat-rate` default, forfettario con `codice_regime` `RF19`;
        `it-standard`, ordinario con `RF01` e IVA 22% se non indichi altro; `non-it`, azienda
        estera senza `codice_regime`, bollo né parametri del forfettario (lasciali fuori); un
        codice di un altro pacchetto è rifiutato), `coefficiente_redditivita`,
        `aliquota_imposta_sostitutiva` e `aliquota_inps` in percentuale (solo forfettario),
        `modalita_pagamento` (codice SdI, `MP05` bonifico), `giorni_scadenza`, `iban`, parametri
        IVA e bollo.
        """
        return invoices.update_fiscal_profile(context, dati, azienda_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_azienda(dati: dict[str, Any], profilo_fiscale: dict[str, Any]) -> dict[str, Any]:
        """Apre una seconda azienda con il suo profilo fiscale in una chiamata: i clienti si
        possono assegnare a lei, le sue fatture hanno un registro proprio e i documenti la sua
        intestazione. Solo un admin, dopo la conferma dei dati: emessa una fattura, un'azienda
        non si cancella più, si disattiva. Non diventa la predefinita, salvo che lo spazio non
        ne abbia una.

        `dati` come `update_azienda` con `nome` obbligatorio (nome breve per selettore e liste);
        `profilo_fiscale` come `update_fiscal_profile` (`non-it` con IVA zero richiede
        `natura_default`). Un profilo rifiutato fa rifiutare tutta la chiamata. Un dato fiscale non
        fornito non si inventa: chiedilo.
        """
        return invoices.create_azienda(context, dati, profilo_fiscale)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_azienda(dati: dict[str, Any], azienda_id: str | None = None) -> dict[str, Any]:
        """Scrive chi emette: l'intestazione di offerte e fatture di un'azienda. `azienda_id`
        omesso: predefinita, creata se manca.

        **Sostituzione totale**: ogni chiave assente torna vuota (`nazione` a `IT`;
        `ragione_sociale` obbligatoria). Leggi prima `describe_azienda` e rimanda l'oggetto con le
        sole modifiche. Chiavi: `nome`, `ragione_sociale`, `partita_iva` (11 cifre) o
        `codice_fiscale`, `indirizzo`, `cap`, `comune`, `provincia`, `nazione`, `pec`, `codice_sdi`
        (7 caratteri), `telefono`, `email`, `sito_web`, `regime_fiscale` (testo in calce),
        `firma_email`, `logo_key`, `firma_key` (chiavi di storage, non byte). Chiedi conferma alla
        persona sul riepilogo; un dato fiscale non fornito non si inventa. Solo un admin.
        """
        return invoices.update_azienda(context, dati, azienda_id)
