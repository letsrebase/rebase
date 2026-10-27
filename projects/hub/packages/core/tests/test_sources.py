"""The sources a card is written from (REB-837): switches off by default, text stored with
a hash that ignores what moves on its own, a removed link deleted at the next refetch,
LinkedIn never stored, everything gone with the person. The fetcher and GitHub are fakes
that count their calls: nothing here reads the network."""

import ast
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fakes_cards import text_pdf
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import rebase_core.sources as sources_module
from rebase_core.ai_config import AI_CONFIG_ENTITY_ID
from rebase_core.audit import AdminActionService
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.github_source import GithubSnapshot, GithubSource, Repo
from rebase_core.models import AiSource, Freelancer, FreelancerSource, User
from rebase_core.schemas import FreelancerCreate
from rebase_core.sources import (
    SourceService,
    candidate_links,
    link_hash_material,
    retry_delay,
)
from rebase_core.web_fetch import MAX_LINKS_PER_PERSON, FetchResult, SafeFetcher

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
PAGE = "Ada\n# Backend engineer\nBuilds payment systems."


class FakeFetcher(SafeFetcher):
    def __init__(self, pages: dict[str, FetchResult] | None = None) -> None:
        super().__init__()
        self.pages = dict(pages or {})
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self.pages.get(url, FetchResult("ok", PAGE, url))


class FakeGithub(GithubSource):
    def __init__(self, snapshot: GithubSnapshot | None = None) -> None:
        super().__init__(lambda *args: (500, {}, b""))
        self.snapshot = snapshot
        self.calls: list[str] = []

    def fetch(self, login: str) -> GithubSnapshot:
        self.calls.append(login)
        return self.snapshot or snapshot_of(login)


def snapshot_of(
    login: str = "ada", description: str = "a ledger", stars: int = 3, **changes: object
) -> GithubSnapshot:
    repos = [Repo("ledger-core", description, "Rust", False, False, stars, "2026-10-01T00:00:00Z")]
    return GithubSnapshot(
        login=login,
        bio="I build things.",
        repos=repos,
        language_totals={"Rust": 100 * stars},
        contribution_recency="codice pubblicato negli ultimi 3 mesi",
        volatile={"public_repos": 1},
        **changes,  # type: ignore[arg-type]
    )


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("UPDATE ai_sources SET enabled = false, updated_by = NULL"))
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _person(
    session: Session, links: list[str], email: str = "ada@studio.it", *, cv: bool = True
) -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        cognome="Lovelace",
        email=email,
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
        links=links,
    )
    service = FreelancerService(session)
    read, _ = (
        service.apply(data, text_pdf("Ada, backend developer."), "cv.pdf", "")
        if cv
        else service.apply(data)
    )
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _service(
    session: Session,
    fetcher: FakeFetcher | None = None,
    github: FakeGithub | None = None,
    *,
    on: tuple[str, ...] = ("github", "links"),
    now: datetime = NOW,
) -> SourceService:
    for kind in on:
        session.execute(text("UPDATE ai_sources SET enabled = true WHERE kind = :k"), {"k": kind})
    session.commit()
    return SourceService(session, fetcher or FakeFetcher(), github or FakeGithub(), lambda: now)


def _rows(session: Session, freelancer_id: UUID) -> list[FreelancerSource]:
    return list(
        session.scalars(
            select(FreelancerSource)
            .where(FreelancerSource.freelancer_id == freelancer_id)
            .order_by(FreelancerSource.kind, FreelancerSource.position)
            .execution_options(populate_existing=True)
        )
    )


LINKS = ["https://github.com/ada", "https://ada.example/", "https://www.linkedin.com/in/ada"]


def test_switches_default_off_and_nothing_fetched_when_off(clean: Session) -> None:
    person = _person(clean, LINKS)
    fetcher, github = FakeFetcher(), FakeGithub()
    service = SourceService(clean, fetcher, github, lambda: NOW)

    assert service.enabled_kinds() == set()
    assert {r.kind: r.enabled for r in clean.scalars(select(AiSource))} == {
        "github": False,
        "links": False,
    }
    result = service.refetch(person)

    assert result.items == []
    assert fetcher.calls == [] and github.calls == []
    assert _rows(clean, person) == []


def test_each_switch_gates_only_its_own_kind(clean: Session) -> None:
    person = _person(clean, LINKS)
    only_github = _service(clean, on=("github",))
    only_github.refetch(person)
    assert [(r.kind, r.ref) for r in _rows(clean, person)] == [("github", "ada")]
    assert only_github.fetcher.calls == []  # type: ignore[attr-defined]

    only_links = _service(clean, on=("links",))
    clean.execute(text("UPDATE ai_sources SET enabled = false WHERE kind = 'github'"))
    clean.commit()
    only_links.refetch(person)
    assert [(r.kind, r.ref) for r in _rows(clean, person)] == [("link", "https://ada.example/")]


def test_refetch_stores_text_hash_and_time(clean: Session) -> None:
    person = _person(clean, LINKS)
    service = _service(clean)

    result = service.refetch(person)

    rows = {(r.kind, r.ref): r for r in _rows(clean, person)}
    assert set(rows) == {("github", "ada"), ("link", "https://ada.example/")}
    github, link = rows[("github", "ada")], rows[("link", "https://ada.example/")]
    assert github.text.startswith("FONTE: GitHub") and "ledger-core" in github.text
    assert link.text == PAGE and link.position == 1
    for row in rows.values():
        assert row.status == "ok" and len(row.content_sha256) == 64 and row.fetched_at == NOW
    assert result.hashes() == {"github": github.content_sha256, "link:1": link.content_sha256}
    assert service.current(person).hashes() == result.hashes()


def test_github_account_is_not_also_read_as_a_page(clean: Session) -> None:
    person = _person(clean, ["https://github.com/ada", "https://github.com/ada/ledger-core"])
    service = _service(clean)
    service.refetch(person)
    assert service.fetcher.calls == []  # type: ignore[attr-defined]
    assert service.github.calls == ["ada"]  # type: ignore[attr-defined]


def test_volatile_change_keeps_hash(clean: Session) -> None:
    person = _person(clean, LINKS)
    github = FakeGithub(snapshot_of(stars=3))
    fetcher = FakeFetcher({"https://ada.example/": FetchResult("ok", PAGE + "\n© 2026 Ada")})
    first = _service(clean, fetcher, github).refetch(person).hashes()

    github.snapshot = snapshot_of(stars=900)
    fetcher.pages["https://ada.example/"] = FetchResult(
        "ok", PAGE + "\n© 2027 Ada\nUltimo aggiornamento: 27 ottobre 2026"
    )
    later = _service(clean, fetcher, github, now=NOW + timedelta(days=8)).refetch(person)

    assert later.hashes() == first
    stored = _rows(clean, person)
    assert all(row.fetched_at == NOW + timedelta(days=8) for row in stored)
    assert "900 stelle" in next(r for r in stored if r.kind == "github").text


def test_content_change_changes_hash(clean: Session) -> None:
    person = _person(clean, LINKS)
    github = FakeGithub(snapshot_of(description="a ledger"))
    fetcher = FakeFetcher()
    first = _service(clean, fetcher, github).refetch(person).hashes()

    github.snapshot = snapshot_of(description="a ledger and a CLI")
    fetcher.pages["https://ada.example/"] = FetchResult("ok", PAGE + "\nNow leading a team.")
    later = _service(clean, fetcher, github).refetch(person).hashes()

    assert later["github"] != first["github"]
    assert later["link:1"] != first["link:1"]


def test_removed_link_row_deleted_at_next_refetch(clean: Session) -> None:
    person = _person(clean, ["https://github.com/ada", "https://a.example/", "https://b.example/"])
    service = _service(clean)
    service.refetch(person)
    assert len(_rows(clean, person)) == 3

    row = clean.get(Freelancer, person)
    assert row is not None
    row.links = ["https://b.example/"]
    clean.commit()
    # Until the refetch the text is still stored, then it is gone, github's with it.
    assert len(_rows(clean, person)) == 3
    result = service.refetch(person)

    assert [(r.kind, r.ref, r.position) for r in _rows(clean, person)] == [
        ("link", "https://b.example/", 1)
    ]
    assert set(result.hashes()) == {"link:1"}


def test_linkedin_link_never_stored_even_if_in_links(clean: Session) -> None:
    person = _person(
        clean,
        [
            "https://www.linkedin.com/in/ada",
            "https://it.linkedin.com/in/ada",
            "https://linkedin.com/in/ada",
            "https://ada.example/",
        ],
    )
    service = _service(clean)
    service.refetch(person)

    assert [r.ref for r in _rows(clean, person)] == ["https://ada.example/"]
    assert service.fetcher.calls == ["https://ada.example/"]  # type: ignore[attr-defined]
    assert candidate_links(["https://www.linkedin.com/in/ada", "linkedin.com/in/ada"]) == []


def test_links_are_capped_and_numbered_in_order(clean: Session) -> None:
    urls = [f"https://site{n}.example/" for n in range(MAX_LINKS_PER_PERSON + 2)]
    person = _person(clean, urls)
    service = _service(clean, on=("links",))
    result = service.refetch(person)

    assert [item.key for item in result.items] == [
        f"link:{n}" for n in range(1, MAX_LINKS_PER_PERSON + 1)
    ]
    assert [item.ref for item in result.items] == urls[:MAX_LINKS_PER_PERSON]
    assert len(service.fetcher.calls) == MAX_LINKS_PER_PERSON  # type: ignore[attr-defined]


def test_unreadable_page_recorded_with_status(clean: Session) -> None:
    person = _person(clean, ["https://spa.example/", "https://blocked.example/"])
    fetcher = FakeFetcher(
        {
            "https://spa.example/": FetchResult("unreadable"),
            "https://blocked.example/": FetchResult("robots"),
        }
    )
    result = _service(clean, fetcher, on=("links",)).refetch(person)

    assert [(i.ref, i.status, i.text, i.content_sha256) for i in result.items] == [
        ("https://spa.example/", "unreadable", "", ""),
        ("https://blocked.example/", "robots", "", ""),
    ]
    assert result.hashes() == {} and result.readable() == []


def test_a_page_that_stops_being_readable_loses_its_text(clean: Session) -> None:
    person = _person(clean, ["https://ada.example/"])
    fetcher = FakeFetcher()
    _service(clean, fetcher, on=("links",)).refetch(person)
    fetcher.pages["https://ada.example/"] = FetchResult("robots")

    result = _service(clean, fetcher, on=("links",)).refetch(person)

    assert [(i.status, i.text) for i in result.items] == [("robots", "")]


def test_a_failed_fetch_keeps_what_was_read_before(clean: Session) -> None:
    person = _person(clean, ["https://github.com/ada", "https://ada.example/"])
    fetcher = FakeFetcher()
    github = FakeGithub()
    first = _service(clean, fetcher, github).refetch(person)

    fetcher.pages["https://ada.example/"] = FetchResult("error")
    github.snapshot = GithubSnapshot("ada", status="error")
    later = _service(clean, fetcher, github, now=NOW + timedelta(days=8)).refetch(person)

    assert later.hashes() == first.hashes()
    assert all(i.status == "ok" and i.text for i in later.items)
    assert all(i.fetched_at == NOW + timedelta(days=8) for i in later.items)


def test_a_first_failed_fetch_is_recorded_as_error(clean: Session) -> None:
    person = _person(clean, ["https://ada.example/"])
    fetcher = FakeFetcher({"https://ada.example/": FetchResult("error")})
    result = _service(clean, fetcher, on=("links",)).refetch(person)
    assert [(i.status, i.text) for i in result.items] == [("error", "")]


def test_turning_a_kind_off_deletes_its_rows_at_once(clean: Session) -> None:
    person = _person(clean, LINKS)
    admin = _admin(clean)
    service = _service(clean)
    service.refetch(person)

    service.set_enabled(admin, "links", False)

    assert [r.kind for r in _rows(clean, person)] == ["github"]
    assert service.enabled_kinds() == {"github"}


def test_refetch_deletes_rows_of_a_kind_that_is_off(clean: Session) -> None:
    person = _person(clean, LINKS)
    service = _service(clean)
    service.refetch(person)
    clean.execute(text("UPDATE ai_sources SET enabled = false"))
    clean.commit()

    assert service.refetch(person).items == []
    assert _rows(clean, person) == []


def test_set_enabled_writes_admin_action(clean: Session) -> None:
    admin = _admin(clean)
    service = _service(clean, on=())

    service.set_enabled(admin, "github", True)
    service.set_enabled(admin, "github", True)  # nothing changed, nothing written
    service.set_enabled(admin, "github", False)

    row = clean.get(AiSource, "github", populate_existing=True)
    assert row is not None and row.enabled is False and row.updated_by == admin
    actions = [
        (a.entity_type, a.kind, a.admin_id, a.payload)
        for a in AdminActionService(clean).timeline("ai_config", AI_CONFIG_ENTITY_ID, 10)
    ]
    assert len(actions) == 2
    assert {(a[0], a[1], a[2]) for a in actions} == {("ai_config", "ai_switch", admin)}
    payloads = [a[3] for a in actions]
    assert {"source": "github", "changed": ["enabled"], "before": {"enabled": False},
            "after": {"enabled": True}} in payloads  # fmt: skip
    assert {"source": "github", "changed": ["enabled"], "before": {"enabled": True},
            "after": {"enabled": False}} in payloads  # fmt: skip


def test_unknown_kind_refused(clean: Session) -> None:
    with pytest.raises(ValidationFailed):
        _service(clean, on=()).set_enabled(_admin(clean), "linkedin", True)


def test_refetch_of_a_missing_person_is_not_found(clean: Session) -> None:
    with pytest.raises(NotFound):
        _service(clean, on=()).refetch(UUID(int=7))


def test_delete_all_on_person_delete_and_on_erasure(clean: Session) -> None:
    gone, kept = _person(clean, LINKS), _person(clean, LINKS, email="grace@studio.it")
    service = _service(clean)
    service.refetch(gone)
    service.refetch(kept)
    admin = _admin(clean)

    # An admin's delete: the text goes in the same commit, the other person's stays.
    FreelancerService(clean).soft_delete(gone, admin)
    assert _rows(clean, gone) == [] and len(_rows(clean, kept)) == 2

    # A hard delete of the person (an erasure) takes the rows with it, by the foreign key.
    clean.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": kept})
    clean.commit()
    assert _rows(clean, kept) == []
    assert clean.execute(text("SELECT count(*) FROM freelancer_sources")).scalar_one() == 0


def test_delete_all_reports_how_many(clean: Session) -> None:
    person = _person(clean, LINKS)
    service = _service(clean)
    service.refetch(person)
    assert service.delete_all(person) == 2
    clean.commit()
    assert service.delete_all(person) == 0


def test_a_deleted_person_is_not_refetched(clean: Session) -> None:
    person = _person(clean, LINKS)
    service = _service(clean)
    service.refetch(person)
    row = clean.get(Freelancer, person)
    assert row is not None
    row.deleted_at = NOW
    clean.commit()

    assert service.refetch(person).items == []
    assert _rows(clean, person) == []


def test_refetch_due_skips_fresh_and_never_imports_llm(clean: Session) -> None:
    fresh, stale, missing = (
        _person(clean, LINKS, email="a@studio.it"),
        _person(clean, LINKS, email="b@studio.it"),
        _person(clean, LINKS, email="c@studio.it"),
    )
    _service(clean, now=NOW - timedelta(days=10)).refetch(stale)
    _service(clean, now=NOW - timedelta(days=1)).refetch(fresh)
    fetcher, github = FakeFetcher(), FakeGithub()
    service = _service(clean, fetcher, github, now=NOW)

    assert service.refetch_due(limit=10) == 2  # stale and missing; fresh is left alone

    assert {r.fetched_at for r in _rows(clean, stale)} == {NOW}
    assert {r.fetched_at for r in _rows(clean, missing)} == {NOW}
    assert {r.fetched_at for r in _rows(clean, fresh)} == {NOW - timedelta(days=1)}
    assert github.calls.count("ada") == 2
    assert service.refetch_due(limit=10) == 0

    # The weekly pass never reaches a model: not imported by the module, nor by what it
    # imports, as far as a fresh interpreter can tell.
    tree = ast.parse(Path(sources_module.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert "rebase_core.llm" not in imported
    probe = "import sys, rebase_core.sources; sys.exit('rebase_core.llm' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0


def test_refetch_due_respects_the_limit_and_the_order(clean: Session) -> None:
    people = [_person(clean, LINKS, email=f"p{n}@studio.it") for n in range(3)]
    service = _service(clean)
    assert service.refetch_due(limit=2) == 2
    assert sum(1 for p in people if _rows(clean, p)) == 2
    assert service.refetch_due(limit=2) == 1


def test_refetch_due_picks_up_a_changed_link_list(clean: Session) -> None:
    person = _person(clean, ["https://a.example/"])
    service = _service(clean, on=("links",))
    service.refetch(person)
    assert service.refetch_due(limit=5) == 0

    row = clean.get(Freelancer, person)
    assert row is not None
    row.links = ["https://a.example/", "https://b.example/"]
    clean.commit()

    assert service.refetch_due(limit=5) == 1
    assert len(_rows(clean, person)) == 2


def test_refetch_due_ignores_people_with_nothing_to_read(clean: Session) -> None:
    only_linkedin = _person(clean, ["https://www.linkedin.com/in/ada"], email="a@studio.it")
    no_links = _person(clean, [], email="b@studio.it")
    service = _service(clean)

    assert service.refetch_due(limit=10) == 0
    assert service.fetcher.calls == [] and service.github.calls == []  # type: ignore[attr-defined]
    assert _rows(clean, only_linkedin) == [] and _rows(clean, no_links) == []


def test_refetch_due_with_every_switch_off_touches_nothing(clean: Session) -> None:
    _person(clean, LINKS)
    service = SourceService(clean, FakeFetcher(), FakeGithub(), lambda: NOW)
    assert service.refetch_due(limit=10) == 0


# ---- the pure helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        (["ada.example"], ["https://ada.example"]),
        (["http://ada.example/x#top"], ["https://ada.example/x"]),
        (["https://a.example/", "https://a.example/"], ["https://a.example/"]),
        (["mailto:ada@example.org", "ftp://x.example/", "  ", ""], []),
        (["https://github.com/ada", "https://www.github.com/ada/repo"], []),
        (["https://LinkedIn.com/in/ada", "https://x.linkedin.com./in/ada"], []),
    ],
)
def test_candidate_links(links: list[str], expected: list[str]) -> None:
    assert candidate_links(links) == expected


def test_link_hash_material_drops_clocks_and_full_dates_only() -> None:
    page = (
        "Ada Lovelace\nSenior engineer 2019 to 2023\nPublished 2026-10-01\n"
        "Updated 27 October 2026 and again 1/2/2027\n© 2026 Ada\nLast updated: today"
    )
    material = link_hash_material(page)
    assert "senior engineer 2019 to 2023" in material
    assert "2026-10-01" not in material and "october" not in material and "©" not in material
    assert "last updated" not in material
    assert link_hash_material(page + "\nNew project: a CLI") != material
    assert link_hash_material("A   B\n\nC") == link_hash_material("a b\nc")


# ---- the weekly pass, review of #671 -------------------------------------------------------


class _Flaky(FakeGithub):
    """GitHub that dies for one account, the way `urllib_call` does: with an exception."""

    def fetch(self, login: str) -> GithubSnapshot:
        self.calls.append(login)
        if login == "boom":
            raise TimeoutError("timed out")
        return snapshot_of(login)


def test_one_persons_failure_does_not_stop_the_pass(clean: Session) -> None:
    bad = _person(clean, ["https://github.com/boom"], email="bad@studio.it")
    good = _person(clean, ["https://github.com/ada"], email="good@studio.it")
    service = _service(clean, FakeFetcher(), _Flaky(), on=("github",))

    assert service.refetch_due(limit=10) == 2

    assert _rows(clean, bad) == [] and [r.ref for r in _rows(clean, good)] == ["ada"]


def test_a_rate_limited_account_keeps_the_text_it_had(clean: Session) -> None:
    """GitHub's 403 is `error` (`GithubSource`), and an `error` keeps what was read: the
    text and the hash stay, so the card does not turn stale for a limit."""
    person = _person(clean, ["https://github.com/ada"])
    _service(clean, on=("github",)).refetch(person)
    before = [(r.text, r.content_sha256) for r in _rows(clean, person)]
    limited = FakeGithub(GithubSnapshot("ada", status="error"))

    _service(clean, FakeFetcher(), limited, on=("github",), now=NOW + timedelta(days=8)).refetch(
        person
    )

    assert [(r.text, r.content_sha256) for r in _rows(clean, person)] == before
    assert {r.status for r in _rows(clean, person)} == {"ok"}
    assert {r.attempts for r in _rows(clean, person)} == {1}  # and it is tried again soon


def test_the_pass_reads_only_people_a_card_is_written_for(clean: Session) -> None:
    kept = _person(clean, LINKS, email="a@studio.it")
    turned_down = _person(clean, LINKS, email="b@studio.it")
    no_cv = _person(clean, LINKS, email="c@studio.it", cv=False)
    service = _service(clean, now=NOW - timedelta(days=10))
    for person in (kept, turned_down):
        service.refetch(person)
    clean.execute(
        text("UPDATE freelancers SET stato = 'scartato' WHERE id = :i"), {"i": turned_down}
    )
    clean.commit()

    assert _service(clean, now=NOW).refetch_due(limit=10) == 1

    assert {r.fetched_at for r in _rows(clean, kept)} == {NOW}
    # What a person turned down had stored goes, and nobody without a CV is ever read.
    assert _rows(clean, turned_down) == [] and _rows(clean, no_cv) == []


def test_removing_the_last_link_deletes_its_text_at_the_next_pass(clean: Session) -> None:
    person = _person(clean, ["https://ada.example/"])
    service = _service(clean, on=("links",))
    service.refetch(person)
    row = clean.get(Freelancer, person)
    assert row is not None
    row.links = []
    clean.commit()

    assert service.refetch_due(limit=5) == 1

    assert _rows(clean, person) == []


def test_a_person_deleted_while_the_pages_were_read_gets_nothing_stored(clean: Session) -> None:
    person = _person(clean, LINKS)

    class Deleting(FakeFetcher):
        def fetch(self, url: str) -> FetchResult:
            clean.execute(
                text("UPDATE freelancers SET deleted_at = now() WHERE id = :i"), {"i": person}
            )
            clean.commit()
            return super().fetch(url)

    service = _service(clean, Deleting())
    service.session.expire_all()

    assert service.refetch(person).items == []
    assert _rows(clean, person) == []


def test_a_switch_turned_off_while_the_pages_were_read_stores_nothing(clean: Session) -> None:
    person = _person(clean, LINKS)

    class Switching(FakeFetcher):
        def fetch(self, url: str) -> FetchResult:
            clean.execute(text("UPDATE ai_sources SET enabled = false WHERE kind = 'links'"))
            clean.commit()
            return super().fetch(url)

    service = _service(clean, Switching(), on=("links",))

    service.refetch(person)

    assert _rows(clean, person) == []


# ---- a failure is retried on a backoff; GitHub's limit is the pass's (REB-844) ---------------


@pytest.mark.parametrize(
    ("attempts", "hours"),
    [(0, 1), (1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 24), (7, 24), (10_000, 24)],
)
def test_the_backoff_doubles_from_an_hour_up_to_a_day(attempts: int, hours: int) -> None:
    assert retry_delay(attempts) == timedelta(hours=hours)


def test_an_error_row_is_retried_on_a_growing_backoff_and_forgotten_on_success(
    clean: Session,
) -> None:
    person = _person(clean, ["https://github.com/ada"])
    github = FakeGithub(GithubSnapshot("ada", status="error"))
    moment = NOW

    for attempts, hours in enumerate([1, 2, 4, 8, 16, 24, 24], start=1):
        service = _service(clean, FakeFetcher(), github, on=("github",), now=moment)
        assert service.refetch_due(limit=10) == 1
        (row,) = _rows(clean, person)
        assert (row.status, row.attempts, row.fetched_at) == ("error", attempts, moment)

        wait = timedelta(hours=hours)
        early = _service(clean, FakeFetcher(), github, on=("github",), now=moment + wait * 0.99)
        assert early.refetch_due(limit=10) == 0, f"after {attempts} failures, before {hours}h"
        moment += wait  # the pass of exactly that hour, as the hourly loop reaches it

    github.snapshot = None  # GitHub answers again
    service = _service(clean, FakeFetcher(), github, on=("github",), now=moment)
    assert service.refetch_due(limit=10) == 1
    (row,) = _rows(clean, person)
    assert (row.status, row.attempts, row.fetched_at) == ("ok", 0, moment)
    # Back on the weekly clock: nothing for six days, then the normal refresh.
    later = _service(clean, FakeFetcher(), github, on=("github",), now=moment + timedelta(days=6))
    assert later.refetch_due(limit=10) == 0
    week = _service(
        clean, FakeFetcher(), github, on=("github",), now=moment + timedelta(days=7, seconds=1)
    )
    assert week.refetch_due(limit=10) == 1


def test_a_source_that_was_read_and_then_fails_is_retried_within_the_hour_too(
    clean: Session,
) -> None:
    person = _person(clean, ["https://github.com/ada"])
    _service(clean, on=("github",)).refetch(person)
    (before,) = _rows(clean, person)
    failing = FakeGithub(GithubSnapshot("ada", status="error"))
    fails_at = NOW + timedelta(days=8)

    _service(clean, FakeFetcher(), failing, on=("github",), now=fails_at).refetch(person)

    (row,) = _rows(clean, person)
    assert (row.text, row.status, row.attempts, row.fetched_at) == (
        before.text,
        "ok",
        1,
        fails_at,
    )
    half = _service(
        clean, FakeFetcher(), failing, on=("github",), now=fails_at + timedelta(minutes=30)
    )
    assert half.refetch_due(limit=10) == 0
    retry = _service(
        clean, FakeFetcher(), FakeGithub(), on=("github",), now=fails_at + timedelta(hours=1)
    )
    assert retry.refetch_due(limit=10) == 1
    assert {r.attempts for r in _rows(clean, person)} == {0}


def test_a_limited_read_writes_nothing_not_even_the_count(clean: Session) -> None:
    person = _person(clean, ["https://github.com/ada"])
    failing = FakeGithub(GithubSnapshot("ada", status="error"))
    _service(clean, FakeFetcher(), failing, on=("github",), now=NOW).refetch(person)
    _service(clean, FakeFetcher(), failing, on=("github",), now=NOW + timedelta(hours=1)).refetch(
        person
    )
    (before,) = _rows(clean, person)
    assert before.attempts == 2

    limited = FakeGithub(GithubSnapshot("ada", status="limited"))
    _service(clean, FakeFetcher(), limited, on=("github",), now=NOW + timedelta(hours=5)).refetch(
        person
    )

    (row,) = _rows(clean, person)
    assert (row.status, row.attempts, row.fetched_at) == ("error", 2, before.fetched_at)


class GithubApi:
    """GitHub's API behind the `HttpHeadersCall` seam with the unauthenticated hourly
    budget. Every call spends one; the answer that takes the last says `remaining: 0`;
    any call over the budget is a 403 naming the reset. `hour_turns()` is the window
    resetting. Every account has no repository, so a person costs two calls, and the
    logins in `broken` answer 502."""

    def __init__(self, budget: int, clock: "Clock") -> None:
        self.budget = budget
        self.clock = clock
        self.remaining = budget
        self.reset = clock.now + timedelta(minutes=40)
        self.calls: list[str] = []
        self.broken: set[str] = set()

    def hour_turns(self) -> None:
        self.remaining = self.budget
        self.reset = self.clock.now + timedelta(hours=1)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append(url)
        window = {"x-ratelimit-reset": str(int(self.reset.timestamp()))}
        if self.remaining == 0:
            return 403, {**window, "x-ratelimit-remaining": "0"}, b'{"message": "rate limit"}'
        self.remaining -= 1
        answered = {**window, "x-ratelimit-remaining": str(self.remaining)}
        login = url.removeprefix("https://api.github.com/users/").split("/")[0]
        if login in self.broken:
            return 502, answered, b"{}"
        if url.endswith("/repos?type=owner&per_page=100&sort=pushed"):
            return 200, answered, b"[]"
        return 200, answered, json.dumps({"login": login, "type": "User", "bio": login}).encode()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _pass(
    session: Session, api: GithubApi, fetcher: FakeFetcher | None = None
) -> tuple[int, SourceService]:
    """One hourly pass: a new process, so a new `GithubSource` with no memory of the last."""
    service = SourceService(
        session,
        fetcher or FakeFetcher(),
        GithubSource(api, "", now=api.clock),
        api.clock,
    )
    return service.refetch_due(limit=50), service


def _github_rows(session: Session, people: list[UUID]) -> list[str]:
    """Per person, in order: the status of their GitHub row, or `-` for none."""
    result = []
    for person in people:
        rows = [r for r in _rows(session, person) if r.kind == "github"]
        result.append(rows[0].status if rows else "-")
    return result


def test_a_spent_budget_stops_the_pass_and_leaves_the_rest_untouched(
    clean: Session, caplog: pytest.LogCaptureFixture
) -> None:
    people = [
        _person(clean, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it") for n in range(5)
    ]
    clean.execute(text("UPDATE ai_sources SET enabled = true"))
    clean.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=4, clock=clock)  # two people's worth

    with caplog.at_level(logging.WARNING, logger="rebase_core.sources"):
        read, _ = _pass(clean, api)

    assert read == 2
    assert _github_rows(clean, people) == ["ok", "ok", "-", "-", "-"]
    assert len(api.calls) == 4  # no call over the budget: the third person was never asked
    assert "3 people wait for the next pass" in caplog.text
    assert clean.scalar(select(FreelancerSource).where(FreelancerSource.status == "error")) is None


def test_the_person_the_limit_cut_off_still_gets_their_links_read(clean: Session) -> None:
    links = _person(clean, ["https://github.com/dev0", "https://ada.example/"], email="a@studio.it")
    github_only = _person(clean, ["https://github.com/dev1"], email="b@studio.it")
    clean.execute(text("UPDATE ai_sources SET enabled = true"))
    clean.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=0, clock=clock)
    fetcher = FakeFetcher()

    read, _ = _pass(clean, api, fetcher)

    assert read == 0  # the first person's account was left for the next pass
    assert [(r.kind, r.status) for r in _rows(clean, links)] == [("link", "ok")]
    assert _rows(clean, github_only) == []
    assert len(api.calls) == 1 and fetcher.calls == ["https://ada.example/"]

    clock.now += timedelta(hours=1)
    api.budget = 60
    api.hour_turns()
    read, _ = _pass(clean, api, fetcher)
    assert read == 2
    assert _github_rows(clean, [links, github_only]) == ["ok", "ok"]


def test_a_later_pass_resumes_where_the_limit_stopped_the_last(clean: Session) -> None:
    people = [
        _person(clean, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it") for n in range(5)
    ]
    clean.execute(text("UPDATE ai_sources SET enabled = true"))
    clean.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=4, clock=clock)
    seen = []

    for _hour in range(3):
        _pass(clean, api)
        seen.append(_github_rows(clean, people))
        clock.now += timedelta(hours=1)
        api.hour_turns()

    assert seen == [
        ["ok", "ok", "-", "-", "-"],
        ["ok", "ok", "ok", "ok", "-"],
        ["ok", "ok", "ok", "ok", "ok"],
    ]
    assert {r.attempts for p in people for r in _rows(clean, p)} == {0}
    read, _ = _pass(clean, api)
    assert read == 0  # everyone is read: nothing is due for a week


def test_one_broken_account_is_backed_off_while_the_rest_are_read(clean: Session) -> None:
    people = [
        _person(clean, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it") for n in range(3)
    ]
    clean.execute(text("UPDATE ai_sources SET enabled = true"))
    clean.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=60, clock=clock)
    api.broken = {"dev1"}

    _pass(clean, api)
    assert _github_rows(clean, people) == ["ok", "error", "ok"]
    calls = len(api.calls)

    clock.now += timedelta(minutes=30)
    _pass(clean, api)
    assert len(api.calls) == calls  # inside its hour: nothing asked

    clock.now += timedelta(minutes=30)
    api.broken = set()
    _pass(clean, api)
    assert _github_rows(clean, people) == ["ok", "ok", "ok"]
    assert len(api.calls) == calls + 2  # one person, two calls
