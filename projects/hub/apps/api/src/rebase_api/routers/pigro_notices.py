"""What PigroCRM tells the hub about its engagements, and asks it (REB-1106, collaborators
design § 5): the notices that keep the hub's read model of them current, and who referred a
freelancer or a company, for the letters.

Both routes are PigroCRM's server's and nobody else's, under one bearer
(`REBASE_PIGRO_NOTICES_TOKEN`, `PIGROCRM_HUB_NOTICES_TOKEN` there): not the engagements token,
which opens the CRM, and not the registry's. Without the token configured neither route exists
(404), so nothing says there is a door; with it, a missing or wrong bearer is a 401. The `detail`
strings are English codes (`not_found`, `invalid_token`) because their only reader is the
caller's log.

`POST /engagement-notices` is idempotent: a notice the hub already holds, or an older one, is
answered `applied: false` with a 200, which the CRM counts as delivered. `POST /referrer-name`
answers a name only for a verified referral (`ReferralService.referrer_name`, REB-664), and the
ids travel in the body, never the URL.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.engagement_notices import EngagementNoticeService
from rebase_core.engagement_schemas import (
    EngagementNotice,
    NoticeAck,
    ReferrerAnswer,
    ReferrerQuestion,
)
from rebase_core.referrals import ReferralService

router = APIRouter(prefix="/api/hub", tags=["hub-pigro-notices"])


def verify_notices_token(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    if not settings.pigro_notices_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    # Bytes, not str: Starlette decodes headers as latin-1 and `compare_digest` refuses a
    # `str` with a non-ASCII character, which would turn a stray byte into a 500.
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), settings.pigro_notices_token.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")


@router.post(
    "/engagement-notices",
    response_model=NoticeAck,
    dependencies=[Depends(verify_notices_token)],
)
def engagement_notice(notice: EngagementNotice, session: SessionDep) -> NoticeAck:
    """Upserts the hub's read model of one engagement from the CRM's notice, and writes the
    referral reward its signature earns in the same transaction."""
    return NoticeAck(applied=EngagementNoticeService(session).apply(notice))


@router.post(
    "/referrer-name",
    response_model=ReferrerAnswer,
    dependencies=[Depends(verify_notices_token)],
)
def referrer_name(question: ReferrerQuestion, session: SessionDep) -> ReferrerAnswer:
    """Who referred the freelancer and the company a letter is about, each `null` unless the
    referral is verified."""
    referrals = ReferralService(session)
    return ReferrerAnswer(
        freelancer=(
            referrals.referrer_name("freelancer", question.hub_freelancer_id)
            if question.hub_freelancer_id is not None
            else None
        ),
        company=(
            referrals.referrer_name("company", question.hub_company_id)
            if question.hub_company_id is not None
            else None
        ),
    )
