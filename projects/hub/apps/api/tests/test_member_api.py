"""The member area over HTTP: a link in, a cookie out, and only your own row behind it."""

import hashlib
import logging
import re
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest
from fakes_cards import text_pdf
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_core.config import Settings, get_settings
from rebase_core.llm import RecordingCall
from rebase_core.mail import Mail, RecordingSender
from rebase_core.models import GuideDownload, Login, MagicLinkToken, User
from rebase_core.perks import GUIDE_PATH

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


class RefusingSender:
    """A sender the provider always turns away, for the test that a refusal is logged."""

    def send(self, mail: Mail) -> bool:
        return False


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in (
        "guide_downloads",
        "sessions",
        "magic_link_tokens",
        "admin_actions",
        "notes",
        "companies",
        "freelancers",
        "users",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _apply(client: TestClient, email: str, first_name: str = "Ada") -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": first_name,
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _request_company(client: TestClient, email: str = "wile@acme.it", **extra: object) -> None:
    payload: dict[str, object] = {
        "company_name": "ACME Srl",
        "contact_first_name": "Wile",
        "contact_last_name": "E.",
        "email": email,
        "phone": "+39 345 1234567",
        "requested_role": "Backend developer",
        "project": "Serve un backend developer per tre mesi, da ottobre.",
        "period_from": "2026-10-01",
        "duration": "3 mesi",
        "daily_budget": "500",
        "remote": "remote",
        "headcount": 1,
    }
    payload.update(extra)
    response = client.post("/api/hub/companies", json=payload)
    assert response.status_code == 201, response.text


def _enter(
    client: TestClient, sender: RecordingSender, email: str
) -> tuple[dict[str, object], httpx.Response]:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    entered = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert entered.status_code == 200, entered.text
    return entered.json(), entered


def test_a_link_asked_with_a_campaign_writes_it_on_the_login(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """REB-426: the login page sends the attribution it arrived with, and the login the
    link opens carries it. The answer to the request itself does not change."""
    _apply(client, "ada@studio.it")
    utm = {
        "utm_source": "email",
        "utm_medium": "outreach",
        "utm_campaign": "outreach-2026-09-r2",
        "utm_content": "cv",
        "utm_term": "11425b70",
    }
    known = client.post("/api/hub/auth/link", json={"email": "ada@studio.it", "utm": utm})
    unknown = client.post("/api/hub/auth/link", json={"email": "nessuno@studio.it", "utm": utm})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200

    api_session.expire_all()
    login = api_session.scalar(select(Login))
    assert login is not None
    assert (login.utm_campaign, login.utm_content, login.utm_term) == (
        "outreach-2026-09-r2",
        "cv",
        "11425b70",
    )


def test_a_link_asked_without_a_campaign_writes_a_login_without_one(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    api_session.expire_all()
    login = api_session.scalar(select(Login))
    assert login is not None and login.utm_campaign is None and login.utm_source is None


def test_a_malformed_campaign_on_the_link_request_is_a_422(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    # The same bounds the wizards' `SignupUtm` holds, reused as it is: a key it does not
    # know is ignored there too, so a stale page never loses a login over one.
    for utm in ({"utm_campaign": "x" * 201}, {"origin": "Non Uno Slug"}):
        response = client.post("/api/hub/auth/link", json={"email": "ada@studio.it", "utm": utm})
        assert response.status_code == 422, (utm, response.text)
    assert sender.sent == []


def test_without_a_sender_the_link_request_is_a_503_sentence(
    client: TestClient, clean: None
) -> None:
    client.app.dependency_overrides[get_sender] = lambda: None  # type: ignore[attr-defined]
    response = client.post("/api/hub/auth/link", json={"email": "ada@studio.it"})
    assert response.status_code == 503
    assert "non è ancora attivo" in response.json()["detail"]


def test_a_refused_mail_is_logged_without_the_address(
    client: TestClient, clean: None, caplog: pytest.LogCaptureFixture
) -> None:
    # `api_engine`'s `upgrade_to_head` runs Alembic's own `env.py`, whose `fileConfig`
    # disables every logger that already existed and is not in `alembic.ini`'s own
    # `[loggers]` list -- this module's among them. Undo that here so `caplog` can see
    # what this test is about; nothing at runtime relies on the logger being disabled.
    logging.getLogger("rebase_api.routers.members").disabled = False
    logging.getLogger("rebase_core.mail").disabled = False
    client.app.dependency_overrides[get_sender] = lambda: RefusingSender()  # type: ignore[attr-defined]
    _apply(client, "ada@studio.it")
    with caplog.at_level(logging.WARNING):
        response = client.post("/api/hub/auth/link", json={"email": "ada@studio.it"})
    assert response.status_code == 202
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any("the magic link mail" in record.getMessage() for record in warnings)
    assert "ada@studio.it" not in caplog.text


def test_the_link_request_answers_the_same_whether_the_address_applied_or_not(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    known = client.post("/api/hub/auth/link", json={"email": "Ada@studio.it"})
    unknown = client.post("/api/hub/auth/link", json={"email": "nessuno@studio.it"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"ok": True}
    assert [mail.to for mail in sender.sent] == ["ada@studio.it"]
    assert "/verify?t=" in sender.sent[0].text


def _tight_budget(client: TestClient, api_session: Session, room: int) -> None:
    """The settings of an installation whose wizard mail budget has `room` links left today:
    the shared database keeps every test's tokens, so the budget is the day's wizard count
    plus `room`."""
    used = (
        api_session.scalar(
            select(func.count())
            .select_from(MagicLinkToken)
            .where(MagicLinkToken.asked_via == "wizard")
        )
        or 0
    )
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined,call-arg]
        _env_file=None,
        resend_daily_quota=100_000,
        campaign_daily_reserve=50_000,
        public_mail_daily_budget=max(used + room, 1),
    )


def test_sign_in_is_never_closed_by_the_wizards_budget_or_by_other_peoples_logins(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """The review's blocker: 16 members each ask for their link on one day, with the
    wizard's budget already spent, and every one gets it."""
    addresses = [f"m{index}@studio.it" for index in range(16)]
    for address in addresses:
        _apply(client, address)
    _tight_budget(client, api_session, room=0)

    for address in addresses:
        assert client.post("/api/hub/auth/link", json={"email": address}).status_code == 202

    assert sorted(mail.to for mail in sender.sent) == sorted(addresses)


def test_one_person_asking_again_and_again_stops_at_their_own_cap_in_silence(
    client: TestClient,
    sender: RecordingSender,
    api_session: Session,
    clean: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A person has a daily cap of their own: past it no mail is sent and the answer is the
    one every other request gets (it must not say whether the address exists), and the log
    says so without the address. Nobody else is affected."""
    logging.getLogger("rebase_core.users").disabled = False
    _apply(client, "ada@studio.it")
    _apply(client, "grace@studio.it")
    cap = Settings(_env_file=None).magic_link_daily_per_user  # type: ignore[call-arg]

    with caplog.at_level(logging.WARNING):
        for _ in range(cap + 2):
            assert (
                client.post("/api/hub/auth/link", json={"email": "ada@studio.it"}).status_code
                == 202
            )
            # Past the per-address minute throttle (unspent links only), still today: the
            # links are spent, not moved back, so the test is the same near midnight.
            api_session.execute(
                text("UPDATE magic_link_tokens SET used_at = now() WHERE used_at IS NULL")
            )
            api_session.commit()
        assert (
            client.post("/api/hub/auth/link", json={"email": "grace@studio.it"}).status_code == 202
        )

    assert [mail.to for mail in sender.sent].count("ada@studio.it") == cap
    assert [mail.to for mail in sender.sent].count("grace@studio.it") == 1
    assert "daily cap" in caplog.text and "ada@studio.it" not in caplog.text


def _reapply(client: TestClient, email: str, first_name: str) -> int:
    """An application, without a CV, from an address that is already on file."""
    return client.post(
        "/api/hub/freelancers",
        data={
            "first_name": first_name,
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
    ).status_code


def test_an_application_for_an_address_on_file_sends_no_mail_once_the_budget_is_spent(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _apply(client, "grace@studio.it")
    assert _reapply(client, "ada@studio.it", "Ada") == 201  # the wizard's one mail
    _tight_budget(client, api_session, room=0)  # that spent the wizard's budget

    # The same answer as always: nothing says what happened.
    assert _reapply(client, "grace@studio.it", "Grace") == 201
    assert [mail.to for mail in sender.sent] == ["ada@studio.it"]


def test_the_link_enters_once_sets_the_member_cookie_and_opens_only_the_members_routes(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    for path in ("/api/hub/me", "/api/hub/me/cv", "/api/hub/me/guide"):
        assert client.get(path).status_code == 401, path

    profile, entered = _enter(client, sender, "ada@studio.it")
    assert profile["email"] == "ada@studio.it"
    assert "status" not in profile and "note" not in profile and "utm_source" not in profile
    cookie = client.cookies.get("rebase_user")
    assert cookie
    set_cookies = _set_cookies(entered)
    set_cookie = next(line for line in set_cookies if line.startswith("rebase_user="))
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "max-age=2592000" in set_cookie  # settings.member_session_days * 86400, 30 days
    # The name before REB-668 is dropped in the same answer, so a browser that still holds
    # it does not keep two cookies for one session.
    assert any(line.startswith("orbiters_user=") and "max-age=0" in line for line in set_cookies)
    assert client.get("/api/hub/me").json()["first_name"] == "Ada"

    # The same link a second time opens nothing.
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    again = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert again.status_code == 401
    assert again.json()["detail"].startswith("Link non valido o scaduto")

    # A member cookie is a real identity, just not an admin's: 403, not 401
    # (REB-278's own AdminDep, §4).
    assert client.get("/api/hub/freelancers").status_code == 403

    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    assert 'filename="Ada CV.pdf"' in cv.headers["content-disposition"]

    logged_out = client.post("/api/hub/me/logout")
    assert logged_out.status_code == 204
    cleared = _set_cookies(logged_out)
    assert any(line.startswith("rebase_user=") and "max-age=0" in line for line in cleared)
    assert any(line.startswith("orbiters_user=") and "max-age=0" in line for line in cleared)
    assert client.get("/api/hub/me").status_code == 401


def _set_cookies(response: httpx.Response) -> list[str]:
    """Every `Set-Cookie` line of an answer, lower-cased: `headers[...]` would join them."""
    return [line.lower() for line in response.headers.get_list("set-cookie")]


def _as_legacy_cookie(client: TestClient) -> str:
    """Turn the session the client just opened into one carried under the cookie's name
    before REB-668, and nothing else: what a browser signed in before the deploy sends."""
    raw = client.cookies.get("rebase_user")
    assert raw
    client.cookies.clear()
    client.cookies.set("orbiters_user", raw)
    return raw


def test_a_session_opened_under_the_old_cookie_name_stays_signed_in_and_moves_to_the_new_one(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """REB-668: a member who signed in before the rename holds `orbiters_user`. They must
    not be logged out by it: the API reads the old name, answers as normal, and reissues
    the same session under `rebase_user` with a fresh 30 days, dropping the old name."""
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    raw = _as_legacy_cookie(client)

    me = client.get("/api/hub/me")
    assert me.status_code == 200
    assert me.json()["first_name"] == "Ada"
    moved = _set_cookies(me)
    adopted = next(line for line in moved if line.startswith("rebase_user="))
    assert adopted.startswith(f"rebase_user={raw.lower()};")  # lower-cased with the line
    assert "max-age=2592000" in adopted and "httponly" in adopted and "secure" in adopted
    assert any(line.startswith("orbiters_user=") and "max-age=0" in line for line in moved)

    # The browser now sends the new name only: same session, nothing left to move.
    client.cookies.clear()
    client.cookies.set("rebase_user", raw)
    again = client.get("/api/hub/me")
    assert again.status_code == 200 and again.json()["first_name"] == "Ada"
    assert _set_cookies(again) == []


def test_an_admin_session_under_the_old_cookie_name_still_opens_the_admin_routes(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    _enter(client, sender, ADMIN_EMAIL)
    assert client.get("/api/hub/freelancers").status_code == 200
    raw = _as_legacy_cookie(client)

    listed = client.get("/api/hub/freelancers")
    assert listed.status_code == 200
    assert any(line.startswith(f"rebase_user={raw.lower()};") for line in _set_cookies(listed))


def test_the_new_cookie_name_wins_when_a_browser_sends_both(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    client.cookies.set("orbiters_user", "a-token-that-opens-nothing")

    me = client.get("/api/hub/me")
    assert me.status_code == 200 and me.json()["first_name"] == "Ada"
    assert _set_cookies(me) == []


def test_a_stale_new_cookie_does_not_hide_a_live_session_under_the_old_name(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """A rollback followed by a roll-forward can leave a browser with a `rebase_user` that
    opens nothing beside an `orbiters_user` that still does: the session is found and the
    live token is reissued under the new name."""
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    raw = _as_legacy_cookie(client)
    client.cookies.set("rebase_user", "a-token-that-opens-nothing")

    me = client.get("/api/hub/me")
    assert me.status_code == 200 and me.json()["first_name"] == "Ada"
    assert any(line.startswith(f"rebase_user={raw.lower()};") for line in _set_cookies(me))


def test_a_download_under_the_old_cookie_name_opens_and_the_next_json_call_moves_the_cookie(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """A route that returns its own `Response` does not carry the reissued cookie, but the
    old name still authorizes it; the session moves on the next call that answers JSON."""
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    raw = _as_legacy_cookie(client)

    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    me = client.get("/api/hub/me")
    assert any(line.startswith(f"rebase_user={raw.lower()};") for line in _set_cookies(me))


def test_logging_out_closes_the_session_behind_each_cookie_name_the_browser_sends(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    first = client.cookies.get("rebase_user")
    client.cookies.clear()
    _enter(client, sender, "ada@studio.it")
    second = client.cookies.get("rebase_user")
    assert first and second and first != second
    client.cookies.clear()
    client.cookies.set("rebase_user", first)
    client.cookies.set("orbiters_user", second)

    assert client.post("/api/hub/me/logout").status_code == 204
    for token in (first, second):
        client.cookies.clear()
        client.cookies.set("rebase_user", token)
        assert client.get("/api/hub/me").status_code == 401


def test_logging_out_under_the_old_cookie_name_closes_the_session_and_clears_both_names(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    raw = _as_legacy_cookie(client)

    logged_out = client.post("/api/hub/me/logout")
    assert logged_out.status_code == 204
    cleared = _set_cookies(logged_out)
    assert any(line.startswith("rebase_user=") and "max-age=0" in line for line in cleared)
    assert any(line.startswith("orbiters_user=") and "max-age=0" in line for line in cleared)

    # The row is closed, not only the browser's copy: the token opens nothing under either name.
    for name in ("rebase_user", "orbiters_user"):
        client.cookies.clear()
        client.cookies.set(name, raw)
        assert client.get("/api/hub/me").status_code == 401, name


def test_a_cv_filename_outside_latin_1_still_downloads_instead_of_500(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """`isalnum()` alone is Unicode-aware and used to let a CJK character or an emoji
    through the sanitiser whole, which Starlette then failed to encode into the
    Latin-1 `Content-Disposition` header at all: the download answered 500 instead of
    the file (REB-241)."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Cheng",
            "last_name": "Wei",
            "email": "cheng@studio.it",
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("简历📄.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text

    _enter(client, sender, "cheng@studio.it")
    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    disposition = cv.headers["content-disposition"]
    assert disposition.isascii()  # the header Starlette must encode as latin-1
    assert 'filename="___.pdf"' in disposition
    assert "filename*=UTF-8''%E7%AE%80%E5%8E%86%F0%9F%93%84.pdf" in disposition


def test_an_accented_cv_filename_keeps_its_real_name_in_filename_star(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """The ASCII fallback alone would turn every accent in an Italian name into an
    underscore (`Niccolò Forlì.pdf` -> `Niccol_ Forl_.pdf`). `filename*` (RFC 5987)
    carries the real name percent-encoded, so a browser that understands it shows the
    person's own name whole."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Niccolò",
            "last_name": "Forlì",
            "email": "niccolo@studio.it",
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Niccolò Forlì.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text

    _enter(client, sender, "niccolo@studio.it")
    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    disposition = cv.headers["content-disposition"]
    assert disposition.isascii()
    assert 'filename="Niccol_ Forl_.pdf"' in disposition
    assert "filename*=UTF-8''Niccol%C3%B2%20Forl%C3%AC.pdf" in disposition


def test_the_guide_is_a_perk_of_the_session_and_not_a_public_file(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """The whole point of ORB-70's second half: the guide is behind the member session.

    Nothing about the file is asserted here beyond it being the one on disk, byte for
    byte, since `packages/core/tests/test_guide_pdf.py` owns what the PDF is. What this
    owns is who may have it: an anonymous caller gets the same 401 as `/me`, and a
    member gets the bytes as an attachment under the name they will see in their
    downloads folder.
    """
    _apply(client, "ada@studio.it")
    assert client.get("/api/hub/me/guide").status_code == 401

    _enter(client, sender, "ada@studio.it")
    answer = client.get("/api/hub/me/guide")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    assert (
        answer.headers["content-disposition"]
        == 'attachment; filename="rebase-guida-primi-passi-freelance.pdf"'
    )
    assert answer.content == GUIDE_PATH.read_bytes()

    client.post("/api/hub/me/logout")
    assert client.get("/api/hub/me/guide").status_code == 401


def test_every_download_of_the_guide_is_written_down_with_the_member_behind_it(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """ORB-156: the admin's counter is a row per download, who and when. Two downloads
    by the same member are two rows for one person, an anonymous 401 writes nothing,
    and the bytes still arrive: recording is a side of the route, not a gate."""
    _apply(client, "ada@studio.it")
    assert client.get("/api/hub/me/guide").status_code == 401
    assert api_session.scalar(select(func.count()).select_from(GuideDownload)) == 0

    profile, _ = _enter(client, sender, "ada@studio.it")
    for _ in range(2):
        answer = client.get("/api/hub/me/guide")
        assert answer.status_code == 200 and answer.content == GUIDE_PATH.read_bytes()
    api_session.expire_all()
    rows = api_session.scalars(select(GuideDownload)).all()
    assert len(rows) == 2
    assert {str(row.user_id) for row in rows} == {profile["id"]}
    assert all(row.downloaded_at is not None for row in rows)


def test_a_wrong_token_is_a_401_and_a_malformed_one_a_422(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    assert client.post("/api/hub/auth/enter", json={"token": "a" * 43}).status_code == 401
    assert client.post("/api/hub/auth/enter", json={"token": "corto"}).status_code == 422


def test_replacing_the_cv_without_a_cookie_is_a_401_even_with_a_valid_pdf(
    client: TestClient, clean: None
) -> None:
    response = client.put("/api/hub/me/cv", files={"cv": ("cv.pdf", PDF, "application/pdf")})
    assert response.status_code == 401


def test_a_member_changes_their_answers_and_the_admin_sees_the_comment(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")

    refused = client.patch(
        "/api/hub/me",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "linkedin_url": "https://example.com/in/ada",
            "daily_rate": "500",
            "job_title": "Backend developer",
            "remote": "remote",
            "links": [],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "linkedin_url"

    changed = client.patch(
        "/api/hub/me",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "linkedin_url": None,
            "daily_rate": "500",
            "job_title": "Staff engineer",
            "remote": "hybrid",
            "links": ["https://github.com/ada"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["job_title"] == "Staff engineer"

    new_pdf = PDF + b"\n% v2\n"
    replaced = client.put(
        "/api/hub/me/cv", files={"cv": ("Ada 2026.pdf", new_pdf, "application/pdf")}
    )
    assert replaced.status_code == 200 and replaced.json()["cv_filename"] == "Ada 2026.pdf"
    not_a_pdf = client.put("/api/hub/me/cv", files={"cv": ("x.pdf", b"ciao", "application/pdf")})
    assert not_a_pdf.status_code == 422 and not_a_pdf.json()["detail"][0]["loc"][-1] == "cv"

    # `PUT /me/cv` now spends from the same public bucket as the wizard's routes
    # (the fix for the unbounded, unmetered upload); a fresh minute here keeps this
    # test about the admin's view of the thread, not about the rate limit's budget.

    # The admin reads the thread the member wrote into.
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    # Log the member out first, so the freelancer thread below is read as the admin,
    # not as Ada still holding the earlier session; REB-278's unified identity puts
    # both roles behind the same `/me`, so the admin also gets a 200 there, with
    # `role` the thing that tells the two apart.
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    assert client.get("/api/hub/me").json()["role"] == "admin"
    listed = client.get("/api/hub/freelancers").json()["items"]
    assert len(listed) == 1 and listed[0]["job_title"] == "Staff engineer"
    thread = client.get(f"/api/hub/freelancers/{listed[0]['id']}/notes").json()
    assert [comment["text"] for comment in thread] == [
        "CV aggiornato dalla persona",
        "Profilo aggiornato dalla persona: tariffa giornaliera, posizione, modalità di lavoro, "
        "link",
    ]
    assert thread[0]["author"] == "Ada Lovelace"


def test_replacing_the_cv_schedules_the_card_write(
    client: TestClient,
    sender: RecordingSender,
    api_session: Session,
    clean: None,
    llm: RecordingCall,
) -> None:
    """REB-510: a new CV is a new anonymous card, written after the 200 in a session of
    its own. The wizard's own CV here is a scan, which asks Claude nothing."""
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    assert llm.requests == []

    cv = text_pdf("Ada Lovelace, staff engineer a Torino: Python, Kubernetes, dieci anni.")
    replaced = client.put("/api/hub/me/cv", files={"cv": ("Ada 2026.pdf", cv, "application/pdf")})
    assert replaced.status_code == 200, replaced.text

    assert len(llm.requests) == 1
    assert "staff engineer a Torino" in llm.requests[0].messages[0].text
    row = api_session.execute(text("SELECT cv_sha256, error FROM freelancer_cards")).one()
    assert (row.cv_sha256, row.error) == (hashlib.sha256(cv).hexdigest(), None)


def test_a_member_never_sees_another_members_row(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it", first_name="Ada")
    _apply(client, "grace@studio.it", first_name="Grace")
    assert _enter(client, sender, "grace@studio.it")[0]["first_name"] == "Grace"
    assert client.get("/api/hub/me").json()["email"] == "grace@studio.it"
    # There is no route that takes an id: the only row reachable is the session's.
    assert client.get("/api/hub/me/00000000-0000-7000-8000-000000000000").status_code == 404


# ---- a company contact's own request (REB-314) -----------------------------------------


def _company_answers(**extra: object) -> dict[str, object]:
    answers: dict[str, object] = {
        "project": "Serve un backend developer per tre mesi, da ottobre.",
        "period_from": "2026-10-01",
        "duration": "4 mesi",
        "daily_budget": "600",
        "remote": "remote",
        "headcount": 1,
        "requested_role": "Backend developer",
    }
    answers.update(extra)
    return answers


def test_the_member_area_lists_every_request_newest_first_without_the_admins_words(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, duration="1 mese", requested_role="Data engineer")
    _request_company(client, duration="3 mesi")
    api_session.execute(text("UPDATE companies SET status = 'in_progress'"))
    api_session.execute(
        text(
            "INSERT INTO notes (id, entity_type, entity_id, text, author) "
            "SELECT gen_random_uuid(), 'company', id, 'da richiamare', 'Ivan' FROM companies"
        )
    )
    api_session.commit()
    profile, _ = _enter(client, sender, "wile@acme.it")

    assert profile["has_company"] is True
    assert [item["duration"] for item in profile["requests"]] == ["3 mesi", "1 mese"]  # type: ignore[index, union-attr]
    older = profile["requests"][1]  # type: ignore[index]
    assert older["requested_role"] == "Data engineer" and older["daily_budget"] == "500.00"
    assert set(older) == {
        "id",
        "requested_role",
        "project",
        "period_from",
        "duration",
        "daily_budget",
        "remote",
        "onsite_days",
        "headcount",
        "created_at",
    }
    # The flattened latest-row fields are gone for good.
    assert "project" not in profile and "azienda_remoto" not in profile


def test_a_company_contact_edits_their_most_recent_request(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, duration="1 mese")
    _request_company(client, duration="3 mesi")
    profile, _ = _enter(client, sender, "wile@acme.it")
    assert profile["has_company"] is True
    assert profile["requests"][0]["duration"] == "3 mesi"  # type: ignore[index]

    refused = client.patch("/api/hub/me/company", json=_company_answers(status="closed"))
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "status"

    changed = client.patch("/api/hub/me/company", json=_company_answers())
    assert changed.status_code == 200, changed.text
    assert [item["duration"] for item in changed.json()["requests"]] == ["4 mesi", "1 mese"]

    # The admin reads the comment the referente left, and the older request stands.
    # `POST /auth/link` and `POST /auth/enter` both spend from the public bucket this
    # test's two `_request_company` calls already drew on; a fresh minute here keeps
    # the test about the thread, not about the rate limit's budget.
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    listed = client.get("/api/hub/companies").json()["items"]
    assert len(listed) == 2
    newest = next(item for item in listed if item["duration"] == "4 mesi")
    thread = client.get(f"/api/hub/companies/{newest['id']}/notes").json()
    assert [comment["text"] for comment in thread] == [
        "Richiesta aggiornata dal referente: durata, budget giornaliero"
    ]
    assert (thread[0]["author"], thread[0]["source"]) == ("Wile E.", "person")
    assert thread[0]["editable"] is False
    older = next(item for item in listed if item["duration"] == "1 mese")
    assert older["daily_budget"] == "500.00"


def test_a_company_contact_edits_an_older_request_by_its_id(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, duration="1 mese")
    _request_company(client, duration="3 mesi")
    profile, _ = _enter(client, sender, "wile@acme.it")
    newest, older = profile["requests"]  # type: ignore[misc]

    refused = client.patch(
        f"/api/hub/me/company/{older['id']}", json=_company_answers(note="scritta da me")
    )
    assert refused.status_code == 422

    changed = client.patch(
        f"/api/hub/me/company/{older['id']}", json=_company_answers(duration="8 mesi")
    )
    assert changed.status_code == 200, changed.text
    assert [(item["id"], item["duration"]) for item in changed.json()["requests"]] == [
        (newest["id"], "3 mesi"),
        (older["id"], "8 mesi"),
    ]

    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    thread = client.get(f"/api/hub/companies/{older['id']}/notes").json()
    assert [comment["text"] for comment in thread] == [
        "Richiesta aggiornata dal referente: durata, budget giornaliero"
    ]
    assert client.get(f"/api/hub/companies/{newest['id']}/notes").json() == []


def test_a_request_by_id_is_a_404_for_somebody_elses_and_for_a_deleted_one(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, email="road@runner.it", duration="7 mesi")
    _request_company(client, email="wile@acme.it", duration="1 mese")
    _request_company(client, email="wile@acme.it", duration="3 mesi")
    theirs = api_session.execute(
        text(
            "SELECT c.id FROM companies c JOIN users u ON u.id = c.user_id "
            "WHERE u.email = 'road@runner.it'"
        )
    ).scalar_one()
    profile, _ = _enter(client, sender, "wile@acme.it")
    newest, older = profile["requests"]  # type: ignore[misc]

    stranger = client.patch(f"/api/hub/me/company/{theirs}", json=_company_answers())
    assert stranger.status_code == 404
    unknown = client.patch(
        "/api/hub/me/company/00000000-0000-7000-8000-000000000000", json=_company_answers()
    )
    assert unknown.status_code == 404

    api_session.execute(
        text("UPDATE companies SET deleted_at = now() WHERE id = :id"), {"id": newest["id"]}
    )
    api_session.commit()
    deleted = client.patch(f"/api/hub/me/company/{newest['id']}", json=_company_answers())
    assert deleted.status_code == 404
    assert [item["id"] for item in client.get("/api/hub/me").json()["requests"]] == [older["id"]]

    untouched = api_session.execute(
        text("SELECT duration FROM companies WHERE id = :id"), {"id": theirs}
    ).scalar_one()
    assert untouched == "7 mesi"


def test_to_member_with_no_company_gets_has_company_false_and_to_404_on_edit(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    profile, _ = _enter(client, sender, "ada@studio.it")
    assert profile["has_company"] is False and profile["requests"] == []

    refused = client.patch(
        "/api/hub/me/company",
        json={
            "project": "Un progetto qualsiasi abbastanza lungo da passare",
            "period_from": "2026-10-01",
            "duration": "3 mesi",
            "daily_budget": "500",
            "remote": "remote",
            "headcount": 1,
            "requested_role": "Backend developer",
        },
    )
    assert refused.status_code == 404


# ---- filing a genuinely new request instead of editing (REB-381) ----------------------


def test_a_company_contact_files_an_additional_request(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, duration="1 mese")
    profile, _ = _enter(client, sender, "wile@acme.it")
    assert profile["has_company"] is True and profile["requests"][0]["duration"] == "1 mese"  # type: ignore[index]

    created = client.post(
        "/api/hub/me/company",
        json={
            "project": "Serve un data engineer per un progetto di sei mesi.",
            "period_from": "2027-01-15",
            "duration": "6 mesi",
            "daily_budget": "650",
            "remote": "hybrid",
            "onsite_days": 2,
            "headcount": 2,
            "requested_role": "Data engineer",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["has_company"] is True
    assert [item["duration"] for item in body["requests"]] == ["6 mesi", "1 mese"]
    assert body["requests"][0]["requested_role"] == "Data engineer"

    # An admin sees two rows, the same company's name on both, the older untouched.
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    listed = client.get("/api/hub/companies").json()["items"]
    matching = [item for item in listed if item["company_name"] == "ACME Srl"]
    assert len(matching) == 2
    newest = next(item for item in matching if item["duration"] == "6 mesi")
    older = next(item for item in matching if item["duration"] == "1 mese")
    assert newest["contact_person"] == older["contact_person"] == "Wile E."
    assert newest["utm_source"] is None


def test_creating_a_company_request_needs_a_session(client: TestClient, clean: None) -> None:
    unauthenticated = client.post(
        "/api/hub/me/company",
        json={
            "project": "Un progetto qualsiasi abbastanza lungo da passare",
            "period_from": "2026-10-01",
            "duration": "3 mesi",
            "daily_budget": "500",
            "remote": "remote",
            "headcount": 1,
            "requested_role": "Backend developer",
        },
    )
    assert unauthenticated.status_code == 401


def test_creating_a_company_request_with_no_existing_one_is_a_404(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    refused = client.post(
        "/api/hub/me/company",
        json={
            "project": "Un progetto qualsiasi abbastanza lungo da passare",
            "period_from": "2026-10-01",
            "duration": "3 mesi",
            "daily_budget": "500",
            "remote": "remote",
            "headcount": 1,
            "requested_role": "Backend developer",
        },
    )
    assert refused.status_code == 404


# ---- completing a card an admin wrote from a signup (ORB-155) -------------------------


def test_a_member_completes_the_card_an_admin_drafted(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    from rebase_core.freelancer_schemas import FreelancerDraft
    from rebase_core.freelancers import FreelancerService
    from rebase_core.service import SignupService
    from rebase_core.signup_schemas import SignupCreate

    signup = SignupService(api_session).subscribe(
        SignupCreate(email="ada@studio.it", first_name="Ada", last_name="Lovelace")
    )
    admin = User(email="claude@rebase.it", first_name="Claude", last_name="", role="admin")
    api_session.add(admin)
    api_session.commit()
    FreelancerService(api_session).draft_from_signup(
        signup.id,
        FreelancerDraft(first_name="Ada", last_name="Lovelace", sources=["https://ada.dev"]),
        admin.id,
        "web",
    )
    profile, _ = _enter(client, sender, "ada@studio.it")
    assert profile["complete"] is False and profile["cv_filename"] is None
    assert client.get("/api/hub/me/cv").status_code == 404

    answered = client.patch(
        "/api/hub/me",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "linkedin_url": None,
            "daily_rate": "500",
            "job_title": "CTO",
            "remote": "hybrid",
            "links": [],
        },
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["complete"] is False
    with_cv = client.put("/api/hub/me/cv", files={"cv": ("Ada CV.pdf", PDF, "application/pdf")})
    assert with_cv.status_code == 200, with_cv.text
    assert with_cv.json()["complete"] is True
    assert client.get("/api/hub/me/cv").status_code == 200
    api_session.execute(text("DELETE FROM signups"))
    api_session.commit()


def test_a_login_shows_up_on_the_admin_side(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    assert client.post("/api/hub/me/logout").status_code == 204
    # The session is gone; the login stays.
    assert client.get("/api/hub/logins").status_code == 401
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    _enter(client, sender, ADMIN_EMAIL)
    stats = client.get("/api/hub/logins").json()
    # The admin's own entry is a login too, since REB-278 a login is for any
    # signed-in person, not only a freelancer with a card.
    assert (stats["total"], stats["members"], stats["total_members"]) == (2, 2, 1)
    assert stats["recent"][0]["email"] == ADMIN_EMAIL
    assert stats["recent"][1]["email"] == "ada@studio.it"
    card = client.get("/api/hub/freelancers").json()["items"][0]
    assert card["logins"] == 1 and card["last_access"] is not None
    detail = client.get(f"/api/hub/freelancers/{card['id']}").json()
    assert detail["logins"] == 1


# --- what PigroCRM may ask (ORB-173) -------------------------------------------------

PIGRO_TOKEN = "un-token-lungo-condiviso-con-il-crm"
LOOKUP = "/api/hub/members/lookup"


@pytest.fixture
def registry_token(client: TestClient) -> None:
    """The installation with `REBASE_PIGRO_REGISTRY_TOKEN` set: the one the CRM's
    signup may ask. Declared, never inherited from a developer's `.env`."""
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        pigro_registry_token=PIGRO_TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )


def _ask(client: TestClient, email: str, **headers: str | bytes) -> httpx.Response:
    return client.post(LOOKUP, json={"email": email}, headers=headers)  # type: ignore[arg-type]


def test_without_a_registry_token_the_member_lookup_does_not_exist(client: TestClient) -> None:
    """The route is a 404 whatever the caller presents, not a 401 that says «there is a
    door»: the same rule as the CRM's `GET /api/tenants/` (ORB-142)."""
    assert _ask(client, "ada@studio.it").status_code == 404
    assert _ask(client, "ada@studio.it", Authorization=f"Bearer {PIGRO_TOKEN}").status_code == 404


def test_the_member_lookup_answers_only_to_the_registry_token(
    client: TestClient, registry_token: None
) -> None:
    assert _ask(client, "ada@studio.it").status_code == 401
    assert _ask(client, "ada@studio.it", Authorization="Bearer sbagliato").status_code == 401
    # Starlette decodes headers as latin-1, and `secrets.compare_digest` refuses a `str`
    # with a non-ASCII character: a stray byte must be a 401 like any wrong token, never a 500.
    odd = _ask(client, "ada@studio.it", Authorization=b"Bearer t\xe9ken")
    assert odd.status_code == 401, odd.text


def test_the_member_lookup_names_a_member_and_says_no_to_anyone_else(
    client: TestClient, registry_token: None, clean: None
) -> None:
    """Case-insensitive, like `uq_freelancers_email_lower`; an unknown address is a plain
    `membro: false` and never an error, and nothing beyond the two names leaves. The
    address travels in the body: a GET with `?email=` would sit in every access log."""
    _apply(client, "Ada@Studio.it")
    bearer = f"Bearer {PIGRO_TOKEN}"

    member = _ask(client, "  ada@studio.IT ", Authorization=bearer)
    assert member.status_code == 200, member.text
    assert member.json() == {"member": True, "first_name": "Ada", "last_name": "Lovelace"}

    unknown = _ask(client, "nessuno@example.org", Authorization=bearer)
    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == {"member": False, "first_name": None, "last_name": None}

    # Not an address at all: still a `false`, the CRM decides what to make of the input.
    assert _ask(client, "non-una-mail", Authorization=bearer).json() == {
        "member": False,
        "first_name": None,
        "last_name": None,
    }
    # No body, or the address in the query string instead: a 422, never a lookup.
    assert client.post(LOOKUP, headers={"Authorization": bearer}).status_code == 422
    assert (
        client.post(
            LOOKUP, params={"email": "ada@studio.it"}, headers={"Authorization": bearer}
        ).status_code
        == 422
    )


def test_a_second_request_from_the_member_area_is_queued_for_the_crm(
    client: TestClient,
    sender: RecordingSender,
    api_session: Session,
    clean: None,
    crm_filings: list[UUID],
) -> None:
    """REB-868: the member's additional request is written `to_link` with its row and
    handed to the CRM filing after the response, as the wizard's is."""
    _request_company(client)
    _enter(client, sender, "wile@acme.it")
    first = crm_filings.copy()

    created = client.post("/api/hub/me/company", json=_company_answers(duration="6 mesi"))

    assert created.status_code == 201, created.text
    assert len(crm_filings) == len(first) + 1
    row = api_session.execute(
        text("SELECT crm_status FROM companies WHERE id = :id"), {"id": crm_filings[-1]}
    ).scalar_one()
    assert row == "to_link"
