"""The CLI's device authorization (REB-1133, spec 2026-11-17 § 2): the codes, the polling, the
single-use token, who may say yes."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, settings

from rebase_core.cli_auth import (
    CODE_SECONDS,
    TOKEN_SECONDS,
    USER_CODE_ALPHABET,
    CliAuthService,
    normalise_code,
    sha256_hex,
)
from rebase_core.errors import CliCodeNotFound, CliNotAllowed, InvalidState
from rebase_core.models import CliAuthorization


def _service(session: Session, clock: Clock) -> CliAuthService:
    return CliAuthService(session, settings(), clock)


def test_a_start_answers_the_codes_and_stores_only_the_hash(hub_session: Session) -> None:
    clock = Clock()
    start = _service(hub_session, clock).start("work_profile")

    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", start.user_code)
    assert not set(start.user_code.replace("-", "")) & set("0O1IL")
    assert set(USER_CODE_ALPHABET).isdisjoint("0O1IL")
    assert start.verification_uri == "https://letsrebase.com/hub/cli"
    assert start.verification_uri_complete == f"{start.verification_uri}?code={start.user_code}"
    assert (start.expires_in, start.interval) == (600, 5)
    row = hub_session.scalars(select(CliAuthorization)).one()
    assert row.device_code_hash == sha256_hex(start.device_code) != start.device_code
    assert row.state == "pending" and row.user_id is None and row.token_hash is None
    assert (row.expires_at - clock()).total_seconds() == CODE_SECONDS


def test_an_unknown_scope_is_refused(hub_session: Session) -> None:
    with pytest.raises(InvalidState):
        _service(hub_session, Clock()).start("everything")


def test_the_whole_flow_ends_in_one_token_handed_out_once(hub_session: Session) -> None:
    clock = Clock()
    service = _service(hub_session, clock)
    user_id, _ = person(hub_session)
    start = service.start("work_profile")

    assert service.poll(start.device_code).error == "authorization_pending"
    read = service.read(start.user_code.lower().replace("-", " "), user_id)
    assert (read.state, read.can_authorize, read.blocked_reason) == ("pending", True, None)
    service.approve(start.user_code, user_id)
    clock.advance(seconds=6)

    polled = service.poll(start.device_code)
    assert polled.error is None and polled.access_token and polled.expires_in == TOKEN_SECONDS
    row = hub_session.scalars(select(CliAuthorization)).one()
    assert row.token_hash == sha256_hex(polled.access_token) and row.user_id == user_id
    assert row.expires_at == clock() + timedelta(seconds=TOKEN_SECONDS)
    clock.advance(seconds=6)
    assert service.poll(start.device_code).error == "expired_token"  # the raw value is gone


def test_polling_faster_than_the_interval_is_slow_down_and_the_interval_grows(
    hub_session: Session,
) -> None:
    clock = Clock()
    service = _service(hub_session, clock)
    start = service.start("work_profile")

    assert service.poll(start.device_code).error == "authorization_pending"
    clock.advance(seconds=2)
    assert service.poll(start.device_code).error == "slow_down"
    clock.advance(seconds=6)  # more than 5, less than the new 10
    assert service.poll(start.device_code).error == "slow_down"
    clock.advance(seconds=16)  # past the interval, now 15
    assert service.poll(start.device_code).error == "authorization_pending"
    assert hub_session.scalars(select(CliAuthorization)).one().poll_interval == 15


def test_a_cancelled_sign_in_is_access_denied(hub_session: Session) -> None:
    clock = Clock()
    service = _service(hub_session, clock)
    user_id, _ = person(hub_session)
    start = service.start("work_profile")
    assert service.deny(start.user_code, user_id).state == "denied"
    assert service.poll(start.device_code).error == "access_denied"
    with pytest.raises(InvalidState):
        service.approve(start.user_code, user_id)


def test_a_code_expires_after_600_seconds(hub_session: Session) -> None:
    clock = Clock()
    service = _service(hub_session, clock)
    user_id, _ = person(hub_session)
    start = service.start("work_profile")
    clock.advance(seconds=CODE_SECONDS)

    assert service.poll(start.device_code).error == "expired_token"
    with pytest.raises(CliCodeNotFound):
        service.approve(start.user_code, user_id)
    assert hub_session.scalars(select(CliAuthorization)).one().state == "expired"


def test_an_unknown_device_code_is_expired_token(hub_session: Session) -> None:
    assert _service(hub_session, Clock()).poll("nope").error == "expired_token"


def test_only_somebody_with_a_live_card_may_authorize(hub_session: Session) -> None:
    clock = Clock()
    service = _service(hub_session, clock)
    stranger, _ = person(hub_session, "nobody@studio.it", card=False)
    start = service.start("work_profile")

    read = service.read(start.user_code, stranger)
    assert (read.can_authorize, read.blocked_reason is not None) == (False, True)
    with pytest.raises(CliNotAllowed):
        service.approve(start.user_code, stranger)
    assert service.poll(start.device_code).error == "authorization_pending"


def test_a_code_with_a_typo_or_the_wrong_length_names_nothing(hub_session: Session) -> None:
    service = _service(hub_session, Clock())
    user_id, _ = person(hub_session)
    for typed in ("", "ABCD", "ABCD-EFG0", "ABCD-EFGHJ"):
        with pytest.raises(CliCodeNotFound):
            service.read(typed, user_id)
    assert normalise_code("abcd efgh") == "ABCD-EFGH"
