"""The campaigns, read over the hub's MCP server (P-REB-41 phase 2): the list with its
numbers, one campaign with each person's outcome, and nothing that sends."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.models import Campaign, CampaignRecipient, User
from rebase_mcp.server import build_server

IVAN = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000001"),
    email="ivan@rebase.it",
    nome="Ivan",
    attivo=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)
SENT = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _seed(factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    session = factory()
    try:
        owner = User(email="campagne-mcp@rebase.it", nome="Ivan", cognome="S", role="admin")
        session.add(owner)
        session.flush()
        campaign = Campaign(
            created_by=owner.id,
            nome="Manca il CV",
            slug="c-mcp-manca-il-cv",
            fonte="stato",
            stato_percorso="manca_cv",
            oggetto="o",
            testo="t",
            bottone_testo="b",
            bottone_meta="area",
            azione="cv",
            stato="inviata",
            contenuto_at=SENT,
            inviata_at=SENT,
        )
        session.add(campaign)
        session.flush()
        later = SENT + timedelta(hours=2)
        for email, done in (("ada@studio.it", later), ("bob@studio.it", None)):
            session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    email=email,
                    tipo="freelancer",
                    codice="1",
                    prima={},
                    disiscrizione_token=f"t-{email}",
                    stato="inviata",
                    inviata_at=SENT,
                    primo_clic_at=later,
                    entrato_at=later,
                    azione_at=done,
                )
            )
        session.commit()
        return campaign.id, owner.id
    finally:
        session.close()


def _drop(factory: sessionmaker[Session], campaign_id: UUID, owner_id: UUID) -> None:
    session = factory()
    try:
        session.execute(
            delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        )
        session.execute(delete(Campaign).where(Campaign.id == campaign_id))
        session.execute(delete(User).where(User.id == owner_id))
        session.commit()
    finally:
        session.close()


async def test_an_agent_reads_the_campaigns_and_each_persons_outcome(
    factory: sessionmaker[Session],
) -> None:
    campaign_id, owner_id = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            listed = _payload(await client.call_tool("list_campagne", {}))
            one = _payload(
                await client.call_tool("get_campagna", {"campagna_id": str(campaign_id)})
            )
            names = {tool.name for tool in (await client.list_tools()).tools}
    finally:
        _drop(factory, campaign_id, owner_id)
    item = next(i for i in listed["items"] if i["id"] == str(campaign_id))
    counts = item["conteggi"]
    assert (counts["inviate"], counts["cliccate"], counts["entrate"], counts["azioni"]) == (
        2,
        2,
        2,
        1,
    )
    people = {p["email"]: p for p in one["destinatari"]}
    assert people["ada@studio.it"]["azione_at"] is not None
    assert people["bob@studio.it"]["azione_at"] is None
    assert one["campagna"]["nome"] == "Manca il CV"
    # Read-only: nothing here sends, schedules or cancels a campaign.
    assert not [n for n in names if "campagn" in n and n not in ("list_campagne", "get_campagna")]


async def test_an_unknown_campaign_is_a_sentence(factory: sessionmaker[Session]) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "get_campagna", {"campagna_id": "01a00000-0000-7000-8000-00000000dead"}
        )
    assert result.is_error
