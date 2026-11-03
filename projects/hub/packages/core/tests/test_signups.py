"""The rebase signup list: one table, one idempotent write, and an answer that says
nothing about the row.

The container `hub_engine` starts is brought to `head` by this package's migrations,
so the `signups` table here is the one production has.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.service import SignupService
from rebase_core.signup_schemas import (
    SignupAck,
    SignupCreate,
    SignupListItem,
    SignupRead,
    SignupUtm,
)


def _create(email: str, **extra: object) -> SignupCreate:
    """The form's three required answers. `nome` and `last_name` are not optional any
    more, so every test that only cares about the address says so through here."""
    return SignupCreate(email=email, nome="Ada", last_name="Lovelace", **extra)  # type: ignore[arg-type]


def _stored(session: Session, email: str) -> SignupListItem:
    """What is actually in the row. `subscribe` deliberately answers with nothing about
    it (see `SignupAck`), so the only way to read a signup is the list an admin reads."""
    listed = SignupService(session).list_recent(limit=100).signups
    return next(item for item in listed if item.email == email)


def test_the_first_signup_is_new_and_lowercased(hub_session: Session) -> None:
    result = SignupService(hub_session).subscribe(_create("Ada@Studio.IT"))
    assert result.nuova is True
    assert result.email == "ada@studio.it"
    assert result.created_at.tzinfo is not None


def test_the_same_address_again_is_one_row_and_a_second_success(
    hub_session: Session,
) -> None:
    service = SignupService(hub_session)
    first = service.subscribe(_create("ada@studio.it"))
    second = service.subscribe(_create("ADA@studio.it"))
    assert second.nuova is False
    assert second.id == first.id
    assert hub_session.execute(text("SELECT count(*) FROM signups")).scalar() == 1


def test_an_address_that_is_not_one_is_refused_before_the_database() -> None:
    with pytest.raises(ValidationError):
        _create("non-e-una-email")


def test_the_list_is_newest_first_and_counts_everything(hub_session: Session) -> None:
    service = SignupService(hub_session)
    for address in ("prima@studio.it", "seconda@studio.it", "terza@studio.it"):
        service.subscribe(_create(address))
    page = service.list_recent(limit=2)
    assert page.total == 3
    assert [item.email for item in page.signups] == ["terza@studio.it", "seconda@studio.it"]


def test_the_attribution_is_stored_with_the_first_signup_and_never_overwritten(
    hub_session: Session,
) -> None:
    service = SignupService(hub_session)
    service.subscribe(
        _create(
            "ada@studio.it",
            utm=SignupUtm(utm_source="linkedin", utm_medium="paid-social", utm_id="{{AD_SET_ID}}"),
        )
    )
    first = _stored(hub_session, "ada@studio.it")
    assert (first.utm_source, first.utm_medium, first.utm_id) == (
        "linkedin",
        "paid-social",
        "{{AD_SET_ID}}",
    )
    assert first.utm_campaign is None
    again = service.subscribe(_create("ada@studio.it", utm=SignupUtm(utm_source="newsletter")))
    assert again.nuova is False
    assert _stored(hub_session, "ada@studio.it").utm_source == "linkedin"


def test_no_attribution_is_stored_as_nothing(hub_session: Session) -> None:
    SignupService(hub_session).subscribe(_create("bob@studio.it"))
    item = _stored(hub_session, "bob@studio.it")
    assert item.utm_source is None and item.utm_id is None


def test_the_person_signs_with_a_name_and_the_name_is_trimmed(hub_session: Session) -> None:
    SignupService(hub_session).subscribe(
        SignupCreate(email="ada@studio.it", nome="  Ada  ", last_name=" Lovelace ")
    )
    stored = _stored(hub_session, "ada@studio.it")
    assert (stored.nome, stored.last_name) == ("Ada", "Lovelace")
    assert stored.linkedin_url is None


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_name_that_is_only_space_is_refused_before_the_database(blank: str) -> None:
    with pytest.raises(ValidationError):
        SignupCreate(email="ada@studio.it", nome=blank, last_name="Lovelace")
    with pytest.raises(ValidationError):
        SignupCreate(email="ada@studio.it", nome="Ada", last_name=blank)


def test_a_name_longer_than_the_column_is_refused() -> None:
    with pytest.raises(ValidationError):
        SignupCreate(email="ada@studio.it", nome="a" * 121, last_name="Lovelace")


@pytest.mark.parametrize(
    "value",
    [
        "https://www.linkedin.com/in/ada",
        "https://linkedin.com/in/ada",
        "https://it.linkedin.com/in/ada",
        # What people paste (ORB-203): no scheme, from a phone's address bar or share
        # sheet, with LinkedIn's own tracking on the end, or `http`.
        "linkedin.com/in/ada",
        "www.linkedin.com/in/ada/",
        "  it.linkedin.com/in/ada  ",
        "https://www.linkedin.com/in/ada?utm_source=share&utm_medium=member_ios",
        "https://www.linkedin.com/in/ada/details/experience/#top",
        "http://www.linkedin.com/in/ada",
        "HTTPS://WWW.LINKEDIN.COM/in/ada",
    ],
)
def test_a_personal_profile_is_stored_in_one_shape_however_it_was_pasted(
    hub_session: Session, value: str
) -> None:
    SignupService(hub_session).subscribe(_create("ada@studio.it", linkedin_url=value))
    assert _stored(hub_session, "ada@studio.it").linkedin_url == "https://www.linkedin.com/in/ada"


def test_a_name_is_stored_decoded_so_one_person_has_one_address(hub_session: Session) -> None:
    SignupService(hub_session).subscribe(
        _create("ada@studio.it", linkedin_url="linkedin.com/in/j%C3%BCrgen-m")
    )
    assert _stored(hub_session, "ada@studio.it").linkedin_url == (
        "https://www.linkedin.com/in/j" + chr(0xFC) + "rgen-m"
    )


def test_a_share_link_longer_than_the_column_is_stored_short(hub_session: Session) -> None:
    pasted = "https://www.linkedin.com/in/ada?utm_source=share&trk=" + "x" * 400
    SignupService(hub_session).subscribe(_create("ada@studio.it", linkedin_url=pasted))
    assert _stored(hub_session, "ada@studio.it").linkedin_url == "https://www.linkedin.com/in/ada"


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        ("https://www.linkedin.com/company/rebase", "https://www.linkedin.com/company/rebase"),
        (
            "https://WWW.LinkedIn.com/company/rebase?x=1",
            "https://www.linkedin.com/company/rebase?x=1",
        ),
        ("linkedin.com/pub/ada-lovelace/1/2/3", "https://linkedin.com/pub/ada-lovelace/1/2/3"),
    ],
)
def test_another_page_on_linkedin_is_kept_as_given_over_https(
    hub_session: Session, value: str, stored: str
) -> None:
    SignupService(hub_session).subscribe(_create("ada@studio.it", linkedin_url=value))
    assert _stored(hub_session, "ada@studio.it").linkedin_url == stored


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/in/ada",
        "https://notlinkedin.com/in/ada",
        "https://linkedin.com.evil.com/in/ada",
        "https://evil.com/linkedin.com/ada",
        "linkedin.com.evil.com/in/ada",
        "evil.com/linkedin.com/in/ada",
        "javascript:alert(1)//linkedin.com/",
        "ftp://www.linkedin.com/in/ada",
        # A bare name is the web form's job to complete; the API does not guess.
        "ada",
        # `urllib.parse` strips tab, CR and LF *before* parsing, so this once validated
        # as a URL and was then stored whole, newline and tail included.
        "https://www.linkedin.com/in/ada\nBcc: qualcuno@altrove.it",
        "https://www.linkedin.com/in/ada\tx",
        "linkedin.com/in/ada\nBcc: qualcuno@altrove.it",
        # From the review of PR #113. A browser reads `\\` as `/`, so this host is
        # evil.com in the admin's browser and LinkedIn to `urlsplit`.
        "https://evil.com\\.linkedin.com/company/x",
        "https://user:pw@www.linkedin.com/company/x",
        "https://www.linkedin.com:443/in/ada",
        "linkedin.com:443/in/ada",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/in/ada lovelace",
        "https://www.linkedin.com/in/a<b>",
        "https://www.linkedin.com/in/%2e%2e",
        "https://www.linkedin.com/in/ada" + chr(0x200B),
        "https://www.linkedin.com/in/" + "a" * 300,
    ],
)
def test_a_profile_somewhere_that_is_not_linkedin_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        _create("ada@studio.it", linkedin_url=value)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_no_profile_at_all_is_stored_as_nothing(hub_session: Session, value: str | None) -> None:
    SignupService(hub_session).subscribe(_create("ada@studio.it", linkedin_url=value))
    assert _stored(hub_session, "ada@studio.it").linkedin_url is None


def test_the_list_carries_the_name_and_the_profile(hub_session: Session) -> None:
    service = SignupService(hub_session)
    service.subscribe(
        SignupCreate(
            email="ada@studio.it",
            nome="Ada",
            last_name="Lovelace",
            linkedin_url="https://www.linkedin.com/in/ada",
        )
    )
    item = service.list_recent().signups[0]
    assert (item.nome, item.last_name) == ("Ada", "Lovelace")
    assert item.linkedin_url == "https://www.linkedin.com/in/ada"


def test_a_row_from_before_the_form_asked_learns_the_name_on_the_next_signup(
    hub_session: Session,
) -> None:
    """The twenty-four rows on the server were written when the form asked only for an
    address, so their `nome` is NULL. Someone signing up again is exactly how that gets
    filled in -- and filling an empty column overwrites nothing, which is why this is
    allowed where the attribution is not."""
    hub_session.execute(
        text("INSERT INTO signups (id, email) VALUES (gen_random_uuid(), 'vecchia@studio.it')")
    )
    hub_session.commit()
    again = SignupService(hub_session).subscribe(
        _create("vecchia@studio.it", linkedin_url="https://www.linkedin.com/in/ada")
    )
    assert again.nuova is False
    stored = _stored(hub_session, "vecchia@studio.it")
    assert (stored.nome, stored.last_name) == ("Ada", "Lovelace")
    assert stored.linkedin_url == "https://www.linkedin.com/in/ada"


def test_a_name_already_on_the_list_is_never_overwritten_and_never_disclosed(
    hub_session: Session,
) -> None:
    """The second half is the security half: whoever posts somebody else's address gets
    the same answer either way, and never learns the name that is already there."""
    service = SignupService(hub_session)
    service.subscribe(SignupCreate(email="ada@studio.it", nome="Ada", last_name="Lovelace"))
    again = service.subscribe(
        SignupCreate(email="ada@studio.it", nome="Qualcun", last_name="Altro")
    )
    assert _stored(hub_session, "ada@studio.it").nome == "Ada"
    assert not set(type(again).model_fields) & {"nome", "last_name", "linkedin_url"}


def test_the_public_answer_says_nothing_about_the_row() -> None:
    """`SignupAck` is what crosses the wire on the one unauthenticated write. Anything
    it carried would be readable by anyone who can guess an address."""
    assert set(SignupAck.model_fields) == {"ok"}
    assert not set(SignupRead.model_fields) & {
        "nome",
        "last_name",
        "linkedin_url",
        "utm_source",
        "utm_id",
    }


@pytest.mark.parametrize(
    "value",
    [
        "Ada\nLovelace",
        "Ada\tLovelace",
        "Ada\rLovelace",
        "Ada\x0bLovelace",
        "Ada\x7f",
        "Ada\x85",
        # Right-to-left override: renders as something other than what is stored, which
        # is how a name in a list becomes a different name in whoever reads it.
        "Ada\u202eesrever",
        "Ada\u2066x\u2069",
    ],
)
def test_a_name_with_a_control_character_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        SignupCreate(email="ada@studio.it", nome=value, last_name="Lovelace")
    with pytest.raises(ValidationError):
        SignupCreate(email="ada@studio.it", nome="Ada", last_name=value)


def test_a_key_the_form_does_not_have_is_refused_rather_than_dropped() -> None:
    """A landing served from a stale cache posts the old body; better a 422 the deploy
    notices than a signup silently missing the two fields this change exists for."""
    with pytest.raises(ValidationError):
        SignupCreate.model_validate(
            {"email": "ada@studio.it", "nome": "Ada", "last_name": "Lovelace", "role": "admin"}
        )
