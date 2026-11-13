"""REB-1105: the CRM's engagement form address «Crea match» sends the admin to."""

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fakes_pigro import DEAL, DEAL_URL, PIGRO

from rebase_core.engagement_handoff import form_url, space_app_url

ENGAGEMENT = UUID("0197f0b2-7a3c-7b1e-9c53-2f0a6a1d4e55")
ORGANIZATION = UUID("0197f0b2-0000-7000-8000-000000000001")
CUSTOMER = UUID("0192e0a0-0000-7000-8000-00000000c057")


def test_the_space_is_the_one_the_deal_link_carries() -> None:
    assert space_app_url(DEAL_URL, DEAL) == f"{PIGRO}/ada-lovelace/app"


def test_a_link_that_is_not_https_or_not_this_deals_names_no_space() -> None:
    assert space_app_url(DEAL_URL.replace("https", "http"), DEAL) is None
    assert space_app_url(f"{PIGRO}/ada-lovelace/app/deal/{ENGAGEMENT}", DEAL) is None
    assert space_app_url(f"{PIGRO}/ada-lovelace/deal/{DEAL}", DEAL) is None
    assert space_app_url("", DEAL) is None
    # This machine is the one plain HTTP the hub talks to.
    assert space_app_url(f"http://127.0.0.1:8000/rebase/app/deal/{DEAL}", DEAL) == (
        "http://127.0.0.1:8000/rebase/app"
    )


def test_the_form_opens_on_the_engagements_page_of_the_space_with_everything_prefilled() -> None:
    url = form_url(
        f"{PIGRO}/ada-lovelace/app",
        engagement_id=ENGAGEMENT,
        deal_id=DEAL,
        freelancer_organization_id=ORGANIZATION,
        client_id=CUSTOMER,
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{PIGRO}/ada-lovelace/app/engagements"
    assert {key: values[0] for key, values in parse_qs(parts.query).items()} == {
        "hub_match_id": str(ENGAGEMENT),
        "freelancer_id": str(ORGANIZATION),
        "deal_id": str(DEAL),
        "client_id": str(CUSTOMER),
    }


def test_a_request_with_no_customer_id_leaves_the_client_to_the_form() -> None:
    url = form_url(
        f"{PIGRO}/ada-lovelace/app",
        engagement_id=ENGAGEMENT,
        deal_id=DEAL,
        freelancer_organization_id=ORGANIZATION,
        client_id=None,
    )

    assert "client_id" not in parse_qs(urlsplit(url).query)
