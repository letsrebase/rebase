"""What an agent proposes from an archived document: contracts and days."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.proposals.schemas import ProposalListQuery
from pigrocrm_mcp.annotations import ADDITIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    proposals,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    Confidenza,
    IsoDateStr,
    MoneyArg,
    OptionalMoney,
    QuantitaArg,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- proposals (REB-362) --------------------------------------------------
    #
    # "agents propose, humans confirm" (spec §0). `propose_contract`/`propose_day`
    # are the *only* write path from an agent onto a document already archived by
    # any door (`import_drive_file`, a direct upload, or a mail attachment archived
    # the same way) -- they write a `proposals` row, never a `Contract`/`WorkUnit`
    # directly. Accepting and rejecting are the confirm half and are NOT tools
    # (REB-806): the same agent that read the untrusted mail would otherwise propose
    # and decide in one turn. They live on the REST/web surface, as a person's act;
    # `Actor.require_person` refuses any agent credential there too.

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def propose_contract(
        document_id: str,
        estratto: str,
        confidenza: Confidenza,
        customer_id: str,
        titolo: str,
        inizio: str,
        tipo_rinnovo: str,
        preavviso_disdetta_giorni: int,
        cadenza_fatturazione: str,
        politica_spese: dict[str, Any],
        rate_card_valido_da: str,
        rate_card_tipo: str,
        rate_card_importo: MoneyArg,
        rate_card_unita: str,
        fine: IsoDateStr = None,
        preavviso_rinnovo_giorni: int | None = None,
        giorni_pagamento: int | None = None,
        pagamento_fine_mese: bool | None = None,
        divisa: str = "EUR",
        requires_prior_approval: bool = False,
        applies_social_charge: bool = False,
        note: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        rate_card_valido_a: IsoDateStr = None,
        rate_card_frazioni_ammesse: list[float] | None = None,
        rate_card_ore_minime: OptionalMoney = None,
        rate_card_periodo_erogazione: str | None = None,
        tipo_estratto: str = "citato",
        motivo_confidenza: str | None = None,
    ) -> dict[str, Any]:
        """Propone un contratto letto da un documento già archiviato: crea solo una proposta in
        attesa, che una persona decide dall'app. `document_id` è il documento da cui `estratto`
        è stato letto verbatim (`tipo_estratto='citato'`) o trascritto da una scansione
        (`'trascritto'`). Gli altri campi sono quelli di `create_contract`, più la prima scheda
        tariffaria (`rate_card_*`, come `create_rate_card`). Nulla viene creato finché la
        proposta non è accettata.
        """
        rate_card: dict[str, Any] = {
            "valido_da": rate_card_valido_da,
            "valido_a": rate_card_valido_a,
            "tipo": rate_card_tipo,
            "importo": rate_card_importo,
            "unita": rate_card_unita,
            "ore_minime": rate_card_ore_minime,
            "periodo_erogazione": rate_card_periodo_erogazione,
        }
        if rate_card_frazioni_ammesse is not None:
            rate_card["frazioni_ammesse"] = rate_card_frazioni_ammesse
        return proposals.propose(
            context,
            {
                "document_id": UUID(document_id),
                "target_type": "contratto",
                "estratto": estratto,
                "tipo_estratto": tipo_estratto,
                "confidenza": confidenza,
                "motivo_confidenza": motivo_confidenza,
                "campi_proposti": {
                    "contract": {
                        "customer_id": customer_id,
                        "titolo": titolo,
                        "inizio": inizio,
                        "fine": fine,
                        "tipo_rinnovo": tipo_rinnovo,
                        "preavviso_rinnovo_giorni": preavviso_rinnovo_giorni,
                        "preavviso_disdetta_giorni": preavviso_disdetta_giorni,
                        "giorni_pagamento": giorni_pagamento,
                        "pagamento_fine_mese": pagamento_fine_mese,
                        "cadenza_fatturazione": cadenza_fatturazione,
                        "divisa": divisa,
                        "requires_prior_approval": requires_prior_approval,
                        "applies_social_charge": applies_social_charge,
                        "politica_spese": politica_spese,
                        "note": note,
                        "custom_fields": custom_fields or {},
                    },
                    "rate_card": rate_card,
                },
            },
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def propose_day(
        document_id: str,
        contract_id: str,
        estratto: str,
        confidenza: Confidenza,
        data: str,
        quantita: QuantitaArg,
        descrizione: str,
        canale: str,
        mittente: str,
        ricevuto_il: str,
        message_id: str | None = None,
        tipo_estratto: str = "citato",
        motivo_confidenza: str | None = None,
    ) -> dict[str, Any]:
        """Propone una giornata letta da un'email o da un documento di approvazione già archiviato:
        crea solo una proposta in attesa, che una persona decide dall'app. `contract_id` è il
        contratto. `canale`, `mittente`, `ricevuto_il` e `message_id` sono l'evidenza
        dell'approvazione, registrata insieme alla giornata all'accettazione.
        """
        return proposals.propose(
            context,
            {
                "document_id": UUID(document_id),
                "contract_id": UUID(contract_id),
                "target_type": "giornata",
                "estratto": estratto,
                "tipo_estratto": tipo_estratto,
                "confidenza": confidenza,
                "motivo_confidenza": motivo_confidenza,
                "campi_proposti": {
                    "contract_id": contract_id,
                    "data": data,
                    "quantita": quantita,
                    "descrizione": descrizione,
                    "approvazione": {
                        "canale": canale,
                        "mittente": mittente,
                        "ricevuto_il": ricevuto_il,
                        "message_id": message_id,
                    },
                },
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        """Legge una proposta: campi proposti, estratto, stato ed esito se decisa
        (`campi_accettati`, `id_risultato`, `deciso_da`, `deciso_il`).
        """
        return proposals.get(context, proposal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_proposals(
        stato: str | None = None,
        target_type: str | None = None,
        document_id: str | None = None,
        contract_id: str | None = None,
        limit: BoundedLimit = 50,
    ) -> dict[str, Any]:
        """Elenca le proposte, dalla più vecchia. `stato`: `in_attesa`, `accettata` o `rifiutata`;
        senza filtro, tutte. Per la coda di revisione usa `in_attesa`.
        """
        return proposals.search(
            context,
            ProposalListQuery(
                stato=stato,  # type: ignore[arg-type]
                target_type=target_type,  # type: ignore[arg-type]
                document_id=UUID(document_id) if document_id else None,
                contract_id=UUID(contract_id) if contract_id else None,
                limit=cast(int, limit),
            ),
        )
