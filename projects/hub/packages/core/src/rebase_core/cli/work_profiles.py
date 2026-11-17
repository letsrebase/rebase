"""The command that applies the work profiles' retention (REB-1124, spec 2026-11-17 § 8)."""

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import Settings, get_settings


def work_profile_sweep(settings: Settings) -> str:
    """Deletes the profiles past their 12 months and the device authorizations a day after
    they expired or were spent. Its line says how many of each."""
    from rebase_core.work_profiles.sweep import sweep

    session = open_session(settings)()
    try:
        counts = sweep(session)
    finally:
        session.close()
    return (
        f"{counts.profiles} work profiles past their 12 months deleted, "
        f"{counts.authorizations} CLI authorizations deleted"
    )


def work_profile_sweep_command() -> int:
    """`rebase work-profile-sweep`: runs in the `sweep` service's loop of `docker-compose.yml`
    after `rebase crm-sweep` (and from the NixOS module's timer), on production and the preview
    alike."""
    print(work_profile_sweep(get_settings()))
    return 0


COMMANDS = {
    "work-profile-sweep": Command(no_arguments, lambda args: work_profile_sweep_command()),
}
