"""Every detector, in the order the catalogue lists its types.

A card that adds a group of types adds its module here and its rows to
`reminders/catalogue.py`; `test_reminders_detectors.py` fails when the two do not name the
same types, so a catalogue row without a detector, or a detector the settings cannot see,
is a red test and not a silent type.
"""

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import replace

from pigrocrm.core.modules import ModuleKey
from pigrocrm.core.reminders.detect import Detector
from pigrocrm.core.reminders.detectors import (
    billing,
    billing_contracts,
    contracts,
    days,
    deals,
    space,
    space_fiscal,
    tasks,
    tax_dates,
    timers,
)


def _of(modules: tuple[ModuleKey, ...], detectors: Iterable[Detector]) -> tuple[Detector, ...]:
    """`detectors`, each tied to the modules it reads: the engine skips it where one is off."""
    return tuple(replace(detector, modules=modules) for detector in detectors)


DETECTORS: tuple[Detector, ...] = (
    *tasks.DETECTORS,
    *_of(("deals",), deals.DETECTORS),
    *_of(("time",), timers.DETECTORS),
    *(
        replace(detector, modules=("time",)) if detector.kind.key == "hours_unbilled" else detector
        for detector in billing.DETECTORS
    ),
    *_of(("contracts",), billing_contracts.DETECTORS),
    *_of(("contracts",), contracts.DETECTORS),
    *_of(("contracts",), days.DETECTORS),
    *_of(("taxes",), space_fiscal.DETECTORS),
    *_of(("taxes",), tax_dates.DETECTORS),
    *space.DETECTORS,
)


def off_types(modules: AbstractSet[str] | None) -> frozenset[str]:
    """The type keys a space with `modules` on does not have: the ones whose detector reads a
    module that is off (`None`: everything is on). The engine skips those detectors, and the
    catalogue and the preferences leave the same types out, so what a person can tune is what
    can reach them."""
    if modules is None:
        return frozenset()
    return frozenset(
        detector.kind.key for detector in DETECTORS if not set(detector.modules) <= modules
    )
