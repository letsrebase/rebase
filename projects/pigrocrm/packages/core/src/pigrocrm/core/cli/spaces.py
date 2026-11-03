"""The commands that act on the registry's spaces as a whole: `ensure-space-defaults`,
`rebuild-identity-index` and `seed-templates`."""

import sys

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import ProgrammingError

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.cli.spaces_walk import disposed, read_spaces, report_unreadable, space_engine
from pigrocrm.core.config import Settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.identity.models import Identity
from pigrocrm.core.identity.service import IdentityService
from pigrocrm.core.reminders.run import ROOT_LABEL
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.tenants import ensure_defaults, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_url, tenant_owner_database_url
from pigrocrm.core.tenants.service import migrate_to_head


def seed_templates(settings: Settings) -> int:
    """`pigrocrm seed-templates`. Idempotent, so it is safe on every deploy -- which is
    the point: the timesheet template has to exist before anyone presses Scarica, and
    requiring a manual step there is how a feature ships broken."""
    with (
        disposed(create_engine_from_settings(settings)) as engine,
        session_factory(engine)() as session,
    ):
        created = TemplateService(session).seed_defaults(Actor.system())
    for template in created:
        print(f"created: {template.name} ({template.type})")
    if not created:
        print("no template to create: they are already there")
    return 0


def _schema_revision(engine: Engine) -> str | None:
    """The Alembic revision a space's database is at, `None` before its first migration
    (no `alembic_version` table yet). A database that cannot be reached raises: that is
    the caller's line on stderr, not a missing table."""
    try:
        with engine.connect() as connection:
            return connection.execute(text("select version_num from alembic_version")).scalar()
    except ProgrammingError:
        return None


def ensure_space_defaults(settings: Settings) -> int:
    """`pigrocrm ensure-space-defaults`: every space in the registry is brought to the
    image's schema, then gets the stages, templates and categories it lacks, table by
    table, only where the table is empty (spec 2026-09-12 §6.5). Runs in the API
    image's CMD after the root's own `alembic upgrade head`.

    **The migration comes first, and it is the reason this command visits every space
    at all** (ORB-189). A space's database is migrated once, when the space is
    provisioned, and `alembic upgrade head` in the CMD knows only the root database. So
    each registry row is upgraded here with the same `migrate_to_head` provisioning
    uses, one line per space saying from which revision to which, and only then
    furnished. This is the only migrator: the cron commands migrate nothing.

    Answers 0 whatever happens to a space or to the registry, since it runs before
    uvicorn and a problem in one space must never keep the API down for the others: that
    is one line on stderr with the exception's type and never its text. The one failure
    that does answer 1 is the application role on the root, which cannot be created:
    uvicorn would start with a URL that cannot log in. The root installation is not in
    the registry and is not touched."""
    # The application role first, on the root (REB-634, spec 2026-10-03 §4 «The role»):
    # the CMD has just migrated the root as the owner, and every request from here on
    # connects as the role `PIGROCRM_DATABASE_URL` names, which must exist and be
    # granted before uvicorn answers. One URL means no role, and one line that says so.
    try:
        if ensure_application_role(settings.owner_database_url, settings.database_url):
            print(f"{ROOT_LABEL}: application role ready")
        else:
            print(f"{ROOT_LABEL}: a single URL, no application role to create")
    except Exception as exc:  # noqa: BLE001 - reported, then the boot stops
        # The CMD chains on this exit code, so the container restarts and says why.
        print(f"{ROOT_LABEL}: application role not created ({type(exc).__name__})", file=sys.stderr)
        return 1
    walk = read_spaces(settings, include_root=False)
    if report_unreadable(walk):
        return 0
    if not walk.targets:
        print("no space in the registry")
        return 0
    for target in walk.targets:
        slug, db_name = target.label, target.db_name
        assert db_name is not None  # a registry row, not the root
        # As the owner: the migration, the grants and the furnishing are the owner's
        # work, and the furnished rows (`FORCE ROW LEVEL SECURITY` binds the owner too)
        # go in under the system actor's scope.
        url = tenant_owner_database_url(settings, db_name)
        with space_engine(url) as engine:
            try:
                before = _schema_revision(engine)
                migrate_to_head(settings, url.render_as_string(hide_password=False))
                after = _schema_revision(engine)
                ensure_application_role(url, tenant_database_url(settings, db_name))
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: not migrated ({type(exc).__name__})", file=sys.stderr)
                continue
            if before == after:
                print(f"{slug}: schema already at {after}")
            else:
                print(f"{slug}: schema migrated from {before or 'zero'} to {after}")
            try:
                with session_factory(engine)() as space:
                    bind_scope(space, Actor.system())
                    report = ensure_defaults(space, name=slug)
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: not furnished ({type(exc).__name__})", file=sys.stderr)
                continue
        if report.seeded:
            print(
                f"{slug}: statuses {report.stages}, templates {report.templates}, "
                f"categories {report.categories}, legal entities {report.legal_entities}"
            )
        else:
            print(f"{slug}: already in place")
    return 0


def rebuild_identity_index(settings: Settings) -> int:
    """`pigrocrm rebuild-identity-index`: a day-one backfill for an installation that
    wants "my spaces" complete immediately, never a correctness requirement --
    `identities` already grows to completeness on its own as people log back in
    (`IdentityService.upsert_and_issue`, called from `login`, `enter_with_link` and
    `accept_invite`). Reads every `users` row of every space -- not only `owner_email`,
    since a space may already hold people invited after it was created -- and upserts one
    `identities` row per distinct address found, through `IdentityService.get_or_create`,
    which never mints a session or a token: a backfill records that an address exists, it
    does not pretend anyone has just logged in.

    Answers 0 whatever happens, as `ensure_space_defaults` does: a registry that cannot be
    reached, a space that cannot be read or whose identities fail to write is one line on
    stderr with the exception's type, never its text, and never the whole command."""
    try:
        registry = ensure_tenants_database(settings)
    except Exception as exc:  # noqa: BLE001 - never fails the whole command
        print(f"space registry unreachable ({type(exc).__name__})", file=sys.stderr)
        return 0
    try:
        walk = read_spaces(settings, include_root=False)
        if report_unreadable(walk):
            return 0
        if not walk.targets:
            print("no space in the registry")
            return 0
        for target in walk.targets:
            slug = target.label
            try:
                with (
                    space_engine(target.url) as engine,
                    session_factory(engine)() as space,
                ):
                    emails = sorted(
                        {row.strip().lower() for row in space.scalars(select(User.email)).all()}
                        - {""}
                    )
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: unreachable ({type(exc).__name__})", file=sys.stderr)
                continue
            if not emails:
                print(f"{slug}: no users")
                continue
            try:
                with session_factory(registry)() as registry_session:
                    existing = set(
                        registry_session.scalars(
                            select(Identity.email).where(Identity.email.in_(emails))
                        ).all()
                    )
                    service = IdentityService(registry_session, settings)
                    for email in emails:
                        service.get_or_create(email)
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: identities not updated ({type(exc).__name__})", file=sys.stderr)
                continue
            new_count = len(emails) - len(existing)
            if new_count:
                print(f"{slug}: {len(emails)} addresses, {new_count} new identities")
            else:
                print(f"{slug}: {len(emails)} addresses, already indexed")
    finally:
        registry.dispose()
    return 0
