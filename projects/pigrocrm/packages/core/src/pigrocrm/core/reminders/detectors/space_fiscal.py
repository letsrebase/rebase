"""The space-wide group, part two: `ceiling_approaching` and `year_end_overrun_projected`
(spec 2026-10-26 §3.5). The rest of the section is `space.py`.

**«Applies when the fiscal profile is the forfettario»** is a legal entity's own profile, so the
detectors walk the active legal entities whose `fiscal_profile` points at the Italian flat-rate
pack (`PACK_IT_FLAT_RATE`) **and** carries the flat-rate regime's regime code (`FLAT_RATE.code`,
`RF19`). A legal entity on an ordinary regime (`RF01`), on the foreign pack, or with no profile
at all gets nothing, and a space none of whose legal entities qualifies skips both types.

**One ceiling, the revenue one.** The pack declares two: `soglia_ricavi` (85,000, the regime
is lost from the next year) and `soglia_fuoriuscita_immediata` (100,000, lost at once). Both
types read the first, and the text of the reminder names the second when the figures are
close to it, because that is the one that costs the regime this year.

**Nothing is computed twice.** The year's revenue and its ceilings come from `evaluate_pack`
(`fiscal/ceiling.py`, the function `AnalyticsService.ceiling_headroom` calls), and the
projection of the year from `AnalyticsService.cash_overview`, whose `projected` is what
the dashboard's projection column adds up: paid, issued and unpaid, and drafts, by the
months the money is due (`base="collection"`, the basis of the pack's own ceilings). The two
are not the same base to the cent (the projection sums `total`, the ceiling `taxable_amount`),
and under the flat-rate regime they are the same number: no VAT, and the stamp duty is never charged
to the customer.

**Measure: the integer percent of the ceiling**, rounded down, so 89.99% is 89 and not yet
the 90 of the `serious` rung.

Recipients are the admins whose scope covers the legal entity (`space.admins_seeing`): an
admin sees every legal entity unless somebody limited them, and then only theirs.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid5

from sqlalchemy import select

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.fiscal.ceiling import CeilingStatus, evaluate_pack
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, Ceiling, FiscalPack, resolve_pack
from pigrocrm.core.fiscal.regime import FLAT_RATE
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.mail import euro
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.space import admins_seeing

APPROACHING = REMINDER_TYPES["ceiling_approaching"]
OVERRUN = REMINDER_TYPES["year_end_overrun_projected"]

REVENUE_CEILING = "soglia_ricavi"
IMMEDIATE_CEILING = "soglia_fuoriuscita_immediata"

_HUNDRED = Decimal(100)

_LegalEntities = list[tuple[LegalEntity, FiscalPack]]


def _load_legal_entities(ctx: DetectorContext) -> _LegalEntities:
    """The active legal entities under the flat-rate regime with the pack that governs them, the
    default legal entity first. Asked for through `ctx.once`: the two types and their «applies
    when» read it once per evaluation."""
    rows = ctx.session.execute(
        select(LegalEntity, FiscalProfile)
        .join(FiscalProfile, FiscalProfile.legal_entity_id == LegalEntity.id)
        .where(
            LegalEntity.active.is_(True),
            FiscalProfile.pack_id == PACK_IT_FLAT_RATE,
            FiscalProfile.regime_code == FLAT_RATE.code,
        )
        .order_by(LegalEntity.is_default.desc(), LegalEntity.name, LegalEntity.id)
    )
    found: list[tuple[LegalEntity, FiscalPack]] = []
    for legal_entity, profile in rows:
        try:
            pack = resolve_pack(profile.pack_id, profile.pack_version)
        except KeyError:
            # A version this build does not ship: nothing to evaluate, and a pass over
            # the other legal entities must not die of it.
            continue
        if any(ceiling.id == REVENUE_CEILING for ceiling in pack.ceilings):
            found.append((legal_entity, pack))
    return found


def _applies(ctx: DetectorContext) -> bool:
    return bool(ctx.once(_load_legal_entities))


def _ceiling(pack: FiscalPack, ceiling_id: str) -> Ceiling | None:
    return next((ceiling for ceiling in pack.ceilings if ceiling.id == ceiling_id), None)


def _percent(amount: Decimal, ceiling: Decimal) -> int:
    """`amount` as a whole percent of `ceiling`, rounded down."""
    return int((amount * _HUNDRED) // ceiling)


def _immediate_note(status: CeilingStatus | None) -> str:
    """The second ceiling, named when it is near or crossed: it ends the regime in the
    year itself, which is the thing the reader would otherwise find out too late."""
    if status is None or status.alert_level is None:
        return ""
    if status.exceeded:
        return (
            f" · Hai superato anche {euro(status.ceiling.threshold)}: oltre quella cifra "
            "il regime forfettario finisce subito, nell'anno stesso"
        )
    return (
        f" · Oltre {euro(status.ceiling.threshold)} il regime forfettario finisce subito, "
        f"nell'anno stesso: te ne separano {euro(status.remaining)}"
    )


def detect_approaching(ctx: DetectorContext) -> list[Detection]:
    """Legal entities whose paid revenue of the year has reached `bound` percent of the revenue
    ceiling."""
    year = ctx.today.year
    found: list[Detection] = []
    for legal_entity, pack in ctx.once(_load_legal_entities):
        recipients = admins_seeing(ctx, legal_entity.id)
        if not recipients:
            continue
        statuses = {
            status.ceiling.id: status
            for status in evaluate_pack(pack, ctx.session, year, legal_entity.id)
        }
        status = statuses[REVENUE_CEILING]
        percent = _percent(status.revenue, status.ceiling.threshold)
        if percent < ctx.bound(APPROACHING):
            continue
        if status.exceeded:
            detail = (
                f"Hai incassato {euro(status.revenue)} nel {year}, oltre la soglia di "
                f"{euro(status.ceiling.threshold)}: dall'anno successivo il regime forfettario "
                "non vale più"
            )
        else:
            detail = (
                f"Hai incassato {euro(status.revenue)} nel {year}, il {percent}% della soglia di "
                f"{euro(status.ceiling.threshold)}: ne restano {euro(status.remaining)}"
            )
        found.append(
            Detection(
                type="ceiling_approaching",
                subject_id=legal_entity.id,
                measure=float(percent),
                recipients=recipients,
                title=f"Soglia del forfettario al {percent}%: {legal_entity.name}",
                detail=detail + _immediate_note(statuses.get(IMMEDIATE_CEILING)),
                entity="fiscal",
                entity_id=legal_entity.id,
                legal_entity_id=legal_entity.id,
            )
        )
    return found


def detect_overrun(ctx: DetectorContext) -> list[Detection]:
    """Legal entities whose projected year (`cash_overview.projected`) is `bound` percent of the
    revenue ceiling or more."""
    year = ctx.today.year
    analytics = AnalyticsService(ctx.session)
    found: list[Detection] = []
    for legal_entity, pack in ctx.once(_load_legal_entities):
        recipients = admins_seeing(ctx, legal_entity.id)
        ceiling = _ceiling(pack, REVENUE_CEILING)
        if not recipients or ceiling is None:
            continue
        projected = analytics.cash_overview(
            year, Actor.system(), "collection", legal_entity.id
        ).projected
        percent = _percent(projected, ceiling.threshold)
        if percent < ctx.bound(OVERRUN):
            continue
        immediate = _ceiling(pack, IMMEDIATE_CEILING)
        note = ""
        if immediate is not None and projected >= immediate.threshold:
            note = (
                f" · Supera anche {euro(immediate.threshold)}: in quel caso il regime forfettario "
                "finisce subito, nell'anno stesso"
            )
        found.append(
            Detection(
                type="year_end_overrun_projected",
                # One occurrence per legal entity and year: next year's projection is a new
                # one, and nothing acknowledged this year may silence it.
                subject_id=uuid5(legal_entity.id, str(year)),
                measure=float(percent),
                recipients=recipients,
                title=f"A fine anno oltre la soglia del forfettario: {legal_entity.name}",
                detail=(
                    f"Fra incassato, da incassare e bozze il {year} arriva a {euro(projected)}, "
                    f"il {percent}% della soglia di {euro(ceiling.threshold)}" + note
                ),
                entity="fiscal",
                entity_id=legal_entity.id,
                legal_entity_id=legal_entity.id,
            )
        )
    return found


DETECTORS = (
    Detector(APPROACHING, detect_approaching, _applies),
    Detector(OVERRUN, detect_overrun, _applies),
)
