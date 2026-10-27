"""The sources a card is written from (REB-837): switches off by default, text stored with
a hash that ignores what moves on its own, a removed link deleted at the next refetch,
LinkedIn never stored, everything gone with the person. The fetcher and GitHub are fakes
that count their calls: nothing here reads the network."""

import ast
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import rebase_core.sources as sources_module
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
        super().__init__(lambda *args: (500, b""))
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


def _person(session: Session, links: list[str], email: str = "ada@studio.it") -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        cognome="Lovelace",
        email=email,
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
        links=links,
    )
    read, _ = FreelancerService(session).apply(data)
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
        for a in AdminActionService(clean).timeline(
            "ai_config", sources_module.AI_CONFIG_ENTITY_ID, 10
        )
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
    imported = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
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
