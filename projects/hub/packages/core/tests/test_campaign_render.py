"""A campaign mail: the text, the tracked button, the unsubscribe, the tags."""

from datetime import UTC, datetime

import pytest

from rebase_core.campaigns.render import (
    RenderTarget,
    destination,
    person_code,
    personalise,
    render,
    unsubscribe_urls,
)
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.models import Campaign

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def campaign(**fields: object) -> Campaign:
    values: dict[str, object] = {
        "nome": "Manca il CV",
        "slug": "c-2026-09-25-manca-cv",
        "fonte": "stato",
        "stato_percorso": "manca_cv",
        "oggetto": "Manca solo il CV",
        "testo": "Ciao {nome},\n\nmanca il <CV> & poco altro.\nDavvero.",
        "bottone_testo": "Carica il CV",
        "bottone_meta": "area",
        "azione": "cv",
        "contenuto_at": datetime(2026, 9, 25, tzinfo=UTC),
    }
    values.update(fields)
    return Campaign(**values)


TARGET = RenderTarget(
    email="ada@studio.it", nome="Ada", codice="ab12cd34", token="tok", recipient_id="r-1"
)


def test_the_name_goes_in_and_a_missing_one_leaves_a_clean_greeting() -> None:
    assert personalise("Ciao {nome}, come va?", "Ada") == "Ciao Ada, come va?"
    assert personalise("Ciao {nome}, come va?", None) == "Ciao, come va?"


def test_the_button_is_tracked_with_the_campaign_the_action_and_the_person() -> None:
    mail = render(campaign(), TARGET, SETTINGS).mail
    url = (
        "https://letsrebase.com/hub/login?utm_source=email&utm_medium=campagna"
        "&utm_campaign=c-2026-09-25-manca-cv&utm_content=cv&utm_term=ab12cd34"
    )
    assert url in mail.text
    assert url.replace("&", "&amp;") in (mail.html or "")


def test_text_is_escaped_in_html_and_kept_in_text() -> None:
    mail = render(campaign(), TARGET, SETTINGS).mail
    assert "manca il <CV> & poco altro.\nDavvero." in mail.text
    assert "manca il &lt;CV&gt; &amp; poco altro.<br>Davvero." in (mail.html or "")
    assert "<CV>" not in (mail.html or "")


def test_every_mail_carries_the_one_click_unsubscribe() -> None:
    rendered = render(campaign(), TARGET, SETTINGS)
    page, api = unsubscribe_urls("tok", SETTINGS)
    assert page == "https://letsrebase.com/hub/disiscrizione?t=tok"
    assert api == "https://letsrebase.com/api/hub/campagne/disiscrizione?t=tok"
    assert rendered.headers == {
        "List-Unsubscribe": f"<{api}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert page in rendered.mail.text


def test_tags_name_the_campaign_the_action_the_kind_and_the_row() -> None:
    assert render(campaign(), TARGET, SETTINGS).tags == {
        "campaign": "c-2026-09-25-manca-cv",
        "azione": "cv",
        "kind": "real",
        "r": "r-1",
    }
    test = render(
        campaign(), RenderTarget("ivan@rebase.it", "Ivan", "00", "prova"), SETTINGS, test=True
    )
    assert test.tags["kind"] == "test" and "r" not in test.tags
    assert test.mail.subject == "[prova] Manca solo il CV"


@pytest.mark.parametrize(
    ("meta", "path"), [("area", "/login"), ("richiesta", "/login"), ("wizard", "/freelance")]
)
def test_each_destination_is_a_hub_page(meta: str, path: str) -> None:
    assert destination(campaign(bottone_meta=meta), SETTINGS) == f"https://letsrebase.com/hub{path}"


def test_the_pigro_destination_waits_for_phase_three() -> None:
    with pytest.raises(ValidationFailed, match="fase Pigro"):
        destination(campaign(bottone_meta="pigro"), SETTINGS)


def test_the_person_code_is_the_waves_code() -> None:
    assert person_code("Ada@Studio.it") == person_code("ada@studio.it")
    assert len(person_code("ada@studio.it")) == 8


def test_a_render_is_byte_identical_twice() -> None:
    """Resend's Idempotency-Key refuses a retry whose payload changed (409
    `invalid_idempotent_request`): the same row must render the same bytes."""
    assert render(campaign(), TARGET, SETTINGS) == render(campaign(), TARGET, SETTINGS)
