"""REB-1108: the hub no longer has a match, a letter or a signature. Engagements are PigroCRM's;
the one route left of the old matches router is the hand-off to the CRM's form."""

from rebase_api.main import create_app

GONE_PREFIXES = (
    "/api/hub/matches",
    "/api/hub/contract-documents",
    "/api/hub/documenso",
)
GONE_SUFFIXES = ("/fiscal", "/matches", "/matches/prefill", "/matches/check", "/matches/preview")


def test_no_route_of_the_old_match_side_is_left() -> None:
    paths = set(create_app().openapi()["paths"])
    left = sorted(
        path for path in paths if path.startswith(GONE_PREFIXES) or path.endswith(GONE_SUFFIXES)
    )
    assert left == []


def test_the_handoff_to_the_crm_is_the_one_route_that_stayed() -> None:
    paths = create_app().openapi()["paths"]
    assert "post" in paths["/api/hub/freelancers/{freelancer_id}/engagement-handoff"]
