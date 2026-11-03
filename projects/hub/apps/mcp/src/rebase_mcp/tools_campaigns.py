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
        """The email campaigns of the «Campagne» section, most recent first. For each:

        - `name`, `subject` and `status` (`draft`, `scheduled`, `sending`, `sent`, `cancelled`);
        - when it starts or started (`scheduled_for`, `sent_at`);
        - if sending is halted, since when and why (`stalled_at`, `stalled_reason`), until
          the next email goes out (also when the daily email limit halts
          the rest of the list: `stalled_reason` gives the day it resumes);
        - the action it measures: `signed_in`, `cv`, `card_complete`, `profile_created` or
          `request_updated`, or `click` when the button leads to a link;
        - in `counts` how many were sent, skipped, failed, delivered,
          bounced and clicked, how many people entered their area after the email
          (`entered`) and how many took the action (`actions`);
        - where the button leads (`button_meta`: `area`, `wizard`, `request` or `link`) and, for
          a link, its address (`button_url`), `null` otherwise.

        `limit` is 20 campaigns if you do not give it and 100 at most; `next_cursor` is the
        opaque cursor of the next page, `None` on the last one: pass it back in `cursor`.

        Read only: a campaign is prepared and sent from the admin area."""
        bounded(cursor, "cursor")
        page = max(1, min(limit, CAMPAIGNS_LIMIT_MAX))
        return deps.run(lambda s: CampaignService(s, deps.campaign_settings).list_all(page, cursor))

    @mcp.tool()
    def get_campaign(
        campaign_id: str, limit: int = RECIPIENTS_LIMIT_DEFAULT, cursor: str | None = None
    ) -> dict[str, Any]:
        """A campaign, by id, with its numbers and the outcome of each person in
        `recipients`:

        - the state (queued, sent, skipped with the reason, failed);
        - when the email was sent, delivered or bounced, and the first click;
        - when the person entered their area (`signed_in_from_mail` true if from the
          link in this email);
        - when they took the action (`action_from_mail` for a profile created from the link).

        `campaign.follows_id` is the campaign a «Riscrivi a chi non ha fatto
        niente» comes from; `campaign.button_meta` and `campaign.button_url` say where the
        button leads, as in `list_campaigns`. `recipients` is ordered by address, `limit` gives
        50 if you do not give it and 200 at most; `next_cursor` is the opaque cursor of the
        next recipients, `None` on the last ones: pass it back in `cursor` with the same
        `campaign_id`. `counts` always count the whole campaign. Every read of the recipients
        (names and addresses) is recorded among the admin's actions (`recipients_read`). Read
        only."""
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
