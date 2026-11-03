"""What an agent proposes from an archived document: contracts and days."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.proposals.schemas import ProposalListQuery, ProposalStatus, ProposalTargetType
from pigrocrm_mcp.annotations import ADDITIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    proposals,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    Confidence,
    IsoDateStr,
    MoneyArg,
    OptionalMoney,
    QuantityArg,
    literal_arg,
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
        excerpt: str,
        confidence: Confidence,
        customer_id: str,
        title: str,
        start: str,
        renewal_type: str,
        termination_notice_days: int,
        billing_cadence: str,
        expense_policy: dict[str, Any],
        rate_card_valid_from: str,
        rate_card_type: str,
        rate_card_amount: MoneyArg,
        rate_card_unit: str,
        end: IsoDateStr = None,
        renewal_notice_days: int | None = None,
        payment_days: int | None = None,
        payment_end_of_month: bool | None = None,
        currency: str = "EUR",
        requires_prior_approval: bool = False,
        applies_social_charge: bool = False,
        note: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        rate_card_valid_to: IsoDateStr = None,
        rate_card_allowed_fractions: list[float] | None = None,
        rate_card_minimum_hours: OptionalMoney = None,
        rate_card_delivery_period: str | None = None,
        excerpt_type: str = "quoted",
        confidence_reason: str | None = None,
    ) -> dict[str, Any]:
        """Propone un contratto letto da un documento già archiviato: crea solo una proposta in
        attesa, che una persona decide dall'app. `document_id` è il documento da cui `excerpt`
        è stato letto verbatim (`excerpt_type='quoted'`) o trascritto da una scansione
        (`'transcribed'`). Gli altri campi sono quelli di `create_contract`, più la prima scheda
        tariffaria (`rate_card_*`, come `create_rate_card`). Nulla nasce prima dell'accettazione.
        """
        rate_card: dict[str, Any] = {
            "valid_from": rate_card_valid_from,
            "valid_to": rate_card_valid_to,
            "type": rate_card_type,
            "amount": rate_card_amount,
            "unit": rate_card_unit,
            "minimum_hours": rate_card_minimum_hours,
            "delivery_period": rate_card_delivery_period,
        }
        if rate_card_allowed_fractions is not None:
            rate_card["allowed_fractions"] = rate_card_allowed_fractions
        return proposals.propose(
            context,
            {
                "document_id": UUID(document_id),
                "target_type": "contract",
                "excerpt": excerpt,
                "excerpt_type": excerpt_type,
                "confidence": confidence,
                "confidence_reason": confidence_reason,
                "proposed_fields": {
                    "contract": {
                        "customer_id": customer_id,
                        "title": title,
                        "start": start,
                        "end": end,
                        "renewal_type": renewal_type,
                        "renewal_notice_days": renewal_notice_days,
                        "termination_notice_days": termination_notice_days,
                        "payment_days": payment_days,
                        "payment_end_of_month": payment_end_of_month,
                        "billing_cadence": billing_cadence,
                        "currency": currency,
                        "requires_prior_approval": requires_prior_approval,
                        "applies_social_charge": applies_social_charge,
                        "expense_policy": expense_policy,
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
        excerpt: str,
        confidence: Confidence,
        date: str,
        quantity: QuantityArg,
        description: str,
        channel: str,
        sender: str,
        received_at: str,
        message_id: str | None = None,
        excerpt_type: str = "quoted",
        confidence_reason: str | None = None,
    ) -> dict[str, Any]:
        """Propone una giornata letta da un'email o da un documento di approvazione già archiviato:
        crea solo una proposta in attesa, che una persona decide dall'app. `contract_id` è il
        contratto. `channel`, `sender`, `received_at` e `message_id` sono l'evidenza
        dell'approvazione, registrata insieme alla giornata all'accettazione.
        """
        return proposals.propose(
            context,
            {
                "document_id": UUID(document_id),
                "contract_id": UUID(contract_id),
                "target_type": "work_unit",
                "excerpt": excerpt,
                "excerpt_type": excerpt_type,
                "confidence": confidence,
                "confidence_reason": confidence_reason,
                "proposed_fields": {
                    "contract_id": contract_id,
                    "date": date,
                    "quantity": quantity,
                    "description": description,
                    "approval": {
                        "channel": channel,
                        "sender": sender,
                        "received_at": received_at,
                        "message_id": message_id,
                    },
                },
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        """Legge una proposta: campi proposti, estratto, stato ed esito se decisa
        (`accepted_fields`, `result_id`, `decided_by`, `decided_at`).
        """
        return proposals.get(context, proposal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_proposals(
        status: str | None = None,
        target_type: str | None = None,
        document_id: str | None = None,
        contract_id: str | None = None,
        limit: BoundedLimit = 50,
    ) -> dict[str, Any]:
        """Elenca le proposte, dalla più vecchia. `status`: `pending`, `accepted` o `rejected`;
        senza filtro, tutte. Per la coda di revisione usa `pending`.
        """
        return proposals.search(
            context,
            ProposalListQuery(
                status=literal_arg("status", status, ProposalStatus),
                target_type=literal_arg("target_type", target_type, ProposalTargetType),
                document_id=UUID(document_id) if document_id else None,
                contract_id=UUID(contract_id) if contract_id else None,
                limit=cast(int, limit),
            ),
        )
