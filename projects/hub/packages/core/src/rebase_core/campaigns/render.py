"""A campaign mail (spec § 5.1): the admin's text, the tracked button, the signature, the
unsubscribe line, in the hub's own frame. Deterministic on purpose: a retry must send
Resend the same bytes under the same Idempotency-Key."""

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from rebase_core.campaigns.states import ENTITY, PIGRO_LATER
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.mail import INK_QUIET, Mail, _button, _frame, _quiet_link
from rebase_core.models import Campaign

_PARA = 'style="margin:0 0 24px 0;"'
_AFTER = 'style="margin:24px 0 0 0;"'
_FINE = (
    f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    'word-break:break-all;"'
)
FALLBACK = "Se il bottone non si apre, copia questo indirizzo nel browser:"
UNSUBSCRIBE_LINE = "Non vuoi più ricevere queste mail?"


@dataclass(frozen=True)
class RenderTarget:
    email: str
    nome: str | None
    codice: str
    token: str
    recipient_id: str | None = None


@dataclass(frozen=True)
class RenderedMail:
    mail: Mail
    headers: dict[str, str]
    tags: dict[str, str]


def person_code(email: str) -> str:
    return hashlib.sha1(email.lower().encode()).hexdigest()[:8]


def personalise(testo: str, nome: str | None) -> str:
    if nome:
        return testo.replace("{nome}", nome)
    return testo.replace(" {nome}", "").replace("{nome}", "")


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def destination(campaign: Campaign, settings: Settings) -> str:
    base = settings.hub_url.rstrip("/")
    if campaign.bottone_meta in ("area", "richiesta"):
        return f"{base}/login"
    if campaign.bottone_meta == "wizard":
        return f"{base}/freelance"
    raise ValidationFailed(ENTITY, "bottone_meta", PIGRO_LATER)


def _tracked(url: str, campaign: Campaign, codice: str) -> str:
    query = urlencode(
        {
            "utm_source": "email",
            "utm_medium": "campagna",
            "utm_campaign": campaign.slug,
            "utm_content": campaign.azione,
            "utm_term": codice,
        }
    )
    return f"{url}?{query}"


def unsubscribe_urls(token: str, settings: Settings) -> tuple[str, str]:
    parts = urlsplit(settings.hub_url)
    page = f"{settings.hub_url.rstrip('/')}/disiscrizione?t={token}"
    api = f"{parts.scheme}://{parts.netloc}/api/hub/campagne/disiscrizione?t={token}"
    return page, api


def render(
    campaign: Campaign, target: RenderTarget, settings: Settings, *, test: bool = False
) -> RenderedMail:
    paragraphs = _paragraphs(personalise(campaign.testo, target.nome))
    url = _tracked(destination(campaign, settings), campaign, target.codice)
    page, api = unsubscribe_urls(target.token, settings)
    text = "\n\n".join(
        (
            *paragraphs,
            f"{campaign.bottone_testo}: {url}",
            "Ivan\nrebase",
            f"{UNSUBSCRIBE_LINE} Disiscriviti: {page}",
        )
    )
    href = html.escape(url, quote=True)
    body = "\n".join(
        (
            *(f"<p {_PARA}>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs),
            _button(href, html.escape(campaign.bottone_testo)),
            f"<p {_FINE}>{FALLBACK}<br>{_quiet_link(href, href)}</p>",
            f"<p {_AFTER}>Ivan<br>rebase</p>",
            f"<p {_FINE}>{UNSUBSCRIBE_LINE} "
            f"{_quiet_link(html.escape(page, quote=True), 'Disiscriviti')}</p>",
        )
    )
    mail = Mail(
        to=target.email,
        subject=("[prova] " if test else "") + campaign.oggetto,
        text=text + "\n",
        html=_frame(campaign.oggetto, body),
    )
    headers = {
        "List-Unsubscribe": f"<{api}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    tags = {
        "campaign": campaign.slug,
        "azione": campaign.azione,
        "kind": "test" if test else "real",
    }
    if target.recipient_id is not None:
        tags["r"] = target.recipient_id
    return RenderedMail(mail, headers, tags)
