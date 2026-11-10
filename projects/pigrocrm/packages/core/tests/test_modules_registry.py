"""The modules registry (spec 2026-11-09, A2 and B2): defaults per profile, the precedence of
an explicit switch over the default, the dependency between modules and the sentence a
route answers with when a module is off."""

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ModuleOff
from pigrocrm.core.modules import (
    MODULE_KEYS,
    MODULES,
    enabled_modules,
    missing_modules,
    require_modules,
)
from pigrocrm.core.modules.registry import PROFILE_KEY, SHAPE_KEYS
from pigrocrm.core.space_settings import OVERRIDABLE_KEYS, SpaceSettingsUpdate, apply_overrides


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_with_no_profile_and_no_row_every_module_is_on_but_the_ones_the_onboarding_asks() -> None:
    assert enabled_modules(_settings()) == set(MODULE_KEYS) - {"collaborators"}


def test_collaborators_is_off_by_default_and_on_only_by_its_own_row() -> None:
    """REB-976: a module whose default is an onboarding answer reads as off when the answer is
    not given, whether there is no profile at all or a profile that never asked."""
    for profile in (None, "solo", "company"):
        assert "collaborators" not in enabled_modules(_settings(space_profile=profile))
        assert "collaborators" in enabled_modules(
            _settings(space_profile=profile, module_collaborators=True)
        )
    assert "collaborators" not in enabled_modules(_settings(module_collaborators=False))


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("solo", {"contracts", "purchases", "taxes", "cash"}),
        ("company", {"deals", "contracts", "time", "purchases", "taxes", "cash"}),
    ],
)
def test_a_profile_switches_on_its_defaults(profile: str, expected: set[str]) -> None:
    assert enabled_modules(_settings(space_profile=profile)) == expected


def test_a_stored_switch_outranks_the_profile_default_either_way() -> None:
    assert "deals" in enabled_modules(_settings(space_profile="solo", module_deals=True))
    assert "contracts" not in enabled_modules(
        _settings(space_profile="company", module_contracts=False)
    )
    assert "purchases" not in enabled_modules(_settings(module_purchases=False))


def test_a_module_whose_requirement_is_off_reads_as_off_whatever_its_own_row_says() -> None:
    assert "time" not in enabled_modules(_settings(module_deals=False, module_time=True))
    assert "time" not in enabled_modules(_settings(space_profile="solo", module_time=True))
    assert "time" in enabled_modules(
        _settings(space_profile="solo", module_deals=True, module_time=True)
    )


def test_every_requirement_is_a_module_listed_before_it() -> None:
    seen: set[str] = set()
    for module in MODULES:
        assert set(module.requires) <= seen, f"{module.key} is listed before what it needs"
        seen.add(module.key)


def test_missing_modules_names_the_ones_that_are_off() -> None:
    settings = _settings(space_profile="solo")

    assert missing_modules(settings, ["contracts", "deals", "time"]) == ["deals", "time"]


def test_a_route_of_a_module_that_is_off_is_refused_with_the_sentence_naming_it() -> None:
    settings = _settings(module_deals=False, module_time=False)

    require_modules(settings, "contracts")
    with pytest.raises(ModuleOff) as refused:
        require_modules(settings, "contracts", "deals")

    assert refused.value.message == (
        "Il modulo Deal è spento: un amministratore può accenderlo in Impostazioni → Moduli"
    )
    assert refused.value.code == "module_off"
    assert refused.value.details == {"module": "deals"}


def test_the_rows_are_laid_over_settings_and_the_generic_update_cannot_write_them() -> None:
    laid = apply_overrides(
        _settings(), {"space_profile": "solo", "module_deals": "false", "module_purchases": "true"}
    )

    assert laid.space_profile == "solo"
    assert laid.module_deals is False and laid.module_purchases is True
    assert laid.module_time is None
    assert set(SHAPE_KEYS) <= set(OVERRIDABLE_KEYS)
    # None of them is a field of the generic body: sending one is refused outright.
    assert not set(SHAPE_KEYS) & set(SpaceSettingsUpdate.model_fields)
    assert PROFILE_KEY in SHAPE_KEYS
