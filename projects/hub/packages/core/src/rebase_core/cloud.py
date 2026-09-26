"""The talent cloud's door (REB-518, spec § 4.1): an admin opens the private talent cloud
from a company request's page to that request's referente, and closes it again.

**One live grant per person and company.** A grant is the referente's `user_id` and the
request's `company_id`; `uq_talent_cloud_grants_user_company_live` holds one live row
per pair. A second «Apri il talent cloud» on the same request, a double click in two
sessions included, answers the grant already there and mails nobody; a person behind
two companies holds two grants, and the cloud is open while any of them is live. What a
person proposes in the cloud (D3) is attributed to their newest live grant's company,
which is what `for_user` answers.

**A request an admin took down closes its door.** The cloud shows names and CVs, so a
grant whose request is soft-deleted opens nothing (`for_user`), and cannot be opened or
closed from a page the admin no longer reaches; restoring the request opens it again.

**The mail is built here and sent by the caller**, after its response: a new grant
answers the referente's «Il talent cloud di rebase è aperto per …» beside the read, an
answered one no mail at all. Nothing here logs a name or an address.

**The cloud itself** (REB-519, spec § 4.2) is `CloudTalentService`, below: the talents
by name for whoever holds a live grant, filtered, vetted first then by name, at most
`CLOUD_LIST_CAP`; the CV of one of them; and who is asking, for the requests the cloud
files with no form. One filter, `team_builder.cloud_visible`, decides who is in the
cloud for the list, the CV and «Richiedi» alike, and one sentence, «Profilo non
disponibile.», answers any id outside it, so the answer never says why.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.bands import BANDS, CLIENT_MARKUP, band_for
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.freelancers import cv_of
from rebase_core.mail import Mail, talent_cloud_opened_mail
from rebase_core.models import (
    CARD_SENIORITIES,
    REMOTE_OPTIONS,
    Company,
    Freelancer,
    FreelancerCard,
    TalentCloudGrant,
    User,
)
from rebase_core.schemas import CvFile, TalentCloudGrantRead
from rebase_core.search import escape_like
from rebase_core.team_builder import cloud_visible
from rebase_core.team_schemas import Card, CloudTalentList, CloudTalentQuery, CloudTalentRead

logger = logging.getLogger(__name__)

ENTITY = "company"
LIVE_GRANT_INDEX = "uq_talent_cloud_grants_user_company_live"
NOT_OPEN = "Il talent cloud non è aperto per questa azienda."
# `list` is not paginated: a grant is a company rebase admitted by hand, a few dozen for
# a long while. The newest this many are what the list answers, and it says so.
LIST_CAP = 200


def _violates_live_grant(exc: IntegrityError) -> bool:
    """Whether the insert lost to another «Apri» on the same request: read from the
    driver's diagnostics, the way `team_requests` reads its own index."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == LIVE_GRANT_INDEX


class TalentCloudService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        """`settings` is `grant`'s alone, for the mail's link to the hub; the reads take
        the session only, which is how `MemberService.me_read` and `CompanyService.get`
        ask them."""
        self.session = session
        self.settings = settings

    def grant(self, company_id: UUID, admin_id: UUID) -> tuple[TalentCloudGrantRead, Mail | None]:
        """«Apri il talent cloud»: the live grant of the request's referente for this
        request, written now with the mail to them, or the one already there with none.
        `NotFound` for a request that is not there or was deleted."""
        assert self.settings is not None, "grant needs the settings for the mail's link"
        company, user = self._require_company(company_id)
        existing = self._live(user.id, company.id)
        if existing is not None:
            return self._read(existing), None
        row = TalentCloudGrant(user_id=user.id, company_id=company.id, granted_by=admin_id)
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if not _violates_live_grant(exc):
                raise
            # The other click committed first: the index, not the read above, is what
            # serializes the two, and the loser answers the winner's row.
            existing = self._live(user.id, company.id)
            assert existing is not None
            return self._read(existing), None
        mail = talent_cloud_opened_mail(
            user.email,
            nome=user.nome,
            azienda=company.nome_azienda,
            url=f"{self.settings.hub_url.rstrip('/')}/me/cloud",
        )
        return self._read(row), mail

    def revoke(self, company_id: UUID, admin_id: UUID) -> TalentCloudGrantRead:
        """«Revoca il talent cloud»: closes this request's live grant, and only this
        one; another company's grant of the same person stays live, and so does their
        cloud. `InvalidState` with its sentence when nothing is live for it."""
        company, user = self._require_company(company_id)
        row = self._live(user.id, company.id)
        if row is None:
            raise InvalidState(NOT_OPEN)
        row.revoked_at = utcnow()
        row.revoked_by = admin_id
        self.session.commit()
        return self._read(row)

    def list(self) -> list[TalentCloudGrantRead]:
        """Every grant, live and closed, newest first, at most `LIST_CAP`. A grant of a
        request an admin deleted is left out, as `for_user` leaves it: it opens nothing,
        and listing it as live would say the opposite; restoring the request lists it
        again."""
        rows = self.session.scalars(
            select(TalentCloudGrant)
            .join(Company, Company.id == TalentCloudGrant.company_id)
            .where(Company.deleted_at.is_(None))
            .order_by(TalentCloudGrant.granted_at.desc(), TalentCloudGrant.id.desc())
            .limit(LIST_CAP)
        ).all()
        return [self._read(row) for row in rows]

    def for_company(self, company_id: UUID) -> TalentCloudGrantRead | None:
        """The live grant a request's page shows beside «Revoca il talent cloud», or
        `None` beside «Apri il talent cloud»."""
        company = self.session.get(Company, company_id)
        if company is None:
            return None
        row = self._live(company.user_id, company.id)
        return self._read(row) if row is not None else None

    def for_user(self, user_id: UUID) -> TalentCloudGrant | None:
        """The person's newest live grant across their companies, on a request that is
        still there: the cloud is open while this is not `None`, and a proposal made in
        it is that grant's company's."""
        return self.session.scalar(
            select(TalentCloudGrant)
            .join(Company, Company.id == TalentCloudGrant.company_id)
            .where(
                TalentCloudGrant.user_id == user_id,
                TalentCloudGrant.revoked_at.is_(None),
                Company.deleted_at.is_(None),
            )
            .order_by(TalentCloudGrant.granted_at.desc(), TalentCloudGrant.id.desc())
            .limit(1)
        )

    def _live(self, user_id: UUID, company_id: UUID) -> TalentCloudGrant | None:
        return self.session.scalar(
            select(TalentCloudGrant).where(
                TalentCloudGrant.user_id == user_id,
                TalentCloudGrant.company_id == company_id,
                TalentCloudGrant.revoked_at.is_(None),
            )
        )

    def _require_company(self, company_id: UUID) -> tuple[Company, User]:
        found = self.session.execute(
            select(Company, User)
            .join(User, User.id == Company.user_id)
            .where(Company.id == company_id, Company.deleted_at.is_(None))
        ).first()
        if found is None:
            raise NotFound(ENTITY, company_id)
        return found[0], found[1]

    def _read(self, row: TalentCloudGrant) -> TalentCloudGrantRead:
        company = self.session.get(Company, row.company_id)
        user = self.session.get(User, row.user_id)
        assert company is not None and user is not None
        granted_by = self.session.get(User, row.granted_by)
        revoked_by = self.session.get(User, row.revoked_by) if row.revoked_by else None
        return TalentCloudGrantRead(
            id=row.id,
            user_id=row.user_id,
            company_id=row.company_id,
            azienda=company.nome_azienda,
            referente=f"{user.nome} {user.cognome}".strip(),
            email=user.email,
            granted_by=row.granted_by,
            granted_by_nome=granted_by.nome if granted_by is not None else None,
            granted_at=row.granted_at,
            revoked_by=row.revoked_by,
            revoked_by_nome=revoked_by.nome if revoked_by is not None else None,
            revoked_at=row.revoked_at,
        )


# ---- the cloud itself (REB-519, spec § 4.2) ------------------------------------------------

# What `CloudDep` answers a signed-in person with no live grant, in a 403.
CLOUD_CLOSED = "Il talent cloud non è aperto per questo account."
# Any freelancer id outside `cloud_visible`, for the CV and «Richiedi» alike: one sentence,
# so the answer does not say whether the person was turned down, deleted or never there.
NOT_IN_THE_CLOUD = "Profilo non disponibile."
TALENT_ENTITY = "talento"
# The cloud's one page (spec § 4.2): the community's size for a while. The cursor module
# encodes one key, and this order is two (vetted, then name); a later card paginates it
# when the count asks for it, and until then the list says when it was cut.
CLOUD_LIST_CAP = 200


class NotInTheCloud(NotFound):
    """A freelancer the cloud does not show: a 404 «Profilo non disponibile.», the same
    for an unknown id, a talent turned down, deleted, without a card, or (for the CV)
    without a file."""

    def __init__(self, freelancer_id: UUID) -> None:
        super().__init__(TALENT_ENTITY, freelancer_id)
        self.message = NOT_IN_THE_CLOUD
        self.args = (NOT_IN_THE_CLOUD,)


@dataclass(frozen=True)
class CloudCaller:
    """Who is asking in the cloud: the signed-in person, their address and phone (none
    when they gave none), and the company of their newest live grant, which is who a
    proposal and a request from the cloud are for (spec § 1)."""

    user_id: UUID
    email: str
    telefono: str | None
    company_id: UUID
    azienda: str


def cloud_card(session: Session, freelancer_id: UUID) -> Card:
    """The card of a talent the cloud shows, or `NotInTheCloud`: what «Richiedi» files
    the request with, the card's role being the one the request records."""
    stored = session.scalar(
        cloud_visible(select(FreelancerCard.card).select_from(Freelancer)).where(
            Freelancer.id == freelancer_id
        )
    )
    if stored is None:
        raise NotInTheCloud(freelancer_id)
    try:
        return Card.model_validate(stored)
    except ValidationError:
        logger.warning("cloud: the card of freelancer %s is not a card", freelancer_id)
        raise NotInTheCloud(freelancer_id) from None


def _band_clauses(query: CloudTalentQuery) -> list[ColumnElement[bool]]:
    """The band filter as conditions on the client's price per day (the rate plus 40%),
    so the database decides it: a band's bottom is at or above `fascia_min` when the
    price reaches the first band bound at or above it, and its top is at or below
    `fascia_max` when the price stays under the last bound at or below it; no such bound,
    no band. A talent with no rate has no price, and no band filter finds them."""
    price = Freelancer.tariffa_giornaliera * CLIENT_MARKUP
    clauses: list[ColumnElement[bool]] = []
    if query.fascia_min is not None:
        floors = [low for low, _ in BANDS if low >= query.fascia_min]
        clauses.append(price >= min(floors) if floors else false())
    if query.fascia_max is not None:
        tops = [high for _, high in BANDS if high is not None and high <= query.fascia_max]
        clauses.append(price < max(tops) if tops else false())
    return clauses


def _filter_clauses(query: CloudTalentQuery) -> list[ColumnElement[bool]]:
    """The filters as conditions: the role matched whole, the seniority and the work mode
    among the words the hub knows (`ValidationFailed` otherwise), the skill inside any of
    the card's skills whatever the case, `%` and `_` taken for themselves, and the band."""
    clauses: list[ColumnElement[bool]] = []
    if query.ruolo:
        clauses.append(FreelancerCard.card["ruolo"].astext == query.ruolo)
    if query.seniority:
        if query.seniority not in CARD_SENIORITIES:
            raise ValidationFailed(
                TALENT_ENTITY, "seniority", f"uno fra {', '.join(CARD_SENIORITIES)}"
            )
        clauses.append(FreelancerCard.card["seniority"].astext == query.seniority)
    if query.modalita:
        if query.modalita not in REMOTE_OPTIONS:
            raise ValidationFailed(
                TALENT_ENTITY, "modalita", f"uno fra {', '.join(REMOTE_OPTIONS)}"
            )
        clauses.append(Freelancer.remoto == query.modalita)
    if query.competenza:
        skills = func.jsonb_array_elements_text(FreelancerCard.card["competenze"]).table_valued(
            "value"
        )
        pattern = f"%{escape_like(query.competenza)}%"
        clauses.append(
            select(skills.c.value).where(skills.c.value.ilike(pattern, escape="\\")).exists()
        )
    clauses.extend(_band_clauses(query))
    return clauses


def _cloud_roles(session: Session) -> list[str]:
    """Each role of the cloud's cards once, sorted as a person reads them."""
    roles = session.scalars(
        cloud_visible(
            select(FreelancerCard.card["ruolo"].astext).select_from(Freelancer)
        ).distinct()
    ).all()
    return sorted({role for role in roles if role}, key=str.casefold)


class CloudTalentService:
    """The cloud's reads. The grant is the caller's to check (`CloudDep`, `caller`): what
    is here answers whoever reached it."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def caller(self, user_id: UUID) -> CloudCaller | None:
        """The person with the company of their newest live grant
        (`TalentCloudService.for_user`), or `None` while no grant of theirs is live."""
        grant = TalentCloudService(self.session).for_user(user_id)
        if grant is None:
            return None
        company = self.session.get(Company, grant.company_id)
        user = self.session.get(User, user_id)
        assert company is not None and user is not None  # foreign keys
        return CloudCaller(
            user_id=user.id,
            email=user.email,
            telefono=user.telefono,
            company_id=company.id,
            azienda=company.nome_azienda,
        )

    def list(self, query: CloudTalentQuery) -> CloudTalentList:
        """Every `cloud_visible` talent the filters leave, vetted first, then by surname
        and name, at most `CLOUD_LIST_CAP`, with the roles of the whole cloud. A stored
        card that no longer validates is left out and logged by id, as the catalogue
        leaves it out. The CV's bytes are never read here: `ha_cv` is asked of the
        database."""
        stmt = cloud_visible(
            select(
                Freelancer.id,
                Freelancer.links,
                Freelancer.remoto,
                Freelancer.tariffa_giornaliera,
                Freelancer.vetted_at,
                and_(
                    Freelancer.cv_bytes.is_not(None),
                    Freelancer.cv_filename.is_not(None),
                    Freelancer.cv_mime.is_not(None),
                ).label("ha_cv"),
                User.nome,
                User.cognome,
                User.linkedin_url,
                FreelancerCard.card,
            ).join(User, User.id == Freelancer.user_id)
        ).where(*_filter_clauses(query))
        rows = self.session.execute(
            stmt.order_by(
                Freelancer.vetted_at.is_(None),
                func.lower(User.cognome),
                func.lower(User.nome),
                Freelancer.id,
            ).limit(CLOUD_LIST_CAP + 1)
        ).all()
        items: list[CloudTalentRead] = []
        for row in rows[:CLOUD_LIST_CAP]:
            try:
                card = Card.model_validate(row.card)
            except ValidationError:
                logger.warning("cloud: the card of freelancer %s is not a card", row.id)
                continue
            items.append(
                CloudTalentRead(
                    freelancer_id=row.id,
                    nome=row.nome,
                    cognome=row.cognome,
                    linkedin_url=row.linkedin_url,
                    links=list(row.links or []),
                    vetted=row.vetted_at is not None,
                    card=card.model_copy(update={"luogo": None}),
                    modalita=row.remoto,
                    fascia=band_for(row.tariffa_giornaliera),
                    ha_cv=bool(row.ha_cv),
                )
            )
        return CloudTalentList(
            items=items, ruoli=_cloud_roles(self.session), capped=len(rows) > CLOUD_LIST_CAP
        )

    def cv(self, freelancer_id: UUID) -> CvFile:
        """The CV of a talent the cloud shows, as stored; `NotInTheCloud` for anyone
        else, and for a talent of the cloud with no file."""
        row = self.session.scalar(
            cloud_visible(select(Freelancer)).where(Freelancer.id == freelancer_id)
        )
        if row is None:
            raise NotInTheCloud(freelancer_id)
        try:
            return cv_of(row)
        except NotFound:
            raise NotInTheCloud(freelancer_id) from None
