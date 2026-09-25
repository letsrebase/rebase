"""CampaignService: what the admin does to a campaign (spec § 4, § 5). The loop that
actually sends is `tick.py`; this module never calls Resend except for the admin's own
test."""

import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.audience import build_audience
from rebase_core.campaigns.schemas import (
    AudiencePreview,
    AudienceRowRead,
    CampaignCounts,
    CampaignDetail,
    CampaignDraft,
    CampaignList,
    CampaignListItem,
    CampaignPatch,
    CampaignRead,
    RecipientRead,
    TemplateRead,
)
from rebase_core.campaigns.states import ENTITY, JOURNEY_STATES, PIGRO_LATER
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient

NOT_A_DRAFT = "Si modifica solo una bozza: riportala in bozza prima."
_CONTENT_FIELDS = (
    "fonte",
    "stato_percorso",
    "filtri",
    "oggetto",
    "testo",
    "bottone_testo",
    "bottone_meta",
    "azione",
)


def _slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "campagna"


class CampaignService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session, self.settings, self.clock = session, settings, clock

    def templates(self) -> list[TemplateRead]:
        return [TemplateRead.model_validate(t) for t in STATE_TEMPLATES.values()]

    def create(self, admin_id: UUID, data: CampaignDraft) -> CampaignRead:
        self._validate(
            data.fonte, data.stato_percorso, data.filtri is not None, data.bottone_meta, data.azione
        )
        now = self.clock()
        fields: dict[str, object] = {
            "created_by": admin_id,
            "nome": data.nome.strip(),
            "slug": self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(data.nome)}"[:70]),
            "fonte": data.fonte,
            "oggetto": data.oggetto,
            "testo": data.testo,
            "bottone_testo": data.bottone_testo,
            "bottone_meta": data.bottone_meta,
            "azione": data.azione,
            "stato": "bozza",
            "contenuto_at": now,
        }
        # Leaving `filtri`/`stato_percorso` out entirely (rather than passing `None`)
        # keeps a JSONB column an honest SQL NULL: an explicit `None` goes through the
        # type's bind processor and writes a JSON `null`, which the check constraint
        # `(fonte = 'filtri') = (filtri IS NOT NULL)` treats as "not null" and rejects.
        if data.fonte == "stato" and data.stato_percorso is not None:
            fields["stato_percorso"] = data.stato_percorso
        if data.filtri is not None:
            fields["filtri"] = data.filtri.model_dump(mode="json", exclude_none=True)
        campaign = Campaign(**fields)
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def update(self, campaign_id: UUID, data: CampaignPatch) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        changes = data.model_dump(exclude_unset=True)
        if "filtri" in changes and data.filtri is not None:
            changes["filtri"] = data.filtri.model_dump(mode="json", exclude_none=True)
        for field, value in changes.items():
            setattr(campaign, field, value)
        if campaign.fonte == "filtri":
            campaign.stato_percorso = None
        self._validate(
            campaign.fonte,
            campaign.stato_percorso,
            campaign.filtri is not None,
            campaign.bottone_meta,
            campaign.azione,
        )
        if any(field in changes for field in _CONTENT_FIELDS):
            campaign.contenuto_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def audience(self, campaign_id: UUID) -> AudiencePreview:
        campaign = self._require(campaign_id)
        rows = build_audience(
            self.session, campaign, now=self.clock(), gap_days=self.settings.campaign_gap_days
        )
        righe = [
            AudienceRowRead(
                email=r.candidate.email,
                nome=r.candidate.nome,
                tipo=r.candidate.tipo,
                escluso=r.escluso,
            )
            for r in rows
        ]
        escluse = sum(1 for r in righe if r.escluso)
        return AudiencePreview(righe=righe, incluse=len(righe) - escluse, escluse=escluse)

    def list_all(self) -> CampaignList:
        campaigns = self.session.scalars(
            select(Campaign).order_by(Campaign.created_at.desc())
        ).all()
        counts = self._counts([c.id for c in campaigns])
        return CampaignList(
            items=[
                CampaignListItem(
                    **CampaignRead.model_validate(c).model_dump(exclude={"pronta"}),
                    conteggi=counts.get(c.id, CampaignCounts()),
                )
                for c in campaigns
            ]
        )

    def detail(self, campaign_id: UUID) -> CampaignDetail:
        campaign = self._require(campaign_id)
        rows = self.session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id)
            .order_by(CampaignRecipient.email)
        ).all()
        return CampaignDetail(
            campagna=CampaignRead.model_validate(campaign),
            conteggi=self._counts([campaign_id]).get(campaign_id, CampaignCounts()),
            destinatari=[RecipientRead.model_validate(r) for r in rows],
        )

    # ---- helpers ------------------------------------------------------------------------

    def _validate(
        self, fonte: str, stato_percorso: str | None, has_filters: bool, meta: str, azione: str
    ) -> None:
        if fonte == "stato":
            if stato_percorso not in JOURNEY_STATES:
                raise ValidationFailed(ENTITY, "stato_percorso", "Scegli uno stato del percorso.")
            if stato_percorso == "pigro_vuoto":
                raise ValidationFailed(ENTITY, "stato_percorso", PIGRO_LATER)
        elif not has_filters:
            raise ValidationFailed(ENTITY, "filtri", "Scegli i filtri della lista.")
        if meta == "pigro":
            raise ValidationFailed(ENTITY, "bottone_meta", PIGRO_LATER)
        if azione == "pigro_cliente":
            raise ValidationFailed(ENTITY, "azione", PIGRO_LATER)

    def _unique_slug(self, base: str) -> str:
        taken = set(
            self.session.scalars(select(Campaign.slug).where(Campaign.slug.startswith(base)))
        )
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def _require(self, campaign_id: UUID) -> Campaign:
        campaign = self.session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFound(ENTITY, campaign_id)
        return campaign

    def _counts(self, ids: list[UUID]) -> dict[UUID, CampaignCounts]:
        if not ids:
            return {}
        r = CampaignRecipient
        rows = self.session.execute(
            select(
                r.campaign_id,
                func.count(),
                func.count(case((r.stato == "in_coda", 1))),
                func.count(case((r.stato == "inviata", 1))),
                func.count(case((r.stato == "saltata", 1))),
                func.count(case((r.stato == "fallita", 1))),
                func.count(r.consegnata_at),
                func.count(r.rimbalzata_at),
            )
            .where(r.campaign_id.in_(ids))
            .group_by(r.campaign_id)
        ).all()
        return {
            row[0]: CampaignCounts(
                destinatari=row[1],
                in_coda=row[2],
                inviate=row[3],
                saltate=row[4],
                fallite=row[5],
                consegnate=row[6],
                rimbalzate=row[7],
            )
            for row in rows
        }
