"""The modules a space can switch (spec 2026-11-09, A2 and B2): which exist, what each one
needs, what a profile switches on by default, and what a `Settings` says is on right now.

A module is a group of features that is on or off for the whole space. Off means gone from
every surface (REST answers 404 with a sentence, the MCP tools are not registered, the
navigation and the jobs skip it) and the data stays where it is. This file is the one
place that decides *whether* a module is on; each surface asks it and never reads the
settings keys itself. Which routes, tools, tabs and jobs belong to a module is each
surface's own table, in the adapter that owns the surface, since core knows no adapter.

A module that is not built is not listed here: `purchases`, `cash`, `collaborators` and
`staff` become rows when the card that builds them ships.
"""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal, Protocol

from pigrocrm.core.errors import ModuleOff

ModuleKey = Literal["deals", "contracts", "time", "costs", "taxes"]
Profile = Literal["solo", "company"]

PROFILES: tuple[Profile, ...] = ("solo", "company")


@dataclass(frozen=True)
class Module:
    key: ModuleKey
    # The words a person reads (Italian, like every user-facing string).
    label: str
    description: str
    # A module cannot be on while one it needs is off.
    requires: tuple[ModuleKey, ...]
    # What each profile switches on when the space has no row for the module.
    default: Mapping[Profile, bool]


MODULES: tuple[Module, ...] = (
    Module(
        "deals",
        "Deal",
        "Pipeline, deal, righe di valore e offerte.",
        (),
        {"solo": False, "company": True},
    ),
    Module(
        "contracts",
        "Contratti",
        "Contratti, listini, giornate lavorate, approvazioni e registro delle giornate.",
        (),
        {"solo": True, "company": True},
    ),
    Module(
        "time",
        "Ore",
        "Ore registrate sui deal, timer e tariffe.",
        ("deals",),
        {"solo": False, "company": True},
    ),
    Module(
        "costs",
        "Costi",
        "Costi e categorie di costo.",
        (),
        {"solo": True, "company": True},
    ),
    Module(
        "taxes",
        "Tasse",
        "Stima fiscale, soglie di fatturato e riepilogo dell'anno.",
        (),
        {"solo": True, "company": True},
    ),
)
MODULES_BY_KEY: Mapping[str, Module] = {module.key: module for module in MODULES}
MODULE_KEYS: tuple[ModuleKey, ...] = tuple(module.key for module in MODULES)

PROFILE_KEY = "space_profile"


def setting_key(module: str) -> str:
    """The `Settings` field and `space_settings` row of a module's switch."""
    return f"module_{module}"


MODULE_SETTING_KEYS: tuple[str, ...] = tuple(setting_key(key) for key in MODULE_KEYS)
# What only the two actions write: never `SpaceSettingsUpdate` (`space_settings/schemas.py`).
SHAPE_KEYS: tuple[str, ...] = (PROFILE_KEY, *MODULE_SETTING_KEYS)


class _Shaped(Protocol):
    space_profile: Profile | None
    module_deals: bool | None
    module_contracts: bool | None
    module_time: bool | None
    module_costs: bool | None
    module_taxes: bool | None


def profile_default(profile: Profile | None, module: ModuleKey) -> bool:
    """What a module reads as with no row of its own: the profile's default, and on while
    the space has no profile (everything stays on until somebody chooses, B3)."""
    if profile is None:
        return True
    return MODULES_BY_KEY[module].default[profile]


def stored_switch(settings: _Shaped, module: ModuleKey) -> bool | None:
    """The row the space wrote for `module`, `None` when it has none."""
    value: bool | None = getattr(settings, setting_key(module))
    return value


def enabled_modules(settings: _Shaped) -> frozenset[ModuleKey]:
    """The modules that are on: a stored switch over the profile's default, then the
    dependencies, so a module whose requirement is off reads as off whatever its own row
    says (the actions never write such a state, a hand-edited row could)."""
    on: set[ModuleKey] = set()
    for module in MODULES:  # `MODULES` lists a requirement before what needs it
        stored = stored_switch(settings, module.key)
        default = profile_default(settings.space_profile, module.key)
        wanted = default if stored is None else stored
        if wanted and all(need in on for need in module.requires):
            on.add(module.key)
    return frozenset(on)


def module_enabled(settings: _Shaped, module: ModuleKey) -> bool:
    return module in enabled_modules(settings)


def missing_modules(settings: _Shaped, needed: Iterable[ModuleKey]) -> list[ModuleKey]:
    """Which of `needed` are off: a surface that spans several modules exists only when
    this is empty."""
    on = enabled_modules(settings)
    return [module for module in needed if module not in on]


def require_modules(settings: _Shaped, *needed: ModuleKey) -> None:
    """Raises `ModuleOff` naming the first of `needed` that is off."""
    require_in(enabled_modules(settings), *needed)


def require_in(on: AbstractSet[str], *needed: ModuleKey) -> None:
    """The same, for a surface that already holds the set of modules that are on (the MCP
    context fixes it when the server is built)."""
    missing = [module for module in needed if module not in on]
    if missing:
        raise ModuleOff(missing[0], MODULES_BY_KEY[missing[0]].label)
