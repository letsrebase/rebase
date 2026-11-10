"""The engagement's rules, as pure functions: what a value may be, which state may follow which,
what overlaps and how many days a week are committed. Nothing here reads a session.

Messages are Italian: a person or an assistant reads them as they are.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from pigrocrm.core.collaborators.models import (
    COLLECTION_DAYS_LIMIT,
    DAYS_PER_WEEK_MAX,
    DAYS_PER_WEEK_MIN,
    ENGAGEMENT_STATES,
    FEE_UNITS,
    LETTER_TYPES,
    PAYMENT_DAYS_LIMIT,
    PAYMENT_DAYS_LIMIT_MONTH_END,
    PAYMENT_RULES,
)
from pigrocrm.core.errors import ValidationFailed

ENTITY: Final = "engagement"
CENT = Decimal("0.01")
HALF = Decimal("0.5")
# What a freelancer can commit to in one week: the top of the `days_per_week` range.
WEEK_CAPACITY = DAYS_PER_WEEK_MAX

# Which state may follow which (design § 1: the hub's own state names). A draft goes out for
# signature or is cancelled; a document out for signature is signed, which makes the engagement
# active, or cancelled; an active one is concluded. `concluded` and `cancelled` are final.
# Nothing moves back: an engagement that was sent and must change is cancelled and redone.
TRANSITIONS: Final[dict[str, tuple[str, ...]]] = {
    "draft": ("awaiting_signature", "cancelled"),
    "awaiting_signature": ("active", "cancelled"),
    "active": ("concluded",),
    "concluded": (),
    "cancelled": (),
}


def one_of(field: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValidationFailed(
            ENTITY, field, f"valore non valido: {value}", expected=" | ".join(allowed)
        )
    return value


def check_state(state: str) -> str:
    return one_of("state", state, ENGAGEMENT_STATES)


def check_transition(current: str, target: str) -> None:
    """Raises when `target` cannot follow `current`."""
    check_state(target)
    if target not in TRANSITIONS[current]:
        follows = ", ".join(TRANSITIONS[current]) or "nessuno: e' uno stato finale"
        raise ValidationFailed(
            ENTITY,
            "state",
            f"da «{current}» non si passa a «{target}»",
            expected=follows,
        )


def check_fee(fee: Decimal, unit: str) -> Decimal:
    """A fee above zero with at most two decimals, the page's own precision."""
    one_of("fee_unit", unit, FEE_UNITS)
    if fee <= 0:
        raise ValidationFailed(ENTITY, "fee", "il compenso deve essere maggiore di zero")
    if fee != fee.quantize(CENT):
        raise ValidationFailed(ENTITY, "fee", "il compenso ha al massimo due decimali")
    return fee


def check_expected_days(expected_days: Decimal | None) -> Decimal | None:
    if expected_days is None:
        return None
    if expected_days <= 0:
        raise ValidationFailed(
            ENTITY, "expected_days", "i giorni previsti devono essere maggiori di zero"
        )
    if expected_days != expected_days.quantize(CENT):
        raise ValidationFailed(
            ENTITY, "expected_days", "i giorni previsti hanno al massimo due decimali"
        )
    return expected_days


def check_days_per_week(days: Decimal | None) -> Decimal | None:
    """From half a day to five, in steps of half a day."""
    if days is None:
        return None
    if not DAYS_PER_WEEK_MIN <= days <= DAYS_PER_WEEK_MAX or days % HALF != 0:
        raise ValidationFailed(
            ENTITY,
            "days_per_week",
            "i giorni a settimana vanno da 0,5 a 5, a passi di 0,5",
            expected="0.5 | 1 | 1.5 | ... | 5",
        )
    return days


def check_dates(starts_on: dt.date, ends_on: dt.date | None) -> None:
    if ends_on is not None and ends_on < starts_on:
        raise ValidationFailed(
            ENTITY, "ends_on", "la fine non puo' essere prima dell'inizio", expected=">= starts_on"
        )


def check_letter_type(letter_type: str, fiscal_kind: str | None) -> str:
    """`ordinary` or `occasional`, and the one the freelancer's classified kind asks for: an
    occasional freelancer is paid on a receipt, anyone else on an invoice."""
    one_of("letter_type", letter_type, LETTER_TYPES)
    if fiscal_kind is not None and (fiscal_kind == "occasional") != (letter_type == "occasional"):
        wanted = "occasional" if fiscal_kind == "occasional" else "ordinary"
        raise ValidationFailed(
            ENTITY,
            "letter_type",
            f"il freelance e' «{fiscal_kind}»: la lettera e' «{wanted}»",
            expected=wanted,
        )
    return letter_type


def default_letter_type(fiscal_kind: str | None) -> str:
    return "occasional" if fiscal_kind == "occasional" else "ordinary"


@dataclass(frozen=True)
class PaymentRule:
    rule: str
    payment_days: int | None
    payment_end_of_month: bool
    collection_days: int | None


def check_payment_rule(
    rule: str, payment_days: int | None, end_of_month: bool, collection_days: int | None
) -> PaymentRule:
    """The payment rule of a letter (design § 4), and the 60 days of law 81/2017 art. 3 that no
    letter can pass.

    `fixed_term`: `payment_days` from 1 to 60 (30 when counted from the end of the month, which
    adds up to 30 more) and no `collection_days`. `on_collection`: `collection_days` from 1 to 60
    and **no fixed term**: the printed clause of the letter names no floor, so a stored term here
    would be a rule the letter does not say. Either way the freelancer is paid within 60 days of
    receiving the invoice, which the due date applies as a ceiling.
    """
    one_of("payment_rule", rule, PAYMENT_RULES)
    if rule == "fixed_term":
        if collection_days is not None:
            raise ValidationFailed(
                ENTITY,
                "collection_days",
                "i giorni dall'incasso valgono solo se il pagamento segue il cliente",
                expected="payment_rule on_collection",
            )
        if payment_days is None:
            raise ValidationFailed(
                ENTITY, "payment_days", "serve il termine di pagamento, in giorni dalla fattura"
            )
        limit = PAYMENT_DAYS_LIMIT_MONTH_END if end_of_month else PAYMENT_DAYS_LIMIT
        if not 0 < payment_days <= limit:
            where = ", se contati dalla fine del mese," if end_of_month else ","
            raise ValidationFailed(
                ENTITY,
                "payment_days",
                f"il termine di pagamento e' di {payment_days} giorni: da 1 a {limit}{where}"
                " altrimenti la lettera supera i 60 giorni della legge 81/2017",
                expected=f"1..{limit}",
            )
        return PaymentRule(rule, payment_days, end_of_month, None)
    if payment_days is not None or end_of_month:
        raise ValidationFailed(
            ENTITY,
            "payment_days",
            "se il pagamento segue il cliente non c'e' un termine fisso: toglilo",
            expected="payment_days vuoto",
        )
    if collection_days is None:
        raise ValidationFailed(
            ENTITY,
            "collection_days",
            "servono i giorni dall'incasso del cliente",
            expected=f"1..{COLLECTION_DAYS_LIMIT}",
        )
    if not 0 < collection_days <= COLLECTION_DAYS_LIMIT:
        raise ValidationFailed(
            ENTITY,
            "collection_days",
            f"i giorni dall'incasso sono {collection_days}: da 1 a {COLLECTION_DAYS_LIMIT},"
            " altrimenti la lettera supera i 60 giorni della legge 81/2017",
            expected=f"1..{COLLECTION_DAYS_LIMIT}",
        )
    return PaymentRule(rule, None, False, collection_days)


# ---- weeks and what is committed in them ----------------------------------------------------


@dataclass(frozen=True)
class Commitment:
    """The part of an engagement the week arithmetic reads."""

    id: UUID
    starts_on: dt.date
    ends_on: dt.date | None
    days_per_week: Decimal | None


def monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def iso_week(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def overlaps(
    first_start: dt.date,
    first_end: dt.date | None,
    second_start: dt.date,
    second_end: dt.date | None,
) -> bool:
    """Whether two closed date ranges share a day; no end is open-ended."""
    return (first_end is None or first_end >= second_start) and (
        second_end is None or second_end >= first_start
    )


def weeks(date_from: dt.date, date_to: dt.date) -> list[tuple[dt.date, dt.date]]:
    """The ISO weeks (Monday, Sunday) that touch `date_from` to `date_to`, both included."""
    out: list[tuple[dt.date, dt.date]] = []
    start = monday(date_from)
    while start <= date_to:
        out.append((start, start + dt.timedelta(days=6)))
        start += dt.timedelta(days=7)
    return out


def committed_in(
    week_start: dt.date, week_end: dt.date, commitments: Iterable[Commitment]
) -> tuple[Decimal, bool, list[UUID]]:
    """The days a week is committed to by `commitments`, whether one of them lacks its
    `days_per_week` (the week is then unknown), and which of them touch the week. An
    engagement counts its full `days_per_week` in every week it touches, a part-week included:
    the answer errs on the side of busy."""
    total = Decimal(0)
    unknown = False
    ids: list[UUID] = []
    for commitment in commitments:
        if not overlaps(week_start, week_end, commitment.starts_on, commitment.ends_on):
            continue
        ids.append(commitment.id)
        if commitment.days_per_week is None:
            unknown = True
        else:
            total += commitment.days_per_week
    return total, unknown, ids


def peak_commitment(
    starts_on: dt.date,
    ends_on: dt.date | None,
    days_per_week: Decimal | None,
    others: Sequence[Commitment],
) -> tuple[Decimal | None, list[UUID]]:
    """The most days a week the freelancer is committed to while a new engagement runs, it and
    the `others` that overlap it together: `None` when the new one or any of them has no
    `days_per_week`. An open-ended range is read up to the last day that can change the total
    (the latest start or end among the ones that overlap): after it the total is constant."""
    touching = [o for o in others if overlaps(starts_on, ends_on, o.starts_on, o.ends_on)]
    ids = [o.id for o in touching]
    if days_per_week is None or any(o.days_per_week is None for o in touching):
        return None, ids
    last = ends_on
    if last is None:
        last = max(
            [
                starts_on,
                *(o.starts_on for o in touching),
                *(o.ends_on for o in touching if o.ends_on),
            ]
        )
    peak = Decimal(0)
    for week_start, week_end in weeks(starts_on, last):
        total, _, _ = committed_in(week_start, week_end, touching)
        peak = max(peak, total + days_per_week)
    return peak, ids
