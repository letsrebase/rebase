"""`set_space_profile`, `set_space_modules` and the read that describes both (spec
2026-11-09, B1 and B2).

The profile and the switches are `space_settings` rows (`registry.SHAPE_KEYS`) that only
this service writes, through `SpaceSettingsService.write_shape`. Both writes are an
unscoped admin's act and, for an agent, a full-access token's (`agent_actions.py`): they
change what the whole team sees. Neither touches a record: off hides, it never deletes.
"""

from collections.abc import Collection

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.modules.registry import (
    MODULE_KEYS,
    MODULES,
    MODULES_BY_KEY,
    ONBOARDING_SKIPPED_KEY,
    PROFILE_KEY,
    ModuleKey,
    Profile,
    enabled_modules,
    onboarding_state,
    profile_default,
    setting_key,
    stored_switch,
)
from pigrocrm.core.modules.schemas import (
    ModuleChange,
    ModuleRead,
    SpaceModulesRead,
    SpaceModulesSet,
    SpaceProfileProposal,
    SpaceProfileSet,
)
from pigrocrm.core.space_settings.service import ENTITY, SETTINGS_ID, SpaceSettingsService
from pigrocrm.core.timetracking.models import Cost, TimeEntry

SET_PROFILE = "set_space_profile"
SET_MODULES = "set_space_modules"
SKIP_ONBOARDING = "skip_space_onboarding"

# What each module holds that a person would miss: the live rows of its own tables. `taxes`
# keeps nothing of its own (the estimate is computed from invoices, and the fiscal profile
# stays in the core), so it never asks for a confirmation.
_RECORDS: dict[str, type[Deal] | type[Contract] | type[TimeEntry] | type[Cost]] = {
    "deals": Deal,
    "contracts": Contract,
    "time": TimeEntry,
    "costs": Cost,
}


def _bit(value: bool) -> str:
    return "true" if value else "false"


class SpaceModulesService:
    """On a session of the space's database; `base` is what `SpaceSettingsService` takes."""

    def __init__(self, session: Session, base: Settings) -> None:
        self.session = session
        self.settings = SpaceSettingsService(session, base)
        self.activities = ActivityService(session)

    def _counts(self) -> dict[str, int]:
        """The live rows of every module that keeps any, in one statement: this answers the
        shell's read of the modules, which every page asks, so it is one round trip and
        not one per table."""
        columns = [
            select(func.count())
            .select_from(model)
            .where(model.deleted_at.is_(None))
            .scalar_subquery()
            .label(key)
            for key, model in _RECORDS.items()
        ]
        row = self.session.execute(select(*columns)).one()
        return {key: int(row._mapping[key] or 0) for key in _RECORDS}

    def _write(self, values: dict[str, str | None], actor: Actor) -> None:
        """The rows, and on the settings' timeline which keys moved and who moved them (keys
        only), in one transaction."""
        changed = self.settings.write_shape(values)
        if changed:
            self.activities.record(ENTITY, SETTINGS_ID, "updated", actor, {"keys": changed})
        self.session.commit()

    def describe(self) -> SpaceModulesRead:
        """Open to every member and every agent: it says what the space can do, and the
        tools that are listed already say as much."""
        effective = self.settings.effective()
        on = enabled_modules(effective)
        counts = self._counts()
        modules = []
        for module in MODULES:
            records = counts.get(module.key, 0)
            modules.append(
                ModuleRead(
                    key=module.key,
                    label=module.label,
                    description=module.description,
                    requires=list(module.requires),
                    enabled=module.key in on,
                    stored=stored_switch(effective, module.key),
                    default=profile_default(effective.space_profile, module.key),
                    holds_data=records > 0,
                    records=records,
                )
            )
        return SpaceModulesRead(
            profile=effective.space_profile,
            onboarding=onboarding_state(effective.space_profile, effective.onboarding_skipped),
            modules=modules,
        )

    def set_profile(self, data: SpaceProfileSet, actor: Actor) -> SpaceProfileProposal:
        """Without `confirm`: the modules the profile would switch, nothing written. With
        it: the profile and a row for every built module, so that the stored set is what
        the space sees and the next change is a visible diff (B2)."""
        actor.require_unscoped_admin(SET_PROFILE)
        if data.confirm:
            self.settings.lock()
        effective = self.settings.effective()
        now_on = enabled_modules(effective)
        counts = self._counts()
        proposed = {
            key: data.modules.get(key, profile_default(data.profile, key)) for key in MODULE_KEYS
        }
        _check_dependencies(proposed)
        changes = [
            ModuleChange(
                module=module.key,
                label=module.label,
                was=module.key in now_on,
                becomes=proposed[module.key],
                records=counts.get(module.key, 0),
            )
            for module in MODULES
            if (module.key in now_on) != proposed[module.key]
        ]
        if data.confirm:
            # Choosing a profile ends the onboarding for good, whether or not it was skipped.
            values: dict[str, str | None] = {
                PROFILE_KEY: data.profile,
                ONBOARDING_SKIPPED_KEY: None,
            }
            values.update({setting_key(key): _bit(value) for key, value in proposed.items()})
            self._write(values, actor)
        return SpaceProfileProposal(
            profile=data.profile,
            previous_profile=effective.space_profile,
            applied=data.confirm,
            changes=changes,
            state=self.describe(),
        )

    def ensure_profile(
        self, profile: Profile, actor: Actor, *, keep_on: Collection[str] = ()
    ) -> list[str]:
        """Gives a space that has no profile `profile` and a row for every module that has
        none (B2, B7): the profile's default, and on for `keep_on` and for every module that holds
        records, since a module with data is never switched off unconfirmed. For the places that
        decide a profile on the space's behalf, the boot (`ensure-space-defaults`) and the
        engagements door, so there is no admin check and no confirmation. It never
        overwrites: a space that already stores a profile is left whole, and a module row
        that exists keeps its value. Answers the keys written, empty when it did nothing,
        which is what makes a second call a no-op."""
        self.settings.lock()
        if self.settings.effective().space_profile is not None:
            return []
        stored = self.settings.effective()
        values: dict[str, str | None] = {PROFILE_KEY: profile}
        counts = self._counts()
        wanted = {
            key: key in keep_on or profile_default(profile, key) or counts.get(key, 0) > 0
            for key in MODULE_KEYS
            if stored_switch(stored, key) is None
        }
        # A module with records never goes off without an admin's confirmation (B1) and
        # there is no admin here, so what holds data stays on, and what it needs with it.
        for module in reversed(MODULES):
            if wanted.get(module.key):
                for need in module.requires:
                    if need in wanted:
                        wanted[need] = True
        values.update({setting_key(key): _bit(on) for key, on in wanted.items()})
        self._write(values, actor)
        return sorted(values)

    def skip_onboarding(self, actor: Actor) -> SpaceModulesRead:
        """«Decido dopo» on the first step (B3): nothing about the modules changes, every one
        stays on, and the onboarding moves to «Primi passi» instead of covering the screen
        at the next login. A space that already has a profile has nothing to skip."""
        actor.require_unscoped_admin(SKIP_ONBOARDING)
        self.settings.lock()
        if self.settings.effective().space_profile is None:
            self._write({ONBOARDING_SKIPPED_KEY: _bit(True)}, actor)
        return self.describe()

    def set_modules(self, data: SpaceModulesSet, actor: Actor) -> SpaceModulesRead:
        """Switches the named modules. A module that holds records is switched off only when
        the call names it in `confirm_off`; the records stay either way."""
        actor.require_unscoped_admin(SET_MODULES)
        self.settings.lock()
        both = set(data.on) & set(data.off)
        if both:
            raise ValidationFailed(
                "space_modules",
                "on",
                f"{sorted(both)} sono sia in `on` sia in `off`",
            )
        stray = set(data.confirm_off) - set(data.off)
        if stray:
            raise ValidationFailed(
                "space_modules",
                "confirm_off",
                f"{sorted(stray)} non sono tra i moduli da spegnere (`off`)",
            )
        now_on = enabled_modules(self.settings.effective())
        target = {key: key in now_on for key in MODULE_KEYS}
        target.update({key: True for key in data.on})
        target.update({key: False for key in data.off})
        # Switching a module off while modules that need it stay on is the confirmation of
        # spec A2 and B1, not a malformed request: the answer lists those modules, and the
        # call that names them in `off` too is the one that goes through. A module the call
        # switches on over a requirement that is off stays the plain refusal below.
        dependants = [
            module.key
            for module in MODULES
            if target[module.key]
            and module.key not in data.on
            and any(need in data.off for need in module.requires)
        ]
        if dependants:
            raise Conflict(
                "space_modules",
                "altri moduli accesi dipendono da quelli da spegnere: spegnili insieme, "
                "nominandoli anche in `off`: "
                + ", ".join(MODULES_BY_KEY[key].label for key in dependants),
                modules=dependants,
            )
        _check_dependencies(target)
        counts = self._counts()
        holding = {
            key: counts[key]
            for key in data.off
            if key in now_on and key not in data.confirm_off and counts.get(key, 0)
        }
        if holding:
            raise Conflict(
                "space_modules",
                "il modulo contiene dati: i record restano e tornano accendendolo, ma va "
                "confermato per nome in `confirm_off`: "
                + ", ".join(
                    f"{MODULES_BY_KEY[key].label} ({records} record)"
                    for key, records in holding.items()
                ),
                modules=sorted(holding),
            )
        values: dict[str, str | None] = {}
        values.update({setting_key(key): _bit(True) for key in data.on})
        values.update({setting_key(key): _bit(False) for key in data.off})
        self._write(values, actor)
        return self.describe()


def _check_dependencies(state: dict[ModuleKey, bool]) -> None:
    """A module cannot be on while one it needs is off."""
    for module in MODULES:
        if not state[module.key]:
            continue
        for need in module.requires:
            if not state[need]:
                raise ValidationFailed(
                    "space_modules",
                    module.key,
                    f"il modulo {module.label} richiede {MODULES_BY_KEY[need].label}: "
                    f"spegni anche {module.label} o tieni acceso {MODULES_BY_KEY[need].label}",
                )
