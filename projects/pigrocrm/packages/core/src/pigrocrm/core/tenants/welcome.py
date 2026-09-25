"""The welcome step after a space is provisioned: a magic link and the mail around it
(spec 2026-09-25 § 2.3 step 3), shared by the signup route and Milestone A's engagements
door.

Only what to send: the caller still decides how to answer its own request -- the access
cookie and the `Location` header are the router's, and whether the mail goes in the
background or at once is the caller's call too.
"""

from sqlalchemy.orm import Session

from pigrocrm.core.auth.magic_link import MagicLinkService
from pigrocrm.core.config import Settings
from pigrocrm.core.mail import EmailSender, Mail, welcome_mail


def welcome_link(space: Session, settings: Settings, owner_email: str, slug: str) -> str | None:
    """The magic link that enters a freshly provisioned space, or None when this
    installation has no public origin: the signup route and the engagements door both
    send `welcome_mail` with it (spec 2026-09-25 § 2.3 step 3)."""
    origin = settings.public_url.strip().rstrip("/")
    if not origin:
        return None
    raw = MagicLinkService(space, settings).request(owner_email)
    if not raw:
        return None
    return f"{origin}/{slug}/app/verify?t={raw}"


def welcome(
    space: Session,
    settings: Settings,
    sender: EmailSender | None,
    owner_email: str,
    slug: str,
    *,
    membro: bool,
) -> Mail | None:
    """The welcome mail ready to send, or None when nothing can be sent (no sender, no
    origin): the caller decides whether to send it in the background or at once."""
    if sender is None:
        return None
    link = welcome_link(space, settings, owner_email, slug)
    if link is None:
        return None
    origin = settings.public_url.strip().rstrip("/")
    return welcome_mail(owner_email, link, f"{origin}/{slug}/app/login", membro=membro)
