"""CampaignService: what the admin does to a campaign (spec § 4, § 5). The loop that
actually sends is `tick.py`; this module never calls Resend except for the admin's own
test."""

import re
import secrets
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminRead
from rebase_core.campaigns.actions import snapshot
from rebase_core.campaigns.audience import REASON_CANCELLED, build_audience
from rebase_core.campaigns.render import RenderTarget, person_code, render
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
    ScheduleRequest,
    TemplateRead,
)
from rebase_core.campaigns.sender import CampaignSender
from rebase_core.campaigns.states import ENTITY, JOURNEY_STATES, PIGRO_LATER
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import (
    CAMPAIGN_NAME_MAX_LENGTH,
    Campaign,
    CampaignRecipient,
    Freelancer,
    Login,
    User,
)

NOT_A_DRAFT = "Si modifica solo una bozza: riportala in bozza prima."
ROME = ZoneInfo("Europe/Rome")
NEED_TEST = "Manda una prova dopo l'ultima modifica, poi invia."
EMPTY_MAIL = "Oggetto, testo e bottone servono prima della prova."
PAST_SLACK = timedelta(minutes=1)
FOLLOW_UP_SUFFIX = " · riscrivi"
ONLY_SENT = "Si riscrive solo a chi ha ricevuto una campagna già inviata."
NOTHING_TO_FOLLOW = "Hanno fatto tutti l'azione: non c'è nessuno a cui riscrivere."
LIST_IS_FIXED = (
    "Chi riceve una «Riscrivi» e cosa misura li decide la campagna da cui viene: "
    "si cambia solo la mail."
)
# What the test mail showed: the list's source, the mail, and the slug, which is the
# button link's `utm_campaign` and Resend's tag and follows a renamed draft.
_CONTENT_FIELDS = (
    "slug",
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
        # `Campaign.filtri` is `JSONB(none_as_null=True)` (models.py), so an explicit
        # `None` here binds as a true SQL NULL, matching the check constraint
        # `(fonte = 'filtri') = (filtri IS NOT NULL)`.
        campaign = Campaign(
            created_by=admin_id,
            nome=data.nome.strip(),
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(data.nome)}"[:70]),
            fonte=data.fonte,
            stato_percorso=data.stato_percorso if data.fonte == "stato" else None,
            filtri=data.filtri.model_dump(mode="json", exclude_none=True)
            if data.fonte == "filtri" and data.filtri
            else None,
            oggetto=data.oggetto,
            testo=data.testo,
            bottone_testo=data.bottone_testo,
            bottone_meta=data.bottone_meta,
            azione=data.azione,
            stato="bozza",
            contenuto_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def update(self, campaign_id: UUID, data: CampaignPatch) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        # The wizard sends every field on each «Avanti»: only a value that differs
        # makes the last test stale, not a field merely present in the request.
        before = {field: getattr(campaign, field) for field in _CONTENT_FIELDS}
        changes = data.model_dump(exclude_unset=True)
        if "filtri" in changes and data.filtri is not None:
            changes["filtri"] = data.filtri.model_dump(mode="json", exclude_none=True)
        if campaign.fonte == "lista":
            fixed = [
                field
                for field in ("fonte", "stato_percorso", "filtri", "azione")
                if field in changes and changes[field] != getattr(campaign, field)
            ]
            if fixed:
                raise ValidationFailed(ENTITY, fixed[0], LIST_IS_FIXED)
        if data.nome is not None:
            changes["nome"] = data.nome.strip()
            if changes["nome"] != campaign.nome:
                # The slug is the button's `utm_campaign` and Resend's tag: it follows
                # the name a draft will be sent with (a filtered campaign starts as
                # «Campagna da filtri»), keeping the day it was created.
                created = campaign.created_at.astimezone(UTC)
                campaign.slug = self._unique_slug(
                    f"c-{created:%Y-%m-%d}-{_slugify(changes['nome'])}"[:70],
                    exclude=campaign.id,
                )
        for field, value in changes.items():
            setattr(campaign, field, value)
        if campaign.fonte == "filtri":
            campaign.stato_percorso = None
        elif campaign.fonte == "stato":
            campaign.filtri = None
        self._validate(
            campaign.fonte,
            campaign.stato_percorso,
            campaign.filtri is not None,
            campaign.bottone_meta,
            campaign.azione,
        )
        if any(getattr(campaign, field) != before[field] for field in _CONTENT_FIELDS):
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
        entered, acted = self._from_mail(campaign, rows)
        return CampaignDetail(
            campagna=CampaignRead.model_validate(campaign),
            conteggi=self._counts([campaign_id]).get(campaign_id, CampaignCounts()),
            destinatari=[
                RecipientRead.model_validate(r).model_copy(
                    update={
                        "entrato_dalla_mail": r.id in entered,
                        "azione_dalla_mail": r.id in acted,
                    }
                )
                for r in rows
            ],
        )

    def _from_mail(
        self, campaign: Campaign, rows: Sequence[CampaignRecipient]
    ) -> tuple[set[UUID], set[UUID]]:
        """Which stamped rows came from this very mail (spec § 4.3, § 6.2). A login counts
        when it carries the campaign's slug and the row's own code, and it is the very
        login that stamped `entrato_at`: the slug alone is also on a forwarded mail, and
        the same slug/code pair can recur from a later, unrelated login. A card created
        for `profilo_creato` counts when it stored the slug. Two queries for the whole
        list."""
        pairs = set(
            self.session.execute(
                select(func.lower(User.email), Login.utm_term, Login.logged_at)
                .join(User, User.id == Login.user_id)
                .where(Login.utm_campaign == campaign.slug)
            ).all()
        )
        entered = {
            r.id
            for r in rows
            if r.entrato_at is not None and (r.email, r.codice, r.entrato_at) in pairs
        }
        if campaign.azione == "entrato":
            return entered, entered
        if campaign.azione != "profilo_creato":
            return entered, set()
        cards = set(
            self.session.scalars(
                select(func.lower(User.email))
                .join(Freelancer, Freelancer.user_id == User.id)
                .where(Freelancer.utm_campaign == campaign.slug, Freelancer.deleted_at.is_(None))
            )
        )
        return entered, {r.id for r in rows if r.azione_at is not None and r.email in cards}

    def send_test(
        self, campaign_id: UUID, admin: AdminRead, sender: CampaignSender
    ) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        if not (
            campaign.oggetto.strip() and campaign.testo.strip() and campaign.bottone_testo.strip()
        ):
            raise ValidationFailed(ENTITY, "testo", EMPTY_MAIL)
        target = RenderTarget(
            email=admin.email,
            nome=(admin.nome or "").split(" ")[0] or None,
            codice=person_code(admin.email),
            token="prova",
        )
        outcome = sender.send(
            render(campaign, target, self.settings, test=True),
            idempotency_key=f"prova-{campaign.id}-{self.clock().isoformat()}",
        )
        if outcome.esito != "accettata":
            raise InvalidState(
                f"Resend non ha accettato la prova ({outcome.dettaglio or outcome.esito})."
            )
        campaign.prova_inviata_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def schedule(self, campaign_id: UUID, data: ScheduleRequest) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        if campaign.prova_inviata_at is None or campaign.prova_inviata_at < campaign.contenuto_at:
            raise InvalidState(NEED_TEST)
        now = self.clock()
        when = self._when(data, now)
        unticked = set(data.esclusi)  # already stripped and lowercased
        rows = [
            r
            for r in build_audience(
                self.session, campaign, now=now, gap_days=self.settings.campaign_gap_days
            )
            if r.escluso is None and r.candidate.email not in unticked
        ]
        if not rows:
            raise ValidationFailed(
                ENTITY, "esclusi", "La lista è vuota: nessuno riceverebbe la mail."
            )
        for row in rows:
            c = row.candidate
            self.session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    email=c.email,
                    nome=c.nome,
                    tipo=c.tipo,
                    user_id=c.user_id,
                    freelancer_id=c.freelancer_id,
                    signup_id=c.signup_id,
                    pigro_slugs=list(c.pigro_slugs),
                    codice=person_code(c.email),
                    prima=snapshot(self.session, c, now),
                    disiscrizione_token=secrets.token_urlsafe(32),
                )
            )
        campaign.stato = "programmata"
        campaign.programmata_per = when
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def back_to_draft(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.stato != "programmata":
            raise InvalidState("Torna in bozza solo una campagna programmata e non ancora partita.")
        self.session.execute(
            delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        )
        campaign.stato, campaign.programmata_per = "bozza", None
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def cancel(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.stato not in ("programmata", "in_invio"):
            raise InvalidState("Si annulla solo una campagna programmata o in invio.")
        self.session.execute(
            update(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.stato == "in_coda"
            )
            .values(stato="saltata", motivo=REASON_CANCELLED)
        )
        campaign.stato = "annullata"
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def follow_up(self, campaign_id: UUID, admin_id: UUID) -> CampaignRead:
        """«Riscrivi a chi non ha fatto niente» (spec § 4.3): a `bozza` with `fonte =
        lista` that follows `campaign_id`, with the same action and a copy of its mail to
        rewrite. Its list is the sent rows with no action, read when shown and frozen
        when scheduled, like any other."""
        parent = self._require(campaign_id)
        if parent.stato != "inviata":
            raise InvalidState(ONLY_SENT)
        waiting = self.session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == parent.id,
                CampaignRecipient.stato == "inviata",
                CampaignRecipient.azione_at.is_(None),
            )
        )
        if not waiting:
            raise InvalidState(NOTHING_TO_FOLLOW)
        now = self.clock()
        nome = f"{parent.nome}{FOLLOW_UP_SUFFIX}"[:CAMPAIGN_NAME_MAX_LENGTH]
        campaign = Campaign(
            created_by=admin_id,
            nome=nome,
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(nome)}"[:70]),
            fonte="lista",
            segue_id=parent.id,
            oggetto=parent.oggetto,
            testo=parent.testo,
            bottone_testo=parent.bottone_testo,
            bottone_meta=parent.bottone_meta,
            azione=parent.azione,
            stato="bozza",
            contenuto_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def _when(self, data: ScheduleRequest, now: datetime) -> datetime:
        if data.giorno is None and data.ora is None:
            return now
        if data.giorno is None or data.ora is None:
            raise ValidationFailed(
                ENTITY, "ora", "Serve giorno e ora, o nessuno dei due per inviare adesso."
            )
        wall = datetime.combine(data.giorno, data.ora)
        local = wall.replace(tzinfo=ROME)  # fold=0: the first of an hour that happens twice
        if local.astimezone(UTC).astimezone(ROME).replace(tzinfo=None) != wall:
            raise ValidationFailed(
                ENTITY, "ora", "Quest'ora non esiste il giorno del cambio d'ora: scegline un'altra."
            )
        when = local.astimezone(UTC)
        if when < now - PAST_SLACK:
            raise ValidationFailed(ENTITY, "giorno", "È già passato: scegli un momento futuro.")
        return when

    # ---- helpers ------------------------------------------------------------------------

    def _validate(
        self, fonte: str, stato_percorso: str | None, has_filters: bool, meta: str, azione: str
    ) -> None:
        if fonte == "stato":
            if stato_percorso not in JOURNEY_STATES:
                raise ValidationFailed(ENTITY, "stato_percorso", "Scegli uno stato del percorso.")
            if stato_percorso == "pigro_vuoto":
                raise ValidationFailed(ENTITY, "stato_percorso", PIGRO_LATER)
        elif fonte == "filtri" and not has_filters:
            raise ValidationFailed(ENTITY, "filtri", "Scegli i filtri della lista.")
        if meta == "pigro":
            raise ValidationFailed(ENTITY, "bottone_meta", PIGRO_LATER)
        if azione == "pigro_cliente":
            raise ValidationFailed(ENTITY, "azione", PIGRO_LATER)

    def _unique_slug(self, base: str, *, exclude: UUID | None = None) -> str:
        """`base`, or `base-2`, `base-3`… if another campaign holds it. `exclude` is
        the campaign being renamed: its own slug is never a collision."""
        query = select(Campaign.slug).where(Campaign.slug.startswith(base))
        if exclude is not None:
            query = query.where(Campaign.id != exclude)
        taken = set(self.session.scalars(query))
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

    def _require_locked(self, campaign_id: UUID) -> Campaign:
        """Like `_require`, but `SELECT ... FOR UPDATE`: every method whose write is
        gated on the row's current `stato` takes this lock first (controller ruling
        R12), so a second concurrent call blocks until the first commits, then sees the
        state the first left behind and raises the ordinary `InvalidState` sentence
        instead of racing into a write (`ck_campaign_recipients_...`'s unique index, or
        two mails to the same person)."""
        campaign = self.session.get(Campaign, campaign_id, with_for_update=True)
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
                func.count(r.primo_clic_at),
                func.count(r.entrato_at),
                func.count(r.azione_at),
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
                cliccate=row[8],
                entrate=row[9],
                azioni=row[10],
            )
            for row in rows
        }
