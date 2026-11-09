from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.modules.registry import ModuleKey, Profile


class ModuleRead(BaseModel):
    key: ModuleKey
    label: str = Field(description="The module's name as a person reads it (Italian)")
    description: str = Field(description="What the module holds, in one line (Italian)")
    requires: list[ModuleKey] = Field(
        description="Modules that must be on for this one to be on: `time` needs `deals`"
    )
    enabled: bool = Field(description="Whether the space has the module on right now")
    stored: bool | None = Field(
        description="The switch the space wrote; `null` while it has none and the profile decides"
    )
    default: bool = Field(description="What the space's profile switches it to, absent a switch")
    holds_data: bool = Field(description="Whether the space has records in it: off never deletes")
    records: int = Field(description="How many live records the module holds")


class SpaceModulesRead(BaseModel):
    profile: Profile | None = Field(description="`solo`, `company`, or `null` until chosen")
    modules: list[ModuleRead]


class ModuleChange(BaseModel):
    module: ModuleKey
    label: str
    was: bool = Field(description="On or off in the space now")
    becomes: bool = Field(description="On or off once the profile is applied")
    records: int = Field(description="Records the module holds: they stay when it goes off")


class SpaceProfileSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Profile
    confirm: bool = Field(
        default=False,
        description=(
            "`false` (the default) answers the modules the profile would switch on and off and "
            "changes nothing; `true` applies exactly that list, with `modules` edits laid on top"
        ),
    )
    modules: dict[ModuleKey, bool] = Field(
        default_factory=dict,
        description=(
            "Edits to the profile's defaults: a module the admin keeps on (or off) whatever "
            "the profile says. Every module not named takes the profile's default"
        ),
    )


class SpaceProfileProposal(BaseModel):
    profile: Profile
    previous_profile: Profile | None
    applied: bool = Field(description="`true` once the profile and its modules are written")
    changes: list[ModuleChange] = Field(description="Only the modules whose state changes")
    state: SpaceModulesRead = Field(
        description="The space as it is after this call: unchanged when `applied` is false"
    )


class SpaceModulesSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: list[ModuleKey] = Field(default_factory=list, description="Modules to switch on")
    off: list[ModuleKey] = Field(default_factory=list, description="Modules to switch off")
    confirm_off: list[ModuleKey] = Field(
        default_factory=list,
        description=(
            "The modules in `off` that hold records and that the caller agrees to switch off: "
            "one that holds data and is not named here is refused. Their data stays"
        ),
    )
