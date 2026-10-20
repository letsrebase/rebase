"""`GET /api/hub/referral-rates`: the two live referral rates, for the public site.

The community site quotes the rates a referral earns (REB-645, Lorenzo's answer 2, option
B) and may not type them: a percentage shown anywhere is read from the admin-edited
`referral_settings` row, and a rate that cannot be read is dropped, never guessed
(`docs/design/DECISIONS.md`, 2026-09-26 and 2026-09-29). This is the one door the site has
to that row; the member area keeps reading it through `GET /api/hub/me/referral`.

Public, deliberately, like the other GETs of this hub the site opens (`/team/proposals/{id}`):
no cookie, no token. Read-only in the strict sense, too: it never inserts the row the
member area seeds on first need (`ReferralService.public_rates`). No speed bump
(`spend_one`): the bucket is the signups' and the wizards', so a page that reads the rates
on every view would spend a visitor's five-a-minute before they submit the form; the read
is one row by `created_at`, nothing mailed and nothing written, and a real cap belongs at
nginx's `limit_req` as `ratelimit.py` already says.
"""

from fastapi import APIRouter, HTTPException, status

from rebase_api.deps import SessionDep
from rebase_core.referral_schemas import PublicReferralRates
from rebase_core.referrals import ReferralService

router = APIRouter(prefix="/api/hub", tags=["hub"])

UNREADABLE = "Le percentuali non sono disponibili in questo momento."


@router.get(
    "/referral-rates",
    response_model=PublicReferralRates,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": (
                "The settings row is missing: the body is the Italian sentence in "
                "`detail`, the answer is `Cache-Control: no-store`, and the page drops the "
                "figure instead of showing a default."
            ),
        },
    },
)
def referral_rates(session: SessionDep) -> PublicReferralRates:
    """The percentage of rebase's margin a referral earns, for a freelancer and for a
    company, as the admin's settings hold them now, read on every call.

    **200**: `{"rate_freelancer": "0.1000", "rate_company": "0.3000"}`. Both are the
    fraction of one as a decimal string with four places, the shape the member area's
    `rate_freelancer` and `rate_company` already carry (`"0.1000"` is 10%). Nothing else is
    in the body, by schema: no margin, no compenso, no euro figure, no code, no counts.

    **503**: the settings row is missing (a deploy never leaves that: migration 0024
    seeds it). The body is the Italian sentence `{"detail": ...}`, the answer carries
    `Cache-Control: no-store`, and a page treats it like any failed fetch: it drops the
    figure it was going to quote. It never falls back to a typed 10% or 30%. A database
    that does not answer is the framework's own 500, which a page drops the same way. A
    200 is never partial, so a client reads either both rates or neither."""
    rates = ReferralService(session).public_rates()
    if rates is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, UNREADABLE, headers={"Cache-Control": "no-store"}
        )
    return rates
