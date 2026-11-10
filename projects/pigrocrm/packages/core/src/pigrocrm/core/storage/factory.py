from pathlib import Path

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.storage.gdrive import GDriveStorage
from pigrocrm.core.storage.lazy_drive import LazyUserDriveStorage, SessionFactory
from pigrocrm.core.storage.local import LocalFileStorage


def _gdrive_not_configured() -> ValidationFailed:
    """Two ways to configure Drive, so the error names both: the service-account
    variables, and the connected Google account whose own credential writes into a
    folder the owner picks. A message naming only the first sends the reader to the
    Google Cloud console for a setup they may have no reason to create, and this error
    is the only place they are looking.

    Neither half names a slice. This sentence is read by an operator whose MCP adapter
    refused to come up, or whose first document operation over the API answered 500 --
    somebody who has no map of this project's slices and no way to act on one, and a
    slice number is a fact about when the code was written, which stops being true the
    moment it ships.

    Built here rather than raised in two branches below: the half-configured service
    account and the missing session factory are the same problem to the reader (Drive
    is selected and finished being configured by neither route), and two copies of this
    paragraph would eventually be two different paragraphs.
    """
    return ValidationFailed(
        "settings",
        "storage_backend",
        "gdrive richiede PIGROCRM_GDRIVE_SERVICE_ACCOUNT_JSON e "
        "PIGROCRM_GDRIVE_ROOT_FOLDER_ID, oppure collega "
        "Drive da Impostazioni e scegli la cartella di scrittura",
        expected="le due variabili del service account, oppure un account "
        "Drive collegato con cartella di scrittura",
    )


def storage_from_settings(
    settings: Settings, *, session_factory: SessionFactory | None = None
) -> DocumentStorage:
    """The single place a backend is chosen. Services take a `DocumentStorage` and
    never call this, which is what makes swapping backends a configuration change
    rather than a code change (spec 11 criterion 5).

    `session_factory` is what the second Drive route needs and the first does not: the
    owner's credential and the folder it writes into live in `google_drive_accounts`,
    so that storage has to be able to open a session later, at each operation. It is
    optional because two of the three outcomes here have no use for one -- and because
    an adapter with no database (there is none today, but the parameter should not
    invent one) can still ask for `local`.

    Which of the two Drive routes is chosen is settled by the environment alone, never
    by which one happens to work: naming *either* service-account variable selects the
    service account and makes the other one required, so a half-finished service-account
    setup fails loudly instead of silently falling through to somebody's personal Drive
    credential.

    **When "loudly" is.** This function runs when an adapter builds its storage, and the
    two adapters do that at different moments. `apps/mcp` calls it in `build_server`, at
    start-up, so a misconfiguration there is a process that does not come up. `apps/api`
    calls it from the `get_storage` dependency, which FastAPI resolves at the first
    request that touches a document -- so there the same misconfiguration is a 500 on
    the first upload, generation or download, and the API itself starts fine. Deliberate,
    and not an oversight to be fixed by building it at import time: an installation whose
    Drive is not connected *yet* must be able to reach the screen that connects it (see
    `LazyUserDriveStorage`), and a process that refuses to boot cannot serve that screen.
    """
    if settings.storage_backend == "gdrive":
        if settings.gdrive_service_account_json or settings.gdrive_root_folder_id:
            if not settings.gdrive_service_account_json or not settings.gdrive_root_folder_id:
                raise _gdrive_not_configured()
            storage = GDriveStorage.from_service_account(
                service_account_json=settings.gdrive_service_account_json,
                root_folder_id=settings.gdrive_root_folder_id,
            )
            # Fails here, while the backend is being built, with a message that says
            # what to fix -- rather than deeper into a customer's upload as a bare 404
            # or a storageQuotaExceeded three requests in (decision 4, task-5 brief).
            # "Here" is start-up on the MCP adapter and the first document operation on
            # the API, for the reason the docstring above gives.
            storage.verify_root_accessible()
            return storage
        if session_factory is None:
            raise _gdrive_not_configured()
        # No verification here, and none is possible: the folder this will write into is
        # a row that may not exist yet, which is the entire reason this storage resolves
        # at its first operation instead of now (see `LazyUserDriveStorage`). The
        # equivalent of `verify_root_accessible` for this route is the `files.get`
        # `GoogleDriveAccountService.set_roots` makes when somebody chooses the folder,
        # not a check made while the backend is being built.
        return LazyUserDriveStorage(session_factory, settings)
    return LocalFileStorage(Path(settings.storage_local_root))


def space_storage_from_settings(
    settings: Settings, slug: str | None, *, session_factory: SessionFactory | None = None
) -> DocumentStorage:
    """The storage of one space, the way the API writes to it and the cron commands must read
    it. On disk a space's files live in their own folder beside the root's
    (`<root>/tenants/<slug>`), never in the root's; on Drive the space resolves its own account
    through `session_factory`, which must open that space's database. `slug` is `None` for the
    root installation, whose storage is `storage_from_settings`'."""
    if slug is not None and settings.storage_backend != "gdrive":
        return LocalFileStorage(Path(settings.storage_local_root) / "tenants" / slug)
    return storage_from_settings(settings, session_factory=session_factory)
