"""CampaignService: what the admin does to a campaign (spec § 4, § 5). The loop that
actually sends is `tick.py`; this module never calls Resend except for the admin's own
test."""

import base64
import binascii
import re
import secrets
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select, tuple_, update
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminRead
from rebase_core.campaigns.actions import snapshot
from rebase_core.campaigns.audience import REASON_CANCELLED, build_audience, waiting_rows
from rebase_core.campaigns.links import LINK, button_problem
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
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate

BAD_CURSOR = "cursore non valido"
NOT_A_DRAFT = "Si modifica solo una bozza: riportala in bozza prima."
ONLY_A_DRAFT_IS_DELETED = (
    "Si elimina solo una bozza: una campagna programmata, inviata o annullata resta."
)
DRAFT_HAS_HISTORY = (
    "Questa bozza ha destinatari con un invio o un esito registrato: non si elimina."
)
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
    "source",
    "journey_status",
    "filters",
    "subject",
    "text",
    "button_text",
    "button_meta",
    "button_url",
    "action",
)


def _stripped(url: str | None) -> str | None:
    """A button's address as it is stored: trimmed, and `None` rather than blank."""
    return (url or "").strip() or None


def _recipient_cursor(recipient_id: UUID) -> str:
    """The opaque cursor after a recipient: its id, which `_recipient_after` turns back
    into the `(email, id)` position (an address is too long to carry in a cursor)."""
    return base64.urlsafe_b64encode(recipient_id.bytes).decode("ascii").rstrip("=")


def _slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "campaign"


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
            data.source,
            data.journey_status,
            data.filters is not None,
            data.button_meta,
            data.action,
        )
        button_url = _stripped(data.button_url)
        self._check_button(data.button_meta, button_url, data.action)
        now = self.clock()
        # `Campaign.filters` is `JSONB(none_as_null=True)` (models.py), so an explicit
        # `None` here binds as a true SQL NULL, matching the check constraint
        # `(fonte = 'filtri') = (filtri IS NOT NULL)`.
        campaign = Campaign(
            created_by=admin_id,
            nome=data.nome.strip(),
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(data.nome)}"[:70]),
            source=data.source,
            journey_status=data.journey_status if data.source == "status" else None,
            filters=data.filters.model_dump(mode="json", exclude_none=True)
            if data.source == "filters" and data.filters
            else None,
            subject=data.subject,
            text=data.text,
            button_text=data.button_text,
            button_meta=data.button_meta,
            button_url=button_url,
            action=data.action,
            status="draft",
            content_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def update(self, campaign_id: UUID, data: CampaignPatch) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.status != "draft":
            raise InvalidState(NOT_A_DRAFT)
        # The wizard sends every field on each «Avanti»: only a value that differs
        # makes the last test stale, not a field merely present in the request.
        before = {field: getattr(campaign, field) for field in _CONTENT_FIELDS}
        changes = data.model_dump(exclude_unset=True)
        if "filters" in changes and data.filters is not None:
            changes["filters"] = data.filters.model_dump(mode="json", exclude_none=True)
        if campaign.source == "list":
            fixed = [
                field
                for field in ("source", "journey_status", "filters", "action")
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
        if campaign.source == "filters":
            campaign.journey_status = None
        elif campaign.source == "status":
            campaign.filters = None
        self._validate(
            campaign.source,
            campaign.journey_status,
            campaign.filters is not None,
            campaign.button_meta,
            campaign.action,
        )
        # A button that leaves «Un link» takes its address with it unless the request
        # says otherwise, so the editor's «Dove porta» alone is a valid change.
        if "button_url" in changes:
            campaign.button_url = _stripped(campaign.button_url)
        elif campaign.button_meta != LINK:
            campaign.button_url = None
        self._check_button(campaign.button_meta, campaign.button_url, campaign.action)
        if any(getattr(campaign, field) != before[field] for field in _CONTENT_FIELDS):
            campaign.content_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def audience(self, campaign_id: UUID) -> AudiencePreview:
        campaign = self._require(campaign_id)
        rows = build_audience(
            self.session, campaign, now=self.clock(), gap_days=self.settings.campaign_gap_days
        )
        lines = [
            AudienceRowRead(
                email=r.candidate.email,
                nome=r.candidate.nome,
                kind=r.candidate.kind,
                excluded=r.excluded,
            )
            for r in rows
        ]
        excluded = sum(1 for r in lines if r.excluded)
        return AudiencePreview(lines=lines, included=len(lines) - excluded, excluded=excluded)

    def list_all(self, limit: int | None = None, cursor: str | None = None) -> CampaignList:
        """Every campaign, newest first. The admin screen reads them all; an agent passes
        `limit` (REB-804) and gets `next_cursor` for the page after, `None` on the last."""
        stmt = select(Campaign)
        spec = SortSpec("created_at", "datetime")
        if cursor:
            value, row_id = decode_cursor(spec, cursor)
            stmt = stmt.where(keyset_predicate(Campaign.created_at, Campaign.id, value, row_id))
        stmt = stmt.order_by(Campaign.created_at.desc(), Campaign.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit + 1)
        campaigns = list(self.session.scalars(stmt).all())
        next_cursor = None
        if limit is not None and len(campaigns) > limit:
            campaigns = campaigns[:limit]
            last = campaigns[-1]
            next_cursor = encode_cursor(spec, last.created_at, last.id)
        counts = self._counts([c.id for c in campaigns])
        return CampaignList(
            items=[
                CampaignListItem(
                    **CampaignRead.model_validate(c).model_dump(exclude={"ready"}),
                    counts=counts.get(c.id, CampaignCounts()),
                )
                for c in campaigns
            ],
            next_cursor=next_cursor,
        )

    def detail(
        self, campaign_id: UUID, limit: int | None = None, cursor: str | None = None
    ) -> CampaignDetail:
        """A campaign with its recipients by address. The admin screen reads them all;
        an agent passes `limit` (REB-804) and gets `next_cursor` for the next recipients,
        `None` on the last page. `counts` always count the whole list."""
        campaign = self._require(campaign_id)
        stmt = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        if cursor:
            after = self._recipient_after(campaign_id, cursor)
            stmt = stmt.where(
                tuple_(CampaignRecipient.email, CampaignRecipient.id)
                > tuple_(after.email, after.id)
            )
        stmt = stmt.order_by(CampaignRecipient.email, CampaignRecipient.id)
        if limit is not None:
            stmt = stmt.limit(limit + 1)
        rows = list(self.session.scalars(stmt).all())
        next_cursor = None
        if limit is not None and len(rows) > limit:
            rows = rows[:limit]
            next_cursor = _recipient_cursor(rows[-1].id)
        entered, acted = self._from_mail(campaign, rows)
        return CampaignDetail(
            campaign=CampaignRead.model_validate(campaign),
            counts=self._counts([campaign_id]).get(campaign_id, CampaignCounts()),
            recipients=[
                RecipientRead.model_validate(r).model_copy(
                    update={
                        "signed_in_from_mail": r.id in entered,
                        "action_from_mail": r.id in acted,
                    }
                )
                for r in rows
            ],
            next_cursor=next_cursor,
        )

    def _recipient_after(self, campaign_id: UUID, cursor: str) -> CampaignRecipient:
        """The recipient a page's cursor names, which must be one of this campaign's: a
        cursor from another campaign, or one that is not ours at all, is refused rather
        than read as «from the start»."""
        try:
            key = UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        except (binascii.Error, ValueError):
            raise ValidationFailed(ENTITY, "cursor", BAD_CURSOR) from None
        row = self.session.get(CampaignRecipient, key)
        if row is None or row.campaign_id != campaign_id:
            raise ValidationFailed(ENTITY, "cursor", BAD_CURSOR)
        return row

    def _from_mail(
        self, campaign: Campaign, rows: Sequence[CampaignRecipient]
    ) -> tuple[set[UUID], set[UUID]]:
        """Which stamped rows came from this very mail (spec § 4.3, § 6.2). A login counts
        when it carries the campaign's slug and the row's own code, and it is the very
        login that stamped `signed_in_at`: the slug alone is also on a forwarded mail, and
        the same slug/code pair can recur from a later, unrelated login. A card created
        for `profile_created` counts the same way, matched on the row's own code too
        (Greptile P1): the wizard stores the button link's `utm_term` on the card
        (`freelancers.py`'s `data.utm`), just as a login does. Two queries for the whole
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
            if r.signed_in_at is not None and (r.email, r.code, r.signed_in_at) in pairs
        }
        if campaign.action == "signed_in":
            return entered, entered
        if campaign.action != "profile_created":
            return entered, set()
        cards = set(
            self.session.execute(
                select(func.lower(User.email), Freelancer.utm_term)
                .join(Freelancer, Freelancer.user_id == User.id)
                .where(Freelancer.utm_campaign == campaign.slug, Freelancer.deleted_at.is_(None))
            ).all()
        )
        return entered, {
            r.id for r in rows if r.action_at is not None and (r.email, r.code) in cards
        }

    def send_test(
        self, campaign_id: UUID, admin: AdminRead, sender: CampaignSender
    ) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.status != "draft":
            raise InvalidState(NOT_A_DRAFT)
        if not (
            campaign.subject.strip() and campaign.text.strip() and campaign.button_text.strip()
        ):
            raise ValidationFailed(ENTITY, "text", EMPTY_MAIL)
        target = RenderTarget(
            email=admin.email,
            nome=(admin.nome or "").split(" ")[0] or None,
            code=person_code(admin.email),
            token="test",
        )
        outcome = sender.send(
            render(campaign, target, self.settings, test=True),
            idempotency_key=f"prova-{campaign.id}-{self.clock().isoformat()}",
        )
        if outcome.outcome != "accepted":
            raise InvalidState(
                f"Resend non ha accettato la prova ({outcome.detail or outcome.outcome})."
            )
        campaign.test_sent_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def schedule(self, campaign_id: UUID, data: ScheduleRequest) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.status != "draft":
            raise InvalidState(NOT_A_DRAFT)
        if campaign.test_sent_at is None or campaign.test_sent_at < campaign.content_at:
            raise InvalidState(NEED_TEST)
        now = self.clock()
        when = self._when(data, now)
        unticked = set(data.excluded)  # already stripped and lowercased
        rows = [
            r
            for r in build_audience(
                self.session, campaign, now=now, gap_days=self.settings.campaign_gap_days
            )
            if r.excluded is None and r.candidate.email not in unticked
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
                    kind=c.kind,
                    user_id=c.user_id,
                    freelancer_id=c.freelancer_id,
                    signup_id=c.signup_id,
                    pigro_slugs=list(c.pigro_slugs),
                    code=person_code(c.email),
                    before=snapshot(self.session, c, now),
                    unsubscribe_token=secrets.token_urlsafe(32),
                )
            )
        campaign.status = "scheduled"
        campaign.scheduled_for = when
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def back_to_draft(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.status != "scheduled":
            raise InvalidState("Torna in bozza solo una campagna programmata e non ancora partita.")
        self.session.execute(
            delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        )
        campaign.status, campaign.scheduled_for = "draft", None
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def delete(self, campaign_id: UUID) -> None:
        """An abandoned draft goes (REB-524). Only a `draft`: it has never frozen a list,
        or `back_to_draft` already dropped the one it had, so the row is all there is.
        The lock is `_require_locked`'s, so a «Programma» racing this delete either
        lands first and makes this refuse, or waits and finds the row gone."""
        campaign = self._require_locked(campaign_id)
        if campaign.status != "draft":
            raise InvalidState(ONLY_A_DRAFT_IS_DELETED)
        # A draft never carries a row that left or came back, but the cascade would
        # erase one silently if it ever did (CodeRabbit on #473): refuse instead.
        r = CampaignRecipient
        history = self.session.scalar(
            select(r.id)
            .where(
                r.campaign_id == campaign.id,
                or_(
                    r.sent_at.is_not(None),
                    r.delivered_at.is_not(None),
                    r.bounced_at.is_not(None),
                    r.first_click_at.is_not(None),
                    r.complaint_at.is_not(None),
                    r.signed_in_at.is_not(None),
                    r.action_at.is_not(None),
                ),
            )
            .limit(1)
        )
        if history is not None:
            raise InvalidState(DRAFT_HAS_HISTORY)
        self.session.delete(campaign)
        self.session.commit()

    def cancel(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require_locked(campaign_id)
        if campaign.status not in ("scheduled", "sending"):
            raise InvalidState("Si annulla solo una campagna programmata o in invio.")
        self.session.execute(
            update(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == "in_coda"
            )
            .values(status="skipped", reason=REASON_CANCELLED)
        )
        campaign.status = "cancelled"
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def follow_up(self, campaign_id: UUID, admin_id: UUID) -> CampaignRead:
        """«Riscrivi a chi non ha fatto niente» (spec § 4.3): a `draft` with `fonte =
        lista` that follows `campaign_id`, with the same action and a copy of its mail to
        rewrite. Its list is the sent rows with no action, read when shown and frozen
        when scheduled, like any other."""
        parent = self._require(campaign_id)
        if parent.status != "sent":
            raise InvalidState(ONLY_SENT)
        # Read live, the same way the list itself is shown (`waiting_rows`): a row's
        # `action_at` is only ever a stamp the tick wrote up to a minute ago, so a
        # count of unstamped rows alone counts someone who already did the action live,
        # just not yet stamped (Greptile P1, CodeRabbit Minor).
        if not waiting_rows(self.session, parent):
            raise InvalidState(NOTHING_TO_FOLLOW)
        now = self.clock()
        nome = f"{parent.nome}{FOLLOW_UP_SUFFIX}"[:CAMPAIGN_NAME_MAX_LENGTH]
        campaign = Campaign(
            created_by=admin_id,
            nome=nome,
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(nome)}"[:70]),
            source="list",
            segue_id=parent.id,
            subject=parent.subject,
            text=parent.text,
            button_text=parent.button_text,
            button_meta=parent.button_meta,
            button_url=parent.button_url,
            action=parent.action,
            status="draft",
            content_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def _when(self, data: ScheduleRequest, now: datetime) -> datetime:
        if data.day is None and data.ora is None:
            return now
        if data.day is None or data.ora is None:
            raise ValidationFailed(
                ENTITY, "ora", "Serve giorno e ora, o nessuno dei due per inviare adesso."
            )
        wall = datetime.combine(data.day, data.ora)
        local = wall.replace(tzinfo=ROME)  # fold=0: the first of an hour that happens twice
        if local.astimezone(UTC).astimezone(ROME).replace(tzinfo=None) != wall:
            raise ValidationFailed(
                ENTITY, "ora", "Quest'ora non esiste il giorno del cambio d'ora: scegline un'altra."
            )
        when = local.astimezone(UTC)
        if when < now - PAST_SLACK:
            raise ValidationFailed(ENTITY, "day", "È già passato: scegli un momento futuro.")
        return when

    # ---- helpers ------------------------------------------------------------------------

    def _validate(
        self, source: str, journey_status: str | None, has_filters: bool, meta: str, action: str
    ) -> None:
        if source == "status":
            if journey_status not in JOURNEY_STATES:
                raise ValidationFailed(ENTITY, "journey_status", "Scegli uno stato del percorso.")
            if journey_status == "pigro_empty":
                raise ValidationFailed(ENTITY, "journey_status", PIGRO_LATER)
        elif source == "filters" and not has_filters:
            raise ValidationFailed(ENTITY, "filters", "Scegli i filtri della lista.")
        if meta == "pigro":
            raise ValidationFailed(ENTITY, "button_meta", PIGRO_LATER)
        if action == "pigro_customer":
            raise ValidationFailed(ENTITY, "action", PIGRO_LATER)

    @staticmethod
    def _check_button(meta: str, url: str | None, action: str) -> None:
        """«Un link» carries an https address and measures the click; any other
        destination carries neither (REB-530, `campaigns/links.py`)."""
        problem = button_problem(meta, url, action)
        if problem is not None:
            raise ValidationFailed(ENTITY, *problem)

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
        gated on the row's current `status` takes this lock first (controller ruling
        R12), so a second concurrent call blocks until the first commits, then sees the
        state the first left behind and raises the ordinary `InvalidState` sentence
        instead of racing into a write (`ck_campaign_recipients_...`'s unique index, or
        two mails to the same person). `populate_existing`: a `FOR UPDATE` read does not
        overwrite an object this session already holds, so without it the check below
        would run on the stale state the lock was meant to refresh (CodeRabbit on #473:
        a `delete` of a `draft` another session had just scheduled, whose cascade then
        took the frozen list with it)."""
        campaign = self.session.get(
            Campaign, campaign_id, with_for_update=True, populate_existing=True
        )
        if campaign is None:
            raise NotFound(ENTITY, campaign_id)
        return campaign

    def _counts(self, ids: list[UUID]) -> dict[UUID, CampaignCounts]:
        if not ids:
            return {}
        r = CampaignRecipient
        # Eleven columns is one past what `select()` types positionally, so the counts
        # are labelled with the field each one fills and read by name, not by index.
        rows = self.session.execute(
            select(
                r.campaign_id,
                func.count().label("recipients"),
                func.count(case((r.status == "in_coda", 1))).label("in_coda"),
                func.count(case((r.status == "sent", 1))).label("sent"),
                func.count(case((r.status == "skipped", 1))).label("skipped"),
                func.count(case((r.status == "failed", 1))).label("failed"),
                func.count(r.delivered_at).label("delivered"),
                func.count(r.bounced_at).label("bounced"),
                func.count(r.first_click_at).label("clicked"),
                func.count(r.signed_in_at).label("income"),
                func.count(r.action_at).label("actions"),
            )
            .where(r.campaign_id.in_(ids))
            .group_by(r.campaign_id)
        ).all()
        return {
            row.campaign_id: CampaignCounts(
                recipients=row.recipients,
                in_coda=row.in_coda,
                sent=row.sent,
                skipped=row.skipped,
                failed=row.failed,
                delivered=row.delivered,
                bounced=row.bounced,
                clicked=row.clicked,
                income=row.income,
                actions=row.actions,
            )
            for row in rows
        }
