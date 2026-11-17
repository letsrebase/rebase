"""The intake of a work profile (REB-1124, spec 2026-11-17 § 3) and the retention sweep (§ 8)."""

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, sample, settings, sign_in

from rebase_core.cli_auth import CliAuthService
from rebase_core.errors import CliNotAllowed, CliTokenInvalid, WorkProfileRefused
from rebase_core.models import CliAuthorization, Freelancer, WorkProfile
from rebase_core.work_profiles.intake import MAX_BYTES, WorkProfileService, add_months
from rebase_core.work_profiles.sweep import sweep


def _body(profile: dict[str, Any]) -> bytes:
    return json.dumps(profile).encode()


def _submit(session: Session, clock: Clock, token: str, profile: dict[str, Any] | bytes) -> Any:
    body = profile if isinstance(profile, bytes) else _body(profile)
    return WorkProfileService(session, settings(), clock).submit(token, body)


def test_a_good_profile_is_stored_as_a_draft_without_contact_and_spends_the_token(
    hub_session: Session,
) -> None:
    clock = Clock()
    user_id, freelancer_id = person(hub_session)
    token = sign_in(hub_session, clock, user_id)

    answer = _submit(hub_session, clock, token, sample())

    assert (answer.state, answer.review_url) == ("draft", "https://letsrebase.com/hub/me")
    row = hub_session.scalars(select(WorkProfile)).one()
    assert row.freelancer_id == freelancer_id and row.state == "draft"
    assert "contact" not in row.payload and row.payload["summary"] == sample()["summary"]
    assert row.groundedness == 89 and len(row.unsupported_anchors) == 3
    assert row.expires_at == add_months(clock(), 12)
    assert hub_session.scalars(select(CliAuthorization)).one().state == "spent"
    with pytest.raises(CliTokenInvalid):  # spent by the first submission
        _submit(hub_session, clock, token, sample())


def test_a_resubmission_replaces_the_row_and_goes_back_to_draft(hub_session: Session) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    _submit(hub_session, clock, sign_in(hub_session, clock, user_id), sample())
    row = hub_session.scalars(select(WorkProfile)).one()
    row.state, row.decided_by, row.decision_note = "rejected", user_id, "no"
    hub_session.commit()

    again = sample()
    again["summary"] = "Works across 44 sessions on 23 products, research-first."
    clock.advance(days=3)
    _submit(hub_session, clock, sign_in(hub_session, clock, user_id), again)

    hub_session.expire_all()
    row = hub_session.scalars(select(WorkProfile)).one()
    assert row.state == "draft" and row.decided_by is None and row.decision_note is None
    assert row.payload["summary"] == again["summary"] and row.submitted_at == clock()


@pytest.mark.parametrize("token", [None, "", "nonsense"])
def test_an_unknown_token_is_refused_before_the_profile_is_read(
    hub_session: Session, token: str | None
) -> None:
    with pytest.raises(CliTokenInvalid):
        WorkProfileService(hub_session, settings(), Clock()).submit(token, b"not even json")


def test_a_token_expires_after_15_minutes(hub_session: Session) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)
    clock.advance(minutes=15)
    with pytest.raises(CliTokenInvalid):
        _submit(hub_session, clock, token, sample())


def test_a_pending_or_denied_code_is_no_token(hub_session: Session) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    service = CliAuthService(hub_session, settings(), clock)
    start = service.start("work_profile")
    service.deny(start.user_code, user_id)
    for raw in (start.device_code, start.user_code):
        with pytest.raises(CliTokenInvalid):
            _submit(hub_session, clock, raw, sample())


def _refusals() -> list[tuple[str, Any, str]]:
    def with_(change: Any) -> dict[str, Any]:
        profile = sample()
        change(profile)
        return profile

    def many(profile: dict[str, Any]) -> None:
        profile["projects"] = [
            copy.deepcopy(profile["projects"][0]) | {"id": f"q{i}"} for i in range(11)
        ]

    def dup(profile: dict[str, Any]) -> None:
        profile["projects"][1]["id"] = profile["projects"][0]["id"]

    return [
        ("not json", b"{nope", "JSON valido"),
        ("not an object", b"[1]", "oggetto"),
        ("wrong schema", with_(lambda p: p.update(schema="other/v2")), "schema"),
        ("no volume", with_(lambda p: p.pop("volume")), "volume"),
        ("repoLabel", with_(lambda p: p["projects"][0].update(repoLabel="acme")), "repository"),
        (
            "nested repoLabel",
            with_(lambda p: p["otherProjects"][0].update(repoLabel="acme")),
            "repository",
        ),
        (
            "file artifact",
            with_(lambda p: p["projects"][0].update(artifact={"type": "file", "label": "x"})),
            "gli allegati non sono accettati: indica un link",
        ),
        ("negative count", with_(lambda p: p["volume"].update(sessions=-1)), "volume.sessions"),
        ("float count", with_(lambda p: p["projects"][0].update(sessions=1.5)), "sessions"),
        (
            "domains over total",
            with_(lambda p: p.update(domains=[{"label": "x", "products": 999, "sessions": 1}])),
            "domains",
        ),
        ("no project", with_(lambda p: p.update(projects=[])), "da 1 a 10"),
        ("eleven projects", with_(many), "da 1 a 10"),
        ("duplicate ids", with_(dup), "id"),
        ("bad month", with_(lambda p: p["window"].update(to="2026-13")), "window.to"),
        (
            "window reversed",
            with_(lambda p: p["window"].update({"from": "2026-06", "to": "2026-01"})),
            "dopo la fine",
        ),
        (
            "ungrounded prose",
            with_(
                lambda p: p.update(
                    summary=(
                        "Shipped 9999 commits in 8888 sessions on Drizzle, Stripe, Prisma, Zod, "
                        "Jest, Vitest."
                    ),
                    cognitive={"tags": [], "narrative": None},
                    projects=[
                        p["projects"][0] | {"did": None, "whyRepresentative": None, "domain": None}
                    ],
                    domains=None,
                    intensity=None,
                    trajectory=None,
                )
            ),
            "non regge",
        ),
        (
            "nothing to check",
            with_(
                lambda p: p.update(
                    summary=None,
                    cognitive=None,
                    trajectory=None,
                    intensity=None,
                    distribution=None,
                    domains=None,
                    projects=[
                        p["projects"][0] | {"did": None, "whyRepresentative": None, "domain": None}
                    ],
                )
            ),
            "troppo poco",
        ),
    ]


@pytest.mark.parametrize(
    ("name", "profile", "sentence"), _refusals(), ids=[case[0] for case in _refusals()]
)
def test_a_refused_profile_is_a_sentence_changes_nothing_and_spends_the_token(
    hub_session: Session, name: str, profile: Any, sentence: str
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)

    with pytest.raises(WorkProfileRefused) as refused:
        _submit(hub_session, clock, token, profile)

    assert sentence in refused.value.message
    assert hub_session.scalar(select(func.count()).select_from(WorkProfile)) == 0
    assert (
        hub_session.scalars(select(CliAuthorization)).one().state == "spent"
    )  # a refusal costs the sign-in


def test_a_profile_over_512_kb_is_refused(hub_session: Session) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)
    with pytest.raises(WorkProfileRefused, match="512 KB"):
        _submit(hub_session, clock, token, b" " * (MAX_BYTES + 1))


def test_a_token_whose_card_was_deleted_after_the_approval_stores_nothing(
    hub_session: Session,
) -> None:
    clock = Clock()
    user_id, freelancer_id = person(hub_session)
    token = sign_in(hub_session, clock, user_id)
    hub_session.get_one(Freelancer, freelancer_id).deleted_at = datetime.now(UTC)
    hub_session.commit()
    with pytest.raises(CliNotAllowed):
        _submit(hub_session, clock, token, sample())
    assert hub_session.scalar(select(func.count()).select_from(WorkProfile)) == 0


def test_months_are_added_by_the_calendar() -> None:
    assert add_months(datetime(2026, 11, 17, tzinfo=UTC), 12) == datetime(2027, 11, 17, tzinfo=UTC)
    assert add_months(datetime(2028, 2, 29, tzinfo=UTC), 12) == datetime(2029, 2, 28, tzinfo=UTC)


def test_the_sweep_deletes_profiles_past_their_year_and_old_authorizations(
    hub_session: Session,
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    _submit(hub_session, clock, sign_in(hub_session, clock, user_id), sample())  # spent
    CliAuthService(hub_session, settings(), clock).start("work_profile")  # pending, young

    clock.advance(hours=23)
    assert (sweep(hub_session, clock).profiles, sweep(hub_session, clock).authorizations) == (0, 0)

    clock.advance(hours=2)  # the spent one is past its day; the pending code expired 600s in
    counts = sweep(hub_session, clock)
    assert (counts.profiles, counts.authorizations) == (0, 2)

    clock.advance(days=366)
    assert sweep(hub_session, clock).profiles == 1
    assert hub_session.scalar(select(func.count()).select_from(WorkProfile)) == 0
