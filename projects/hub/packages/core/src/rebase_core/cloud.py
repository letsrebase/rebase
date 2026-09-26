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
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound
from rebase_core.mail import Mail, talent_cloud_opened_mail
from rebase_core.models import Company, TalentCloudGrant, User
from rebase_core.schemas import TalentCloudGrantRead

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
