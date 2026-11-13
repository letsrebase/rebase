"""The one door to PigroCRM's API: the call, what its answer means, the sweep and the write
(REB-892, HCORE-08).

Four clients reach the CRM: the registry of spaces (`rebase_core.pigro`, a `GET`), the
engagements door (`rebase_core.engagements`, a `PUT` and a `GET`), the leads door
(`rebase_core.crm_link`, a `PUT`) and the reads of a freelancer (`FreelancerDoor` below, `GET`s
by the hub's own freelancer id). They differ in a path, a token and the row they write,
and in nothing else, so this module holds what they share and takes those as
parameters.

**The `PUT`** (`PigroDoor.put`) never raises: whatever happens, a refused connection, a
timeout, a status, a body that is too long or not the shape, is an `Outcome` to write on the
row. The status maps to the state the row is left in (`200`/`201` is `linked`; a status
the caller names as refused is `rejected`, which only a person asks again; a `503` and any
other is `error`, which the sweep retries) and to the cause the row stores (`CAUSE_*`, or
the CRM's own sentence when its answer carries one). The bearer travels only over HTTPS
(`speaks_https`), plain HTTP only to this machine.

**The `GET`** (`PigroDoor.get`) raises `PigroUnavailable` with the sentence the admin area
shows, or `Gone` for a status the caller names (the report's `409`). A read that may be asked
again (`attempts`) is asked again on a connection that failed and on a `502`, `503` or `504`:
every `GET` of the door is idempotent.

**The freelancer reads** (`FreelancerDoor`, REB-1107) are what the hub keeps asking the CRM
now that the relationship with a freelancer lives there: availability, the member's contracts
and their signed PDF, the fiscal identifiers the anonymous CV redacts, and the merge check.
They answer the way every `GET` does, and a caller decides what an unreachable CRM means for
it: the anonymous CV is not written, the member's page shows an error, a merge is refused.

**The lock discipline** is the house rule of REB-868 and REB-498: *no database lock is held
across a network call*. A caller locks the row to read what is sent and to stamp the attempt,
commits (`committed`), calls the CRM with nothing held, then locks the row again, in the same
order, to write what the CRM answered (`write`) over the row as it is by then. `sweep_round`
reads its rows with no lock, lets go of the transaction, and gives every row to the caller's
own `link` in a transaction of its own.
"""

import json
import logging
import urllib.error
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.door_schemas import (
    CrmAvailability,
    CrmContracts,
    CrmFiscalIdentifiers,
    CrmFreelancerOrganization,
    CrmMergeCheck,
)
from rebase_core.http import (
    MAX_BODY_BYTES,
    MAX_DOWNLOAD_BYTES,
    HttpCall,
    urllib_call,
    urllib_download_call,
)
from rebase_core.match_words import ERROR, HTTPS_ONLY, LINKED, REJECTED

_log = logging.getLogger(__name__)

# What an admin reads when the CRM does not answer as it should, shared by every client of
# the door so they all say the same words. `ANSWERED_STATUS` is a format, filled with the
# status the CRM answered.
NOT_ANSWERING = "Pigro non risponde."
ANSWERED_STATUS = "Pigro non ha risposto ({status})."
TOO_LONG = "Pigro ha risposto qualcosa di troppo lungo."
NOT_THE_SHAPE = "Pigro ha risposto qualcosa che non è un elenco."

# What a failed or refused call stores in `pigro_error` / `crm_error`: the cause alone,
# never a sentence of its own, since the card wraps it (spec § 3.5): «Pigro non ha
# risposto: HTTP 503.», «Pigro ha rifiutato il collegamento: <the CRM's sentence>». A seam
# sentence there would say «Pigro» twice. The CRM's own sentence is the cause whenever its
# answer carries one, on a 409, a 422 or a 503.
CAUSE_STATUS = "HTTP {status}"
CAUSE_TIMEOUT = "timeout"
CAUSE_REFUSED = "connessione rifiutata"
CAUSE_UNREACHABLE = "nessuna connessione"
CAUSE_TOO_LONG = "risposta troppo lunga"
CAUSE_NOT_THE_SHAPE = "risposta non leggibile"
# A field of the body the CRM's door refused, named, never in FastAPI's English.
CAUSE_FIELD = "{field}: non valido"
# A refusal's sentence as stored on a row: the CRM's own words, bounded, since a body of a
# megabyte is still a body the seam lets through.
ERROR_MAX_LENGTH = 500
# Where plain HTTP is still acceptable: a CRM running on this machine, in development.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_PUT_OK = (200, 201)
_NOT_NOW = 503
# The statuses of a CRM that is there but not ready (a restart, its space's database busy): a
# read asked again once rather than reported at once.
_TRANSIENT = (502, 503, 504)
# What the reads of a freelancer say when this environment has no token for the door.
NOT_CONFIGURED = "Pigro non è configurato su questo ambiente."
FREELANCERS_PATH = "/api/rebase/freelancers"
READ_ATTEMPTS = 2


class PigroUnavailable(Exception):
    """The CRM did not answer as it should: no answer, a status other than the one asked
    for, or a body that is not the shape. The message is the sentence the admin area
    shows."""


class PigroMisconfigured(PigroUnavailable):
    """This environment cannot reach the door at all: no token, or a CRM that is not on HTTPS.
    Not a CRM that failed to answer, so an API says 503 for it where it says 502 for the rest."""


class Gone(Exception):
    """A `GET` answered with a status the caller named as meaning «the thing is gone» (the
    report's `409`: the deal was deleted in the space), with the cause a row stores."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


@dataclass(frozen=True)
class Outcome[Answer: BaseModel]:
    """What one `PUT` came to: the state to write, the sentence for anything but a link,
    and the CRM's answer for a link. `gone` marks a status the caller named as the thing
    having been deleted on the CRM's side, which its `link` writes even over `linked`."""

    status: str
    error: str | None = None
    answer: Answer | None = None
    gone: bool = False


class SweepCount(NamedTuple):
    """A sweep round's count, for its line: `linked` is how many rows it took to
    `linked`, `failed` how many it tried and left otherwise (an `error`, a `rejected`,
    or a call that raised)."""

    linked: int
    failed: int


def speaks_https(url: str) -> bool:
    """Whether the bearer may travel to `url`: HTTPS, or plain HTTP to this machine."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def too_long(raw: bytes, limit: int = MAX_BODY_BYTES) -> bool:
    """Whether an answer passes the seam's cap (the seam reads one byte beyond it)."""
    return len(raw) > limit


def cause_of(raw: bytes, status: int) -> str:
    """What a call the CRM refused, or answered «not now», stores: the CRM's own sentence,
    or the bare status (`CAUSE_STATUS`)."""
    return crm_sentence(raw) or CAUSE_STATUS.format(status=status)


def failure_cause(exc: Exception) -> str:
    """The cause of a call that never got an answer: `urllib` wraps the socket's own
    error in `URLError`, a read that stalls raises `TimeoutError` bare."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return CAUSE_TIMEOUT
    if isinstance(reason, ConnectionRefusedError):
        return CAUSE_REFUSED
    return CAUSE_UNREACHABLE


def crm_sentence(raw: bytes) -> str | None:
    """The CRM's own sentence in an answer, `None` when it carries none. A `Conflict`'s
    problem document (`code` `conflict`) by its `reason`: its `detail` is `entity:
    reason`, «engagement: Il deal di questa lettera è stato eliminato nello spazio.»,
    and an admin reads the sentence, not the entity. Any other problem document by its
    `detail`, which for a `ValidationFailed` is `entity.field: reason` and keeps the
    field's name. FastAPI's own list of errors, whose words are English and repeat the
    value, as `field: non valido` for each field it names."""
    if not raw or too_long(raw):
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    reason = body.get("reason")
    if body.get("code") == "conflict" and isinstance(reason, str) and reason.strip():
        return reason.strip()[:ERROR_MAX_LENGTH]
    detail = body.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:ERROR_MAX_LENGTH]
    if isinstance(detail, list):
        fields = dict.fromkeys(
            ".".join(str(part) for part in error["loc"] if part != "body")
            for error in detail
            if isinstance(error, dict) and isinstance(error.get("loc"), list)
        )
        named = [CAUSE_FIELD.format(field=field) for field in fields if field]
        if named:
            return "; ".join(named)[:ERROR_MAX_LENGTH]
    return None


@dataclass(frozen=True)
class PigroDoor:
    """The CRM at `api_url`, reached with `token` through `http`: the seam is
    `urllib_engagements_call` for the doors that may provision a space and `urllib_call`
    for the registry; a test hands a fake."""

    api_url: str
    token: str
    http: HttpCall

    def url(self, path: str) -> str:
        return f"{self.api_url.rstrip('/')}{path}"

    def headers(self, *, with_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def put[A: BaseModel](
        self,
        path: str,
        body: Mapping[str, object],
        answer: type[A],
        *,
        subject: str,
        refused: Collection[int] = (422,),
        gone: Collection[int] = (),
    ) -> Outcome[A]:
        """The one `PUT`, with no row lock held and only over HTTPS. Never raises:
        whatever happens is an outcome to write. `answer` is the model a `200`/`201` must
        parse as; `refused` the statuses that are a `rejected` (the CRM's refusal of this
        very request, which only a person asks again); `gone` those among them that mean
        the thing was deleted on the CRM's side (checked first, and marked on the
        outcome). `subject` names the call in the log («the link of match ...»), never the
        token or an address."""
        if not speaks_https(self.api_url):
            return Outcome(ERROR, HTTPS_ONLY)
        try:
            status, raw = self.http(
                "PUT", self.url(path), self.headers(with_body=True), _json(body)
            )
        except Exception as exc:  # noqa: BLE001 - never raises: any failure is an outcome
            _log.warning("the CRM did not answer %s (%s)", subject, type(exc).__name__)
            return Outcome(ERROR, failure_cause(exc))
        if status in _PUT_OK:
            if too_long(raw):
                return Outcome(ERROR, CAUSE_TOO_LONG)
            try:
                return Outcome(LINKED, answer=answer.model_validate_json(raw))
            except ValueError:  # pydantic's `ValidationError` is one
                return Outcome(ERROR, CAUSE_NOT_THE_SHAPE)
        if status in gone:
            return Outcome(REJECTED, cause_of(raw, status), gone=True)
        if status in refused:
            return Outcome(REJECTED, cause_of(raw, status))
        if status == _NOT_NOW:
            # The door's «not now» (its lock busy, the space unreachable), in its words.
            return Outcome(ERROR, cause_of(raw, status))
        return Outcome(ERROR, CAUSE_STATUS.format(status=status))

    def get[T](
        self,
        path: str,
        parse: Callable[[bytes], T],
        *,
        gone: Collection[int] = (),
        limit: int = MAX_BODY_BYTES,
        attempts: int = 1,
    ) -> T:
        """One `GET`, parsed by `parse`, which raises a `ValueError` (Pydantic's
        `ValidationError` is one) for a body that is not the shape. `PigroUnavailable`
        with the admin's sentence for no answer, a status other than `200`, a body past
        `limit` (the seam's own cap; a file's is the download seam's) or one that is not the
        shape; `Gone` for a status in `gone`. With `attempts` above one, a connection that
        failed and a `502`, `503` or `504` are asked again at once, up to that many calls."""
        for attempt in range(1, attempts + 1):
            try:
                status, raw = self.http("GET", self.url(path), self.headers(), b"")
            except Exception as exc:  # noqa: BLE001 - a refused connection, a DNS miss, a timeout
                if attempt < attempts:
                    continue
                raise PigroUnavailable(NOT_ANSWERING) from exc
            if status in _TRANSIENT and attempt < attempts:
                continue
            break
        if status in gone:
            raise Gone(cause_of(raw, status))
        if status != 200:
            raise PigroUnavailable(ANSWERED_STATUS.format(status=status))
        if too_long(raw, limit):
            raise PigroUnavailable(TOO_LONG)
        try:
            return parse(raw)
        except ValueError as exc:
            raise PigroUnavailable(NOT_THE_SHAPE) from exc


@dataclass(frozen=True)
class FreelancerDoor:
    """The CRM's reads of one freelancer, by the hub's own freelancer id (REB-1107, collaborators
    design § 5): `GET`s under `{api_url}/api/rebase/freelancers/{id}`, behind the engagements
    token, each asked twice at most (`READ_ATTEMPTS`). `http` is the JSON seam and `download`
    the one with room for a signed PDF (`urllib_download_call`); a test hands the same fake.

    Each read raises `PigroUnavailable` with the sentence an admin or a member is shown: the
    environment has no token (`NOT_CONFIGURED`), the CRM is not on HTTPS (`HTTPS_ONLY`, the
    bearer never travels in clear), or the call failed as `PigroDoor.get` says. What an
    unreachable CRM means is the caller's decision, not this class's."""

    api_url: str
    token: str
    http: HttpCall
    download: HttpCall

    def _door(self, http: HttpCall) -> PigroDoor:
        if not self.token:
            raise PigroMisconfigured(NOT_CONFIGURED)
        if not speaks_https(self.api_url):
            raise PigroMisconfigured(HTTPS_ONLY)
        return PigroDoor(self.api_url, self.token, http)

    def _path(self, freelancer_id: UUID, tail: str, query: Mapping[str, str] | None = None) -> str:
        path = f"{FREELANCERS_PATH}/{freelancer_id}/{tail}"
        return f"{path}?{urlencode(query)}" if query else path

    def availability(
        self,
        freelancer_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> CrmAvailability:
        """The weeks a freelancer is committed, from the CRM's live engagements: `unknown` for
        a week an engagement with no `days_per_week` touches, never `free`. A card the CRM
        holds nothing about is free in every week."""
        query = {
            key: value.isoformat()
            for key, value in (("from", date_from), ("to", date_to))
            if value is not None
        }
        return self._door(self.http).get(
            self._path(freelancer_id, "availability", query),
            CrmAvailability.model_validate_json,
            attempts=READ_ATTEMPTS,
        )

    def contracts(self, freelancer_id: UUID) -> CrmContracts:
        """The documents that reached the freelancer, newest first; none for a card the CRM
        holds nothing about."""
        return self._door(self.http).get(
            self._path(freelancer_id, "contracts"),
            CrmContracts.model_validate_json,
            attempts=READ_ATTEMPTS,
        )

    def signed_pdf(self, freelancer_id: UUID, document_id: UUID) -> bytes:
        """The signed copy of one of the freelancer's own documents. `Gone` for a document that
        is not theirs, has no signed copy or does not exist: the CRM answers all three alike."""
        return self._door(self.download).get(
            self._path(freelancer_id, f"contracts/{document_id}/pdf"),
            bytes,
            gone=(404,),
            limit=MAX_DOWNLOAD_BYTES,
            attempts=READ_ATTEMPTS,
        )

    def fiscal_identifiers(self, freelancer_id: UUID) -> CrmFiscalIdentifiers:
        """Tax code, VAT number and certified email of the freelancer's CRM organization, each
        `None` when the CRM holds none, for the anonymous CV's redaction list."""
        return self._door(self.http).get(
            self._path(freelancer_id, "fiscal-identifiers"),
            CrmFiscalIdentifiers.model_validate_json,
            attempts=READ_ATTEMPTS,
        )

    def merge_check(self, freelancer_id: UUID, other_id: UUID) -> CrmMergeCheck:
        """What the CRM holds that stops these two cards becoming one."""
        return self._door(self.http).get(
            self._path(freelancer_id, "merge-check", {"with": str(other_id)}),
            CrmMergeCheck.model_validate_json,
            attempts=READ_ATTEMPTS,
        )

    def ensure_organization(
        self, freelancer_id: UUID, card: Mapping[str, object]
    ) -> Outcome[CrmFreelancerOrganization]:
        """«Crea match» (REB-1105): the CRM finds or writes the freelancer's organization from
        the hub's card, by the hub id and never by name (a `PUT`, so asking twice is asking
        once). Never raises: a refusal (`409` for an archived organization or a VAT number that
        is another freelancer's, `422` for a card it cannot take) is a `rejected` outcome with the
        CRM's sentence, anything else an `error` one. `card` is the door's body: `legal_name`,
        `email`, `contact_points`, `vat_number`, `tax_code`."""
        if not self.token:
            return Outcome(ERROR, NOT_CONFIGURED)
        return PigroDoor(self.api_url, self.token, self.http).put(
            self._path(freelancer_id, "organization"),
            card,
            CrmFreelancerOrganization,
            subject=f"the organization of freelancer {freelancer_id}",
            refused=(409, 422),
        )


def freelancer_door(settings: Settings, http: HttpCall = urllib_call) -> FreelancerDoor:
    """The reads of a freelancer as this environment configures them: the CRM at
    `REBASE_PIGRO_API_URL` under `REBASE_PIGRO_ENGAGEMENTS_TOKEN`, through `http`. A signed PDF
    needs more room than the JSON seam gives, so production's `urllib_call` is paired with
    `urllib_download_call` for it, and a test's fake serves both."""
    download = urllib_download_call if http is urllib_call else http
    return FreelancerDoor(settings.pigro_api_url, settings.pigro_engagements_token, http, download)


def _json(body: Mapping[str, object]) -> bytes:
    return json.dumps(body).encode()


def write[Row, A: BaseModel](
    row: Row,
    outcome: Outcome[A],
    *,
    state: str,
    error: str,
    copy: Callable[[Row, A], None],
) -> None:
    """The outcome on the row: `state` and `error` are the names of its two columns (`pigro_status`
    and `pigro_error`, `crm_status` and `crm_error`); a link's answer is copied by `copy`,
    which sets the row's own columns for it and leaves the error cleared. An outcome with no
    answer leaves what the row already holds of the last link and sets only the cause."""
    setattr(row, state, outcome.status)
    if outcome.answer is None:
        setattr(row, error, outcome.error)
        return
    copy(row, outcome.answer)
    setattr(row, error, None)


@contextmanager
def committed(session: Session) -> Iterator[None]:
    """One short transaction of a link: committed when the block ends, rolled back and
    re-raised when it fails, so no lock outlives the step that took it."""
    try:
        yield
        session.commit()
    except Exception:
        session.rollback()
        raise


def sweep_round(
    session: Session,
    ids: Sequence[UUID],
    link: Callable[[UUID], bool | None],
    *,
    what: str,
) -> SweepCount:
    """The recovery round: `link` on every id, each on its own, a failure logged under
    `what` («match», «request») without stopping the rest. `ids` were read with no lock;
    the transaction that read them is let go before the first call, so nothing is held
    while the CRM answers. `link` says `True` for a row it took to `linked`, `False`
    for one it left otherwise and `None` for one that needed nothing (counted in neither)."""
    session.rollback()
    linked = failed = 0
    for row_id in ids:
        try:
            done = link(row_id)
        except Exception:
            session.rollback()
            _log.warning("%s %s could not be linked to the CRM", what, row_id, exc_info=True)
            failed += 1
            continue
        if done is None:
            continue
        if done:
            linked += 1
        else:
            failed += 1
    return SweepCount(linked=linked, failed=failed)
