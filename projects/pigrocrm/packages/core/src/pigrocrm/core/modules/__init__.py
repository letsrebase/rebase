"""Profiles and switchable modules (spec 2026-11-09, Part B): the registry the surfaces
ask, and the service behind `set_space_profile`, `set_space_modules` and the read that
describes them."""

from pigrocrm.core.modules.registry import (
    MODULE_KEYS,
    MODULE_SETTING_KEYS,
    MODULES,
    MODULES_BY_KEY,
    ONBOARDING_SKIPPED_KEY,
    PROFILE_KEY,
    PROFILES,
    SHAPE_KEYS,
    Module,
    ModuleKey,
    OnboardingState,
    Profile,
    enabled_modules,
    missing_modules,
    module_enabled,
    onboarding_state,
    profile_default,
    require_in,
    require_modules,
    setting_key,
)

__all__ = [
    "MODULES",
    "MODULES_BY_KEY",
    "MODULE_KEYS",
    "MODULE_SETTING_KEYS",
    "ONBOARDING_SKIPPED_KEY",
    "PROFILES",
    "PROFILE_KEY",
    "SHAPE_KEYS",
    "Module",
    "ModuleKey",
    "OnboardingState",
    "Profile",
    "enabled_modules",
    "missing_modules",
    "module_enabled",
    "onboarding_state",
    "profile_default",
    "require_in",
    "require_modules",
    "setting_key",
]
