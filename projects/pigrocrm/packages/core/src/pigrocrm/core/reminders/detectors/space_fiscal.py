"""The space-wide group, part two: `ceiling_approaching` and `year_end_overrun_projected`
(spec 2026-10-26 §3.5). The rest of the section is `space.py`.

**«Applies when the fiscal profile is the forfettario»** is an azienda's own profile, so the
detectors walk the active aziende whose `fiscal_profile` points at the Italian flat-rate
pack (`PACK_IT_FLAT_RATE`) **and** carries the forfettario's regime code (`FORFETTARIO.codice`,
`RF19`). An azienda on an ordinary regime (`RF01`), on the foreign pack, or with no profile
at all gets nothing, and a space none of whose aziende qualifies skips both types.

**One ceiling, the revenue one.** The pack declares two: `soglia_ricavi` (85,000, the regime
is lost from the next year) and `soglia_fuoriuscita_immediata` (100,000, lost at once). Both
types read the first, and the text of the reminder names the second when the figures are
close to it, because that is the one that costs the regime this year.

**Nothing is computed twice.** The year's revenue and its ceilings come from `evaluate_pack`
(`fiscal/ceiling.py`, the function `AnalyticsService.ceiling_headroom` calls), and the
projection of the year from `AnalyticsService.cash_overview`, whose `proiettato` is what
the dashboard's projection column adds up: paid, issued and unpaid, and drafts, by the
months the money is due (`base="incasso"`, the basis of the pack's own ceilings). The two
are not the same base to the cent (the projection sums `totale`, the ceiling `imponibile`),
and under the forfettario they are the same number: no VAT, and the bollo is never charged
to the customer.

**Measure: the integer percent of the ceiling**, rounded down, so 89.99% is 89 and not yet
the 90 of the `serious` rung.

Recipients are the admins whose scope covers the azienda (`space.admins_seeing`): an
admin sees every azienda unless somebody limited them, and then only theirs.
"""

from __future__ import annotations

import weakref
from decimal import Decimal
from uuid import uuid5

from sqlalchemy import select

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.fiscal.ceiling import CeilingStatus, evaluate_pack
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, Ceiling, FiscalPack, resolve_pack
from pigrocrm.core.fiscal.regime import FORFETTARIO
from pigrocrm.core.mail import euro
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.space import admins_seeing

APPROACHING = REMINDER_TYPES["ceiling_approaching"]
OVERRUN = REMINDER_TYPES["year_end_overrun_projected"]

REVENUE_CEILING = "soglia_ricavi"
IMMEDIATE_CEILING = "soglia_fuoriuscita_immediata"

_HUNDRED = Decimal(100)


_Aziende = list[tuple[LegalEntity, FiscalPack]]

# One evaluation builds one `DetectorContext` and hands it to `applies` and `detect` of
# both types, so the aziende are read once per evaluation and shared by the four calls.
# Keyed by the context's identity and dropped when the context is collected: the cache
# never outlives an evaluation, so it can never show a later one the data of an earlier.
_LOADED: dict[int, tuple[weakref.ref[DetectorContext], _Aziende]] = {}


def _forfettario_aziende(ctx: DetectorContext) -> _Aziende:
    """The active aziende under the forfettario with the pack that governs them, the
    default azienda first, read once per evaluation."""
    key = id(ctx)
    hit = _LOADED.get(key)
    if hit is not None and hit[0]() is ctx:
        return hit[1]
    found = _load_aziende(ctx)
    _LOADED[key] = (weakref.ref(ctx, lambda _: _LOADED.pop(key, None)), found)
    return found


def _load_aziende(ctx: DetectorContext) -> _Aziende:
    """The active aziende under the forfettario with the pack that governs them, the
    default azienda first."""
    rows = ctx.session.execute(
        select(LegalEntity, FiscalProfile)
        .join(FiscalProfile, FiscalProfile.azienda_id == LegalEntity.id)
        .where(
            LegalEntity.attiva.is_(True),
            FiscalProfile.pack_id == PACK_IT_FLAT_RATE,
            FiscalProfile.codice_regime == FORFETTARIO.codice,
        )
        .order_by(LegalEntity.predefinita.desc(), LegalEntity.nome, LegalEntity.id)
    )
    found: list[tuple[LegalEntity, FiscalPack]] = []
    for azienda, profile in rows:
        try:
            pack = resolve_pack(profile.pack_id, profile.pack_version)
        except KeyError:
            # A version this build does not ship: nothing to evaluate, and a pass over
            # the other aziende must not die of it.
            continue
        if any(ceiling.id == REVENUE_CEILING for ceiling in pack.ceilings):
            found.append((azienda, pack))
    return found


def _applies(ctx: DetectorContext) -> bool:
    return bool(_forfettario_aziende(ctx))


def _ceiling(pack: FiscalPack, ceiling_id: str) -> Ceiling | None:
    return next((ceiling for ceiling in pack.ceilings if ceiling.id == ceiling_id), None)


def _percent(amount: Decimal, ceiling: Decimal) -> int:
    """`amount` as a whole percent of `ceiling`, rounded down."""
    return int((amount * _HUNDRED) // ceiling)


def _immediate_note(status: CeilingStatus | None) -> str:
    """The second ceiling, named when it is near or crossed: it ends the regime in the
    year itself, which is the thing the reader would otherwise find out too late."""
    if status is None or status.livello_allerta is None:
        return ""
    if status.superata:
        return (
            f" · Hai superato anche {euro(status.ceiling.soglia)}: oltre quella cifra "
            "il regime forfettario finisce subito, nell'anno stesso"
        )
    return (
        f" · Oltre {euro(status.ceiling.soglia)} il regime forfettario finisce subito, "
        f"nell'anno stesso: te ne separano {euro(status.residuo)}"
    )


def detect_approaching(ctx: DetectorContext) -> list[Detection]:
    """Aziende whose paid revenue of the year has reached `bound` percent of the revenue
    ceiling."""
    anno = ctx.today.year
    found: list[Detection] = []
    for azienda, pack in _forfettario_aziende(ctx):
        recipients = admins_seeing(ctx, azienda.id)
        if not recipients:
            continue
        statuses = {
            status.ceiling.id: status
            for status in evaluate_pack(pack, ctx.session, anno, azienda.id)
        }
        status = statuses[REVENUE_CEILING]
        percent = _percent(status.ricavi, status.ceiling.soglia)
        if percent < ctx.bound(APPROACHING):
            continue
        if status.superata:
            detail = (
                f"Hai incassato {euro(status.ricavi)} nel {anno}, oltre la soglia di "
                f"{euro(status.ceiling.soglia)}: dall'anno successivo il regime forfettario "
                "non vale più"
            )
        else:
            detail = (
                f"Hai incassato {euro(status.ricavi)} nel {anno}, il {percent}% della soglia di "
                f"{euro(status.ceiling.soglia)}: ne restano {euro(status.residuo)}"
            )
        found.append(
            Detection(
                type="ceiling_approaching",
                subject_id=azienda.id,
                measure=float(percent),
                recipients=recipients,
                title=f"Soglia del forfettario al {percent}%: {azienda.nome}",
                detail=detail + _immediate_note(statuses.get(IMMEDIATE_CEILING)),
                entity="fiscal",
                entity_id=azienda.id,
                azienda_id=azienda.id,
            )
        )
    return found


def detect_overrun(ctx: DetectorContext) -> list[Detection]:
    """Aziende whose projected year (`cash_overview.proiettato`) is `bound` percent of the
    revenue ceiling or more."""
    anno = ctx.today.year
    analytics = AnalyticsService(ctx.session)
    found: list[Detection] = []
    for azienda, pack in _forfettario_aziende(ctx):
        recipients = admins_seeing(ctx, azienda.id)
        ceiling = _ceiling(pack, REVENUE_CEILING)
        if not recipients or ceiling is None:
            continue
        proiettato = analytics.cash_overview(anno, Actor.system(), "incasso", azienda.id).proiettato
        percent = _percent(proiettato, ceiling.soglia)
        if percent < ctx.bound(OVERRUN):
            continue
        immediate = _ceiling(pack, IMMEDIATE_CEILING)
        note = ""
        if immediate is not None and proiettato >= immediate.soglia:
            note = (
                f" · Supera anche {euro(immediate.soglia)}: in quel caso il regime forfettario "
                "finisce subito, nell'anno stesso"
            )
        found.append(
            Detection(
                type="year_end_overrun_projected",
                # One occurrence per azienda and year: next year's projection is a new
                # one, and nothing acknowledged this year may silence it.
                subject_id=uuid5(azienda.id, str(anno)),
                measure=float(percent),
                recipients=recipients,
                title=f"A fine anno oltre la soglia del forfettario: {azienda.nome}",
                detail=(
                    f"Fra incassato, da incassare e bozze il {anno} arriva a {euro(proiettato)}, "
                    f"il {percent}% della soglia di {euro(ceiling.soglia)}" + note
                ),
                entity="fiscal",
                entity_id=azienda.id,
                azienda_id=azienda.id,
            )
        )
    return found


DETECTORS = (
    Detector(APPROACHING, detect_approaching, _applies),
    Detector(OVERRUN, detect_overrun, _applies),
)
