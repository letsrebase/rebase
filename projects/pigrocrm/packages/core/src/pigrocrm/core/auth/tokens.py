from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed

ALGORITHM = "HS256"
# "identity" (design 2026-09-23, REB-376): a fourth kind of claim, signed with this
# same secret, that decode_token already refuses to accept in place of an "access" or
# "refresh" token and vice versa -- the closed Literal is what makes that refusal
# structural rather than a convention a caller could forget.
TokenType = Literal["access", "refresh", "identity"]


class TokenPayload(BaseModel):
    sub: UUID
    role: str | None
    type: TokenType
    exp: datetime
    # Only refresh tokens carry one -- see issue_refresh_token. None for access tokens.
    jti: UUID | None = None
    # The space an access token is good for (`ROOT_AUDIENCE` for the root); None for the
    # other kinds.
    aud: str | None = None


# The audience of an access token minted for the root installation. Not a valid slug
# (`SLUG_PATTERN` is lowercase letters, digits and hyphens), so no space can wear it.
ROOT_AUDIENCE = "@root"


def _issue(
    user_id: UUID,
    role: str | None,
    token_type: TokenType,
    delta: timedelta,
    settings: Settings,
    *,
    jti: UUID | None = None,
    issued_at: datetime | None = None,
    audience: str | None = None,
) -> str:
    now = issued_at if issued_at is not None else datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + delta).timestamp()),
    }
    if jti is not None:
        claims["jti"] = str(jti)
    if audience is not None:
        claims["aud"] = audience
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def issue_access_token(
    user_id: UUID,
    role: str,
    settings: Settings,
    *,
    space: str | None = None,
    issued_at: datetime | None = None,
) -> str:
    """`issued_at` is what lets the same access token be signed twice and come out
    identical. HS256 over identical claims with the same key is deterministic, so the
    only thing that would otherwise differ between two signings a few seconds apart is
    `iat` (and the `exp` derived from it) -- which is exactly what the refresh grace
    window needs to pin down: two tabs presenting the same refresh cookie within ten
    seconds get the *same* pair back, not two pairs (see
    `RefreshTokenService.rotate`). Left None -- every other caller -- this reads the
    clock as before.

    `space` is the slug of the space the token is for, `None` for the root: it travels as
    the `aud` claim and `get_actor` refuses a token minted for another space, so the one
    HS256 secret shared by the root and every space no longer makes a token portable
    between them."""
    return _issue(
        user_id,
        role,
        "access",
        timedelta(minutes=settings.access_token_minutes),
        settings,
        issued_at=issued_at,
        audience=space if space is not None else ROOT_AUDIENCE,
    )


def issue_refresh_token(
    user_id: UUID,
    settings: Settings,
    *,
    jti: UUID | None = None,
    issued_at: datetime | None = None,
) -> str:
    """`jti` identifies this exact token: two refresh tokens issued in the same second
    would otherwise carry identical `iat`/`exp` claims, and HS256 over identical claims
    with the same key is deterministic -- byte-for-byte the same token, which defeats
    rotation entirely. A fresh random `jti` is generated whenever the caller does not
    supply one, so two tokens can never collide; `RefreshTokenService.issue` supplies
    its own so the same value can be persisted for later revocation/consumption.

    `issued_at`, like `issue_access_token`'s, makes the signature reproducible: the same
    `jti` re-signed at the same instant is the same token, byte for byte, which is what
    `RefreshTokenService.rotate` returns to a second tab inside the grace window
    instead of minting a second successor."""
    return _issue(
        user_id,
        None,
        "refresh",
        timedelta(days=settings.refresh_token_days),
        settings,
        jti=jti if jti is not None else uuid4(),
        issued_at=issued_at,
    )


def issue_identity_token(
    identity_id: UUID,
    settings: Settings,
    *,
    jti: UUID | None = None,
    issued_at: datetime | None = None,
) -> str:
    """Carries no role: an identity is never a role holder, only a proven address
    (design 2026-09-23 §1) -- `_issue`'s `role` claim is always `None` here, unlike
    the two token kinds above. `jti` points at the `IdentitySession` row that makes
    this token revocable (§2); a fresh one is minted when the caller does not supply
    one, the same discipline `issue_refresh_token` uses and for the same reason."""
    return _issue(
        identity_id,
        None,
        "identity",
        timedelta(days=settings.identity_token_days),
        settings,
        jti=jti if jti is not None else uuid4(),
        issued_at=issued_at,
    )


def decode_token(
    token: str, settings: Settings, *, expected_type: TokenType, audience: str | None = None
) -> TokenPayload:
    """`audience`, when given, is the one space (or `ROOT_AUDIENCE`) the caller serves: a
    token that names another, or none, is refused. Checked by hand because PyJWT's own
    `audience` check would also refuse every token that carries an `aud` when the caller
    passes none, which refresh and identity tokens, and the callers that do not care
    which space, rely on."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"verify_aud": False}
        )
    except jwt.PyJWTError as exc:
        raise ValidationFailed("session", "token", "token non valido o scaduto") from exc

    if claims.get("type") != expected_type:
        raise ValidationFailed("session", "token", "tipo di token errato", expected=expected_type)
    if audience is not None and claims.get("aud") != audience:
        raise ValidationFailed("session", "token", "token di un altro spazio")
    # A signature can be valid while the payload is still missing or malformed --
    # e.g. no "sub", no "exp", or a "sub" that is not a UUID. Building TokenPayload
    # from claims must never let a raw KeyError/ValueError escape: this function's
    # contract is "return a TokenPayload or raise a domain error," never a bare
    # stdlib exception.
    try:
        raw_jti = claims.get("jti")
        return TokenPayload(
            sub=UUID(claims["sub"]),
            role=claims.get("role"),
            type=claims["type"],
            exp=datetime.fromtimestamp(claims["exp"], tz=UTC),
            jti=UUID(raw_jti) if raw_jti is not None else None,
            aud=claims.get("aud"),
        )
    except (KeyError, ValueError) as exc:
        raise ValidationFailed("session", "token", "token malformato") from exc
