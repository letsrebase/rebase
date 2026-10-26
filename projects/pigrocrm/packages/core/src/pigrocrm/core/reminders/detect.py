"""The detector framework (spec 2026-10-26 §3, §9; Mastro's `engine.ts` and `detectors.ts`).

**One pure detector per type.** A detector reads the database and writes nothing: given
the day and the context it returns what is true, as `Detection`s, and that is all. It is
tested on a date by handing it that date, never by waiting for it. Nothing here is a job
and nothing is stored: the engine (`engine.py`) recomputes every reminder on every pass,
and what is stored is bookkeeping about a detected one (`ledger.py`, `state.py`), which
is what makes a reminder disappear by itself when its cause is fixed.

**A detector names the measure and the people, not the severity.** Severity is resolved
per recipient from their own ladder (spec §5), so the detector emits the raw measure (days
late, hours run) for every subject that reaches the loosest rung of any ladder
(`DetectorContext.bound`), and the engine turns it into a severity for each person who
would be told. That is what lets a person move their thresholds without a detector run
per person.

**Skipped, not discarded.** «Applies when» is a condition on the space, evaluated before
the detector runs (`Detector.applies`); a type that does not apply, or that an admin
switched off, is not run and its reminders are neither raised nor cleaned (spec §3.1,
§7). A detector is registered in `detectors/__init__.py` with its catalogue row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.reminders.catalogue import ReminderType, Severity
from pigrocrm.core.reminders.directory import Directory
from pigrocrm.core.reminders.preferences import ReminderSettings


@dataclass(frozen=True)
class DetectorContext:
    """What a detector may read: the session, the day (and the instant, for the types
    measured in hours), the zone `today` is a day in, the people, and the settings."""

    session: Session
    today: date
    now: datetime
    timezone: str
    directory: Directory
    settings: ReminderSettings

    def bound(self, kind: ReminderType) -> int:
        """The loosest rung of any ladder of `kind` in this space (see
        `ReminderSettings.candidate_bound`): how far a detector must look."""
        return self.settings.candidate_bound(kind)


@dataclass(frozen=True)
class Detection:
    """One subject a type has found, with who should be told.

    `entity` and `entity_id` say what the web opens (an attività, a deal, a timer);
    `customer_id` is the customer it belongs to, when it has one, for the link and the
    grouping. `title` and `detail` are what the person reads, in Italian, built here
    because only the detector knows the facts.
    """

    type: str
    subject_id: UUID
    measure: float
    recipients: frozenset[UUID]
    title: str
    detail: str
    entity: str
    entity_id: UUID
    azienda_id: UUID | None = None
    customer_id: UUID | None = None


def _always(_: DetectorContext) -> bool:
    return True


@dataclass(frozen=True)
class Detector:
    kind: ReminderType
    detect: Callable[[DetectorContext], list[Detection]]
    # «Applies when» (spec §3), a condition on the space: the ceiling only under the
    # forfettario, period reminders only if the space closes periods. Always, by default.
    applies: Callable[[DetectorContext], bool] = _always


@dataclass(frozen=True)
class Reminder:
    """A detection as one recipient sees it: the severity from their own ladder, and the
    daily `period` of the types delivered once a day."""

    key: str
    type: str
    user_id: UUID
    severity: Severity
    measure: float
    title: str
    detail: str
    entity: str
    entity_id: UUID
    azienda_id: UUID | None
    customer_id: UUID | None
    period: date | None
