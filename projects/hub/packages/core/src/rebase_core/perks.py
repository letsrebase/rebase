"""The files a member gets, read off this package's own data.

One perk today: the guide to the first steps as a freelancer. It is a generated PDF,
committed here rather than built in the image, and `../../../tools/build_guide_pdf.py`
explains why at length. This module is only the way in: the path, the bytes and the
name a browser saves, so the API layer holds no path of its own and a test can name the
same file the route serves.

It lives beside the code rather than in the website's `dist` because it is behind the
session: a perk of being signed in, not a public download (Lorenzo, ORB-70, 2026-09-10).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from rebase_core.login_schemas import GuideDownloadRead, GuideStats
from rebase_core.models import Freelancer, GuideDownload, User

PERKS_DIR = Path(__file__).resolve().parent / "perks"

GUIDE_FILENAME = "rebase-guide-first-steps-freelance.pdf"
GUIDE_PATH = PERKS_DIR / GUIDE_FILENAME


def guide_bytes() -> bytes:
    """The guide's PDF. Read on every request rather than cached at import: it is 48 KB
    off local disk, and a module-level cache would keep a stale copy alive in a process
    that outlives a deploy of new content."""
    return GUIDE_PATH.read_bytes()


RECENT_DOWNLOADS = 20
# The short list a card's own detail shows (REB-284), well below the admin's own
# guide page: an admin reading one person does not need their last twenty.
RECENT_DOWNLOADS_FOR_CARD = 5


class PerkService:
    """What the hub remembers about a perk being taken (ORB-156): a row per download
    of the guide, and the numbers the admin area reads off those rows. Nothing here
    decides who may download; `MeDep` does, before the route calls `record`."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record_guide_download(self, user_id: UUID) -> None:
        """One row, committed on its own: the download is a fact whether or not the
        bytes then reach the browser, and a failed write must not withhold the file."""
        self.session.add(GuideDownload(user_id=user_id))
        self.session.commit()

    @staticmethod
    def _downloads() -> Select[GuideDownload, User, UUID]:
        """A download, its person and that person's live card (`None` for a company
        contact): a person has at most one card (`uq_freelancers_user_id`), so the outer
        join never doubles a row."""
        return (
            select(GuideDownload, User, Freelancer.id)
            .join(User, User.id == GuideDownload.user_id)
            .outerjoin(
                Freelancer, and_(Freelancer.user_id == User.id, Freelancer.deleted_at.is_(None))
            )
        )

    def guide_stats(self, now: datetime | None = None) -> GuideStats:
        moment = now or datetime.now(UTC)
        week_ago = moment - timedelta(days=7)
        total = self.session.scalar(select(func.count()).select_from(GuideDownload)) or 0
        members = self.session.scalar(select(func.count(func.distinct(GuideDownload.user_id)))) or 0
        total_members = self.session.scalar(select(func.count()).select_from(Freelancer)) or 0
        last = (
            self.session.scalar(
                select(func.count())
                .select_from(GuideDownload)
                .where(GuideDownload.downloaded_at >= week_ago)
            )
            or 0
        )
        rows = self.session.execute(
            self._downloads()
            .order_by(GuideDownload.downloaded_at.desc(), GuideDownload.id.desc())
            .limit(RECENT_DOWNLOADS)
        ).all()
        return GuideStats(
            total=total,
            members=members,
            total_members=total_members,
            last_7_days=last,
            recent=[
                GuideDownloadRead(
                    id=download.id,
                    user_id=download.user_id,
                    freelancer_id=freelancer_id,
                    first_name=person.first_name,
                    last_name=person.last_name,
                    email=person.email,
                    downloaded_at=download.downloaded_at,
                )
                for download, person, freelancer_id in rows
            ],
        )

    def recent_for_user(
        self, user_id: UUID, limit: int = RECENT_DOWNLOADS_FOR_CARD
    ) -> list[GuideDownloadRead]:
        """The last few times this one person downloaded the guide (REB-284's
        freelancer detail): the same shape `guide_stats` reads for everybody, scoped
        to a single `user_id` instead of grouped across the whole hub."""
        rows = self.session.execute(
            self._downloads()
            .where(GuideDownload.user_id == user_id)
            .order_by(GuideDownload.downloaded_at.desc(), GuideDownload.id.desc())
            .limit(limit)
        ).all()
        return [
            GuideDownloadRead(
                id=download.id,
                user_id=download.user_id,
                freelancer_id=freelancer_id,
                first_name=person.first_name,
                last_name=person.last_name,
                email=person.email,
                downloaded_at=download.downloaded_at,
            )
            for download, person, freelancer_id in rows
        ]
