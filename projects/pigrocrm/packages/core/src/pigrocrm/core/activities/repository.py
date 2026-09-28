from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity


class ActivityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, activity: Activity) -> Activity:
        self.session.add(activity)
        self.session.flush()
        return activity

    def timeline(self, entity_type: str, entity_id: UUID, limit: int) -> list[Activity]:
        stmt = (
            select(Activity)
            .where(Activity.entity_type == entity_type, Activity.entity_id == entity_id)
            .order_by(Activity.occurred_at.desc(), Activity.id.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars())

    def recent(self, limit: int = 50) -> list[Activity]:
        """The global activity feed of §6.1: newest first, across every entity.

        Not paginated, and fifty rows at most. A complete history of activity is the
        entity's own `timeline`, which already exists; a paginated global feed would be a
        second way to browse the same rows, with its own cursor to get wrong.

        Served by `ix_activities_recent` once a space's feed is large enough for the
        planner to want it; nothing asserts the plan since REB-580, because a space's
        tables stay small and the index is there for the tenant that outgrows that.
        `ix_activities_entity` cannot serve it: its ordering column is third.

        The `id` tie-break is the same one `timeline` and `by_kind` carry, for the same
        reason: `occurred_at` has microsecond resolution and rows written in one
        transaction can share it.
        """
        return list(
            self.session.execute(
                select(Activity)
                .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                .limit(limit)
            ).scalars()
        )

    def by_kind(self, kinds: Sequence[str], limit: int = 20) -> list[Activity]:
        """Activities of the given kinds, newest first, across every entity.

        Spec §9.5 and §11.1: the automation run log is a read of `activities` by `kind`,
        not a new table (§9.4). A dedicated execution log would be a table whose only
        function is answering a question `activities` answers better -- the same reasoning
        that made slice 3 refuse to historicise `fiscal_profile`.

        An empty `kinds` returns nothing. `IN ()` is not portable and a carelessly written
        empty filter matches everything, which here would dump the whole timeline into a
        settings page.

        The `id` tie-break mirrors `timeline` above: `occurred_at` has microsecond
        resolution and two automations firing inside one trigger's transaction can share
        it, at which point an unordered tie is a list that changes order between two reads
        of the same rows.
        """
        if not kinds:
            return []
        return self._newest(kinds, limit)

    def by_kind_between(
        self, kinds: Sequence[str], da: datetime, a: datetime, limit: int = 20
    ) -> list[Activity]:
        """The same read as `by_kind`, restricted to the instants `[da, a)`.

        The window belongs in the query and not in the caller. `by_kind` answers the
        newest `limit` rows of the whole table, so a caller that reads them and then
        filters by period silently loses its period the moment more than `limit` rows
        were written *after* it -- which is not a hypothetical for the weekly report
        (spec 2026-09-16 §3.1, section 6): `pigrocrm digest --data` recomputes an old
        week, and every stage change since then stands between that week and the reader.

        Half-open on purpose: the caller has a range of days in the emitter's zone, and
        the only exact way to express it in instants is «from midnight to midnight, the
        second one excluded». An inclusive upper bound would need a last microsecond
        nobody can name, and `occurred_at` has microsecond resolution.
        """
        if not kinds:
            return []
        return self._newest(kinds, limit, window=(da, a))

    def _newest(
        self,
        kinds: Sequence[str],
        limit: int,
        *,
        window: tuple[datetime, datetime] | None = None,
    ) -> list[Activity]:
        """The two reads above share one ordering and one limit, written once: the `id`
        tie-break is the property both of them are relied on for."""
        stmt = select(Activity).where(Activity.kind.in_(list(kinds)))
        if window is not None:
            da, a = window
            stmt = stmt.where(Activity.occurred_at >= da, Activity.occurred_at < a)
        return list(
            self.session.execute(
                stmt.order_by(Activity.occurred_at.desc(), Activity.id.desc()).limit(limit)
            ).scalars()
        )
