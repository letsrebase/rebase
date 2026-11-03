"""Rate resolution -- the one place §5.1's order is expressed, and the only place it
is ever consulted.

The whole design turns on one question: what happens to last quarter's margin when
you raise a rate today? The answer is "nothing", and not out of discipline. A rate is
resolved exactly once, at write time, and copied onto the row; from then on no report
reads `deals.hourly_rate` or `users.default_hourly_rate` at all. The row is the
authority on itself, always and only -- the same shape as `invoices.snapshot` (slice 3
§8.3), applied to the smallest unit.

`rate_history(user_id, valido_da, valido_a, tariffa)` was considered and rejected for
three reasons, all verifiable. It does not remove the copy, it duplicates it: even
with a history, an entry *back-dated* today would read the rate of the period it names
and change a total somebody has already read, so the value would still have to be
frozen -- and then the history is a second place the same truth can diverge. It turns
every read into a temporal join whose validity intervals are editable, so correcting a
`valid_from` silently rewrites the margins of every period it covers. And the question
it would answer -- "when did Marco's rate change?" -- is already answered better by
the timeline (§4.5), which is also the only place that says *who* changed it. The same
reasoning slice 3 §7.1 used to refuse historicising `fiscal_profile`.

There is deliberately no "this person's rate on this deal" level. It is the fourth
level every system of this kind eventually grows, and it stays out because nobody
exercises it today: the audience is a freelancer or a two-person studio, where the
rate is set by the contract with the client (level `deal`) and the exception is
written on the single entry (level `manual`). The extension path, if it is ever
needed, is a `deal_user_rates(deal_id, user_id, tariffa)` table inserted between
levels 1 and 2 -- no new column on any existing table, and the freezing above makes it
invisible to every row already written.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import NotFound
from pigrocrm.core.timetracking.schemas import CostOrigin, RateDescription, RateOrigin


@dataclass(frozen=True)
class ResolvedRates:
    rate: Decimal | None
    rate_source: RateOrigin
    cost: Decimal | None
    cost_source: CostOrigin


class RateResolver:
    """A helper, not a service: no `Actor`, no transaction, no commit.

    That distinction is load-bearing beyond taste. The architecture test in Task
    4A-13 audits the public methods of the *services* in this slice and demands that
    each either has an MCP tool or appears in the ten-name exclusion list. A resolver
    that took an `Actor` would look like a service and force a spurious entry into
    one of those two lists.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _deal(self, deal_id: UUID) -> Deal:
        deal = self.session.get(Deal, deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        return deal

    def _user(self, user_id: UUID) -> User:
        # Plain `get`, not `get_active`: reading the rate of a deactivated user is
        # correct -- their historical hours stay in the P&L with their name, because
        # the money really was spent (residual R3's answer). Refusing to *assign* new
        # hours to them is `TimeEntryService`'s job, through `get_active`, and belongs
        # there rather than here.
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        return user

    def resolve(
        self,
        *,
        deal_id: UUID,
        user_id: UUID,
        explicit_rate: Decimal | None = None,
        cost_explicit: Decimal | None = None,
    ) -> ResolvedRates:
        """Stops at the first level that yields a value.

        `is not None`, never a truthiness test: an explicit `Decimal("0.000000")` is a
        choice -- somebody declaring free work on purpose -- and falling through it to
        a deal rate would overwrite a decision behind their back. `0` is a value,
        never a blank; the same rule `fields/validator.py:is_blank` already states and
        the frontend's `isBlank` mirrors.
        """
        deal = self._deal(deal_id)
        user = self._user(user_id)

        rate: Decimal | None
        rate_source: RateOrigin
        if explicit_rate is not None:
            rate, rate_source = explicit_rate, "manual"
        elif deal.hourly_rate is not None:
            rate, rate_source = deal.hourly_rate, "deal"
        elif user.default_hourly_rate is not None:
            rate, rate_source = user.default_hourly_rate, "user"
        else:
            # `None`, never `0.00`. A silent zero would say "this work was free",
            # which is a lie that sums; a global default would be a number nobody
            # chose quietly becoming everybody's rate. In the P&L these hours appear
            # in the hour count, are excluded from the accrued value and from the
            # margin, and are named on screen as "ore senza tariffa" with their count.
            rate, rate_source = None, "absent"

        cost: Decimal | None
        cost_source: CostOrigin
        if cost_explicit is not None:
            cost, cost_source = cost_explicit, "manual"
        elif user.default_hourly_cost is not None:
            cost, cost_source = user.default_hourly_cost, "user"
        else:
            cost, cost_source = None, "absent"
        # No `deal` branch above, by design: `cost_source` never takes that value,
        # which is why `CostOrigin` is a narrower Literal than `RateOrigin` rather
        # than the same one with a value nobody writes.

        return ResolvedRates(rate, rate_source, cost, cost_source)

    def describe(self, *, deal_id: UUID, user_id: UUID) -> RateDescription:
        """What a new entry would freeze right now, without writing one. Backs the
        `describe_rates` tool and endpoint: reading before acting (slice 1 §8.4)."""
        resolved = self.resolve(deal_id=deal_id, user_id=user_id)
        return RateDescription(
            deal_id=deal_id,
            user_id=user_id,
            rate=resolved.rate,
            rate_source=resolved.rate_source,
            cost=resolved.cost,
            cost_source=resolved.cost_source,
        )
