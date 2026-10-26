"""Every detector, in the order the catalogue lists its types.

A card that adds a group of types adds its module here and its rows to
`reminders/catalogue.py`; `test_reminders_detectors.py` fails when the two do not name the
same types, so a catalogue row without a detector, or a detector the settings cannot see,
is a red test and not a silent type.
"""

from pigrocrm.core.reminders.detect import Detector
from pigrocrm.core.reminders.detectors import (
    attivita,
    billing,
    billing_contracts,
    contracts,
    days,
    deals,
    timers,
)

DETECTORS: tuple[Detector, ...] = (
    *attivita.DETECTORS,
    *deals.DETECTORS,
    *timers.DETECTORS,
    *billing.DETECTORS,
    *billing_contracts.DETECTORS,
    *contracts.DETECTORS,
    *days.DETECTORS,
)
