"""The campaigns' tools, read-only (P-REB-41 phase 2): an agent reports on a campaign without a
terminal. Both are paged (REB-804)."""

from typing import Any

from mcp.server import MCPServer
from sqlalchemy.orm import Session

from rebase_core.campaigns.service import CampaignService
from rebase_mcp.tools_common import RECIPIENTS_READ, Deps, bounded, to_uuid

# A page of campaigns is a few hundred bytes each, a page of recipients about 550: the
# defaults keep a page under ~25 KB, and the maxima are what an agent may ask for.
CAMPAIGNS_LIMIT_DEFAULT = 20
CAMPAIGNS_LIMIT_MAX = 100
RECIPIENTS_LIMIT_DEFAULT = 50
RECIPIENTS_LIMIT_MAX = 200


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_campaigns(
        limit: int = CAMPAIGNS_LIMIT_DEFAULT, cursor: str | None = None
    ) -> dict[str, Any]:
        """Le campagne di mail della sezione «Campagne», dalla più recente. Per ognuna:

        - nome, oggetto e stato (bozza, programmata, in_invio, inviata, annullata);
        - quando parte o è partita (`scheduled_for`, `sent_at`);
        - se l'invio è fermo, da quando e perché (`stalled_at`, `stalled_reason`), finché
          non parte la prossima mail (anche quando il limite giornaliero di mail ferma
          il resto della lista: `stalled_reason` dice il giorno in cui riparte);
        - l'azione che misura: entrato, cv, scheda_completa, profilo_creato o
          richiesta_aggiornata, oppure clic quando il bottone porta a un link;
        - in `counts` quante sono state inviate, saltate, fallite, consegnate,
          rimbalzate e cliccate, quante persone sono entrate nella loro area dopo la mail
          (`income`) e quante hanno fatto l'azione (`actions`);
        - dove porta il bottone (`button_meta`: area, wizard, richiesta o link) e, per
          un link, il suo indirizzo (`button_url`), `null` altrimenti.

        `limit` è di 20 campagne se non lo dai e di 100 al massimo; `next_cursor` è il
        cursore opaco della pagina successiva, `None` all'ultima: ripassalo in `cursor`.

        Solo lettura: una campagna si prepara e si invia dall'area admin."""
        bounded(cursor, "cursor")
        page = max(1, min(limit, CAMPAIGNS_LIMIT_MAX))
        return deps.run(lambda s: CampaignService(s, deps.campaign_settings).list_all(page, cursor))

    @mcp.tool()
    def get_campaign(
        campaign_id: str, limit: int = RECIPIENTS_LIMIT_DEFAULT, cursor: str | None = None
    ) -> dict[str, Any]:
        """Una campagna, per id, con i suoi numeri e l'esito di ogni persona in
        `recipients`:

        - lo stato (in_coda, inviata, saltata con il motivo, fallita);
        - quando la mail è stata inviata, consegnata o è rimbalzata, e il primo clic;
        - quando la persona è entrata nella sua area (`signed_in_from_mail` vero se dal
          link di questa mail);
        - quando ha fatto l'azione (`action_from_mail` per un profilo creato dal link).

        `campaign.segue_id` è la campagna da cui viene una «Riscrivi a chi non ha fatto
        niente»; `campaign.button_meta` e `campaign.button_url` dicono dove porta il
        bottone, come in `list_campaigns`. `recipients` va per indirizzo, `limit` ne dà 50 se
        non lo dai e 200 al massimo; `next_cursor` è il cursore opaco dei destinatari
        successivi, `None` agli ultimi: ripassalo in `cursor` con lo stesso `campaign_id`.
        `counts` contano sempre l'intera campagna. Ogni lettura dei destinatari (nomi e
        indirizzi) è registrata fra le azioni dell'admin (`recipients_read`). Solo lettura."""
        key = to_uuid(campaign_id, "campaign_id")
        bounded(cursor, "cursor")
        page = max(1, min(limit, RECIPIENTS_LIMIT_MAX))

        def call(session: Session) -> dict[str, Any]:
            detail = CampaignService(session, deps.campaign_settings).detail(key, page, cursor)
            deps.record_read(
                session,
                "campaign",
                key,
                RECIPIENTS_READ,
                {"lines": len(detail.recipients)},
            )
            return detail.model_dump(mode="json")

        return deps.call(call)
