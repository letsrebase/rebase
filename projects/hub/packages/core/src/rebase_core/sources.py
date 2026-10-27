"""The third-party sources a card is written from: stored, hashed, refetched, deleted.

REB-837 (design 2026-10-27, § Sources and § Provenance). A person's GitHub account and
the links they gave are read by `GithubSource` and `SafeFetcher`; this service is what
keeps the result per person in `freelancer_sources`, so a card can say what it was
written from and a change of a source can mark it stale.

* **Off by default.** `ai_sources` holds one switch per kind (`github`, `links`), both
  off, and neither is turned on in production until Lorenzo and Ivan confirm the privacy
  notice covers reading what people linked. A kind that is off is not fetched, and its
  rows are deleted at once when an admin turns it off and again at the next refetch.
* **The hash ignores what moves on its own.** The hash of a GitHub source is its
  `hash_material()` (no stars, last push or language counts); of a page, its text without
  dates and without a copyright or «last updated» line. Only a change of the hashed
  content makes a card stale.
* **A removed link is a deleted row.** `refetch` makes the stored rows exactly the
  sources the person's links name today, so a link removed from the card loses its text
  at the next refetch, and a person soft-deleted or merged away loses all of it at once
  (`delete_all`, called from `FreelancerService.soft_delete` and the merge). The rows also
  go with the freelancer on `ON DELETE CASCADE`.
* **LinkedIn never gets here.** Any link on `linkedin.com` is dropped before a fetch is
  considered, and the fetcher refuses it again if one slips through.
* **No model.** The weekly pass (`refetch_due`) reads pages and calls GitHub; it never
  writes a card and never imports a provider (`rebase_core.llm` is not imported here, and
  a test reads this file to keep it so).

A source that fails with `error` (the network, a 5xx, a timeout) does not replace text
that was read before: the row keeps it and only the time of the attempt moves, so one bad
night does not turn a card stale and then stale again.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rebase_core.ai_config import AI_CONFIG_ENTITY, AI_CONFIG_ENTITY_ID
from rebase_core.audit import AdminActionService, field_changes, utcnow
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.github_source import GithubSource, github_login_from_links
from rebase_core.models import AI_SOURCE_KINDS, AiSource, Freelancer, FreelancerSource
from rebase_core.web_fetch import (
    MAX_LINKS_PER_PERSON,
    SafeFetcher,
    is_blocked_host,
)

REFETCH_EVERY = timedelta(days=7)

# The switch's name for each kind of stored row: `ai_sources.kind` says `links` (plural,
# the admin's word), `freelancer_sources.kind` says `link` (one row, one link).
_ROW_KIND = {"github": "github", "links": "link"}
_OPAQUE_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.IGNORECASE)
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

_MONTHS = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
    r"|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre"
    r"|dicembre|gen|giu|lug|ago|set|ott|dic)"
)
# A full date, never a bare year or a month and year: «2019 to 2023» on a portfolio is
# content, «last updated 27 October 2026» is a clock.
_DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{2,4}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}\.?,?\s+\d{{4}}\b"
    rf"|\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
    re.IGNORECASE,
)
_CLOCK_LINE = re.compile(
    r"©|\(c\)\s*\d{4}|copyright|last updated|updated on|ultimo aggiornamento|aggiornato il",
    re.IGNORECASE,
)


def link_hash_material(text: str) -> str:
    """A page's text as far as it is the page's content: lines that are a clock (a
    copyright line, «last updated») and full dates removed, whitespace and case
    collapsed. What is left changes when the person rewrites the page."""
    lines = []
    for line in text.splitlines():
        if _CLOCK_LINE.search(line):
            continue
        cleaned = " ".join(_DATE.sub(" ", line).split()).casefold()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def candidate_links(links: list[str]) -> list[str]:
    """The links of a person that are read as pages, in order: scheme added when a bare
    host was typed, `http` raised to `https`, fragment dropped, duplicates removed,
    LinkedIn and anything that is not a web address left out, the GitHub account left to
    the GitHub source, and at most `MAX_LINKS_PER_PERSON` kept."""
    kept: list[str] = []
    for link in links:
        url = _normalise(link)
        if url is None or url in kept:
            continue
        host = _host(url)
        if is_blocked_host(host) or host in _GITHUB_HOSTS:
            continue
        kept.append(url)
    return kept[:MAX_LINKS_PER_PERSON]


def _normalise(link: object) -> str | None:
    if not isinstance(link, str):
        return None
    url = link.strip()
    if not url:
        return None
    if "://" not in url:
        if _OPAQUE_SCHEME.match(url):  # mailto:, tel:, javascript:
            return None
        url = f"https://{url}"
    scheme, _, rest = url.partition("://")
    if scheme.lower() not in ("http", "https"):
        return None
    return f"https://{rest.split('#', 1)[0]}"


def _host(url: str) -> str:
    authority = url.partition("://")[2].split("/", 1)[0].split("?", 1)[0]
    return authority.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")


@dataclass(frozen=True)
class SourceItem:
    kind: str  # `github` or `link`
    ref: str  # the login, or the URL
    position: int
    text: str
    content_sha256: str
    status: str
    fetched_at: datetime

    @property
    def key(self) -> str:
        """How a card names this source: `github`, or `link:<n>` by its position."""
        return "github" if self.kind == "github" else f"link:{self.position}"


@dataclass(frozen=True)
class SourceSet:
    items: list[SourceItem]

    def hashes(self) -> dict[str, str]:
        """`github` and `link:<n>` to the hash of what was read. A source that could not
        be read has no hash: it contributed nothing to the card."""
        return {item.key: item.content_sha256 for item in self.items if item.status == "ok"}

    def readable(self) -> list[SourceItem]:
        return [item for item in self.items if item.status == "ok"]


@dataclass(frozen=True)
class _Wanted:
    kind: str
    ref: str
    position: int


@dataclass(frozen=True)
class _Read:
    kind: str
    ref: str
    position: int
    text: str
    content_sha256: str
    status: str


def _sha(material: str) -> str:
    return hashlib.sha256(material.encode()).hexdigest()


def delete_sources(session: Session, freelancer_id: UUID) -> int:
    """Every stored source of a person, at once, and how many rows that was. Not committed
    here: the caller's own delete commits it with the rest, as `CardWriter.delete` does.
    A function beside the service because the callers that need only this (a person's
    soft delete, a merge) have no fetcher to build a service with."""
    result = session.execute(
        delete(FreelancerSource).where(FreelancerSource.freelancer_id == freelancer_id)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


class SourceService:
    def __init__(
        self,
        session: Session,
        fetcher: SafeFetcher,
        github: GithubSource,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.fetcher = fetcher
        self.github = github
        self.now = now

    # ---- the switches ----------------------------------------------------------------

    def enabled_kinds(self) -> set[str]:
        """The kinds an admin has turned on: `github`, `links`."""
        return set(self.session.scalars(select(AiSource.kind).where(AiSource.enabled.is_(True))))

    def set_enabled(self, admin_id: UUID, kind: str, enabled: bool) -> None:
        if kind not in AI_SOURCE_KINDS:
            raise ValidationFailed("ai_source", "kind", "fonte sconosciuta")
        row = self.session.get(AiSource, kind)
        assert row is not None, "migration 0038 seeds both switches"
        before = row.enabled
        if before == enabled:
            return
        row.enabled = enabled
        row.updated_by = admin_id
        row.updated_at = self.now()
        if not enabled:
            # Off must mean off now, not at the next weekly pass: the text of a page
            # nobody may read for the card any more is not kept.
            self.session.execute(
                delete(FreelancerSource).where(FreelancerSource.kind == _ROW_KIND[kind])
            )
        self.session.commit()
        AdminActionService(self.session).record(
            AI_CONFIG_ENTITY,
            AI_CONFIG_ENTITY_ID,
            "ai_switch",
            admin_id,
            {"source": kind, **field_changes({"enabled": before}, {"enabled": enabled})},
        )

    # ---- reading ---------------------------------------------------------------------

    def current(self, freelancer_id: UUID) -> SourceSet:
        """What is stored for the person, as it was last read. No network."""
        return self._set(self._rows(freelancer_id))

    def refetch(self, freelancer_id: UUID) -> SourceSet:
        """Reads the person's enabled sources again and makes the stored rows exactly
        them: new ones added, changed ones updated, and every row whose link was removed
        (or whose kind is now off, or that is LinkedIn) deleted. The network is read
        first and the database written once, so a slow page holds no lock."""
        row = self.session.get(Freelancer, freelancer_id)
        if row is None:
            raise NotFound("freelancer", freelancer_id)
        if row.deleted_at is not None:
            self.delete_all(freelancer_id)
            self.session.commit()
            return SourceSet([])
        reads = [self._read(wanted) for wanted in self._wanted(row.links)]
        fetched_at = self.now()
        existing = {(r.kind, r.ref): r for r in self._rows(freelancer_id)}
        keep = {(read.kind, read.ref) for read in reads}
        for key, stale in existing.items():
            if key not in keep:
                self.session.delete(stale)
        for read in reads:
            previous = existing.get((read.kind, read.ref))
            if read.status == "error" and previous is not None and previous.status == "ok":
                previous.fetched_at = fetched_at
                previous.position = read.position
                continue
            self.session.execute(self._upsert(freelancer_id, read, fetched_at))
        self.session.commit()
        return self.current(freelancer_id)

    def delete_all(self, freelancer_id: UUID) -> int:
        """Every stored source of the person, at once. Not committed here."""
        return delete_sources(self.session, freelancer_id)

    def refetch_due(self, limit: int, older_than: timedelta = REFETCH_EVERY) -> int:
        """The weekly pass: refetches up to `limit` people whose sources are older than
        `older_than`, are missing, or no longer match their links. Never calls a model.
        Answers how many people were refetched."""
        enabled = self.enabled_kinds()
        cutoff = self.now() - older_than
        stored: dict[UUID, list[FreelancerSource]] = {}
        for source in self.session.scalars(select(FreelancerSource)):
            stored.setdefault(source.freelancer_id, []).append(source)
        people = self.session.scalars(
            select(Freelancer)
            .where(Freelancer.deleted_at.is_(None))
            .where(func.jsonb_array_length(Freelancer.links) > 0)
            .order_by(Freelancer.created_at)
        ).all()
        refetched = 0
        for person in people:
            if refetched >= limit:
                break
            rows = stored.get(person.id, [])
            wanted = {(w.kind, w.ref) for w in self._wanted(person.links, enabled)}
            if wanted == {(r.kind, r.ref) for r in rows} and all(
                r.fetched_at >= cutoff for r in rows
            ):
                continue
            self.refetch(person.id)
            refetched += 1
        return refetched

    # ---- helpers ---------------------------------------------------------------------

    def _rows(self, freelancer_id: UUID) -> list[FreelancerSource]:
        return list(
            self.session.scalars(
                select(FreelancerSource)
                .where(FreelancerSource.freelancer_id == freelancer_id)
                .order_by(FreelancerSource.kind, FreelancerSource.position)
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _set(rows: list[FreelancerSource]) -> SourceSet:
        return SourceSet(
            [
                SourceItem(
                    kind=r.kind,
                    ref=r.ref,
                    position=r.position,
                    text=r.text,
                    content_sha256=r.content_sha256,
                    status=r.status,
                    fetched_at=r.fetched_at,
                )
                for r in rows
            ]
        )

    def _wanted(self, links: list[str], enabled: set[str] | None = None) -> list[_Wanted]:
        """The sources a person's links name that are switched on, before any fetch."""
        on = self.enabled_kinds() if enabled is None else enabled
        wanted: list[_Wanted] = []
        if "github" in on:
            normalised = [url for link in links if (url := _normalise(link)) is not None]
            login = github_login_from_links(normalised)
            if login is not None:
                wanted.append(_Wanted("github", login, 1))
        if "links" in on:
            wanted.extend(
                _Wanted("link", url, n) for n, url in enumerate(candidate_links(links), start=1)
            )
        return wanted

    def _read(self, wanted: _Wanted) -> _Read:
        if wanted.kind == "github":
            snapshot = self.github.fetch(wanted.ref)
            if snapshot.status != "ok":
                return _Read("github", wanted.ref, wanted.position, "", "", snapshot.status)
            return _Read(
                "github",
                wanted.ref,
                wanted.position,
                snapshot.normalised_text(),
                _sha(snapshot.hash_material()),
                "ok",
            )
        result = self.fetcher.fetch(wanted.ref)
        if result.status != "ok":
            return _Read("link", wanted.ref, wanted.position, "", "", result.status)
        return _Read(
            "link",
            wanted.ref,
            wanted.position,
            result.text,
            _sha(link_hash_material(result.text)),
            "ok",
        )

    @staticmethod
    def _upsert(freelancer_id: UUID, read: _Read, fetched_at: datetime) -> Insert:
        values = {
            "position": read.position,
            "text": read.text,
            "content_sha256": read.content_sha256,
            "status": read.status,
            "fetched_at": fetched_at,
        }
        return (
            pg_insert(FreelancerSource)
            .values(freelancer_id=freelancer_id, kind=read.kind, ref=read.ref, **values)
            .on_conflict_do_update(constraint="uq_freelancer_sources_ref", set_=values)
        )
