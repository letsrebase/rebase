"""What makes a bare sign-up not a lead (REB-800).

A sign-up is a lead until its address has a card (`_card_emails`, written three times in
this package). Since LinkedIn is the second identity, a sign-up whose LinkedIn profile is
on somebody's card is that person under another address, so it is no lead either: the
person is listed once, as the card, and a campaign to leads does not mail them twice.
The row itself stays on the list of sign-ups; it is only left out of the lead lists.
"""

from sqlalchemy import ColumnElement, exists, func, select

from rebase_core.linkedin import LINKEDIN_PROFILE
from rebase_core.models import Freelancer, Signup, User


def card_by_linkedin() -> ColumnElement[bool]:
    """`EXISTS` a card whose person holds the LinkedIn profile of the enclosing query's
    `Signup`: use it as `~card_by_linkedin()` beside the address anti-join. Compared on
    `lower(linkedin_url)`, the expression both unique indexes are on, and only for a personal
    profile (`linkedin_key`)."""
    return exists(
        select(User.id)
        .join(Freelancer, Freelancer.user_id == User.id)
        .where(
            Signup.linkedin_url.like(f"{LINKEDIN_PROFILE}%"),
            func.lower(User.linkedin_url) == func.lower(Signup.linkedin_url),
        )
    )
