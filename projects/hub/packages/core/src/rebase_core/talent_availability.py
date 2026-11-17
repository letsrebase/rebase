"""Who is already committed, as the CRM says (REB-1120, collaborators design § 3 and § 5): what
«Talenti», a team request's talents and the MCP talent tools show beside each freelancer, so an
admin sees who is busy before writing to them about another project.

The CRM computes it, by ISO week, from the freelancer's live engagements (draft, out for signature
and active); the hub only summarises the weeks it answers into this week and when that changes.
One read covers a whole list (`FreelancerDoor.commitments`): the CRM answers every card it holds a
live engagement for, and every other card is free. An unreachable CRM raises `PigroUnavailable`,
and what that means is the caller's: a list stays usable and says the availability could not be
read.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from rebase_core.door_schemas import CrmAvailability, CrmWeek
from rebase_core.pigro_door import FreelancerDoor
from rebase_core.schema_common import ResponseModel

AvailabilityStatus = Literal["free", "committed", "unknown"]


class TalentAvailability(ResponseModel):
    """One freelancer's availability as the CRM reads it, summarised. `status` is this week's:
    `free`, `committed` (`committed_days` a week) or `unknown`, when an engagement of the week has
    no days a week in the CRM, which is never to be read as free. `committed_from` is the Monday
    of the first week that is not free, when this one is; `free_from` the Monday of the first free
    week, when this one is not. Both are `None` when nothing changes before `until`, the last day
    the CRM was asked about."""

    status: AvailabilityStatus
    committed_days: Decimal
    committed_from: date | None
    free_from: date | None
    until: date


class TalentCommitments(ResponseModel):
    """The availability of every freelancer the CRM holds a live engagement for, by hub
    freelancer id, from `date_from` to `date_to`. A card that is not in `freelancers` is free in
    every week of it."""

    date_from: date
    date_to: date
    freelancers: dict[UUID, TalentAvailability]


def _status(week: CrmWeek) -> AvailabilityStatus:
    # A status a later CRM may add is read as «cannot say», never as free.
    if week.status in ("free", "committed"):
        return week.status  # type: ignore[return-value]
    return "unknown"


def summarize(weeks: Sequence[CrmWeek], until: date) -> TalentAvailability:
    """This week and its next change, from the weeks the CRM answered, first one this week."""
    if not weeks:
        return free(until)
    now, later = weeks[0], weeks[1:]
    status = _status(now)
    if status == "free":
        changes = next((week.starts_on for week in later if _status(week) != "free"), None)
        return TalentAvailability(
            status=status,
            committed_days=now.committed_days,
            committed_from=changes,
            free_from=None,
            until=until,
        )
    return TalentAvailability(
        status=status,
        committed_days=now.committed_days,
        committed_from=None,
        free_from=next((week.starts_on for week in later if _status(week) == "free"), None),
        until=until,
    )


def free(until: date) -> TalentAvailability:
    """A freelancer the CRM holds no live engagement for in the window."""
    return TalentAvailability(
        status="free", committed_days=Decimal(0), committed_from=None, free_from=None, until=until
    )


def of_one(answer: CrmAvailability) -> TalentAvailability:
    """The summary of one freelancer's own read (`FreelancerDoor.availability`)."""
    return summarize(answer.weeks, answer.date_to)


@dataclass(frozen=True)
class Commitments:
    """The CRM's one answer for a list, and the summary of any card in it."""

    read: TalentCommitments

    def of(self, freelancer_id: UUID) -> TalentAvailability:
        return self.read.freelancers.get(freelancer_id) or free(self.read.date_to)


def read_commitments(door: FreelancerDoor) -> Commitments:
    """Every committed freelancer's availability, in one read of the CRM, over its default window
    (this week and the eleven after). Raises `PigroUnavailable` when the CRM cannot answer."""
    answer = door.commitments()
    return Commitments(
        TalentCommitments(
            date_from=answer.date_from,
            date_to=answer.date_to,
            freelancers={
                item.hub_freelancer_id: summarize(item.weeks, answer.date_to)
                for item in answer.freelancers
            },
        )
    )
