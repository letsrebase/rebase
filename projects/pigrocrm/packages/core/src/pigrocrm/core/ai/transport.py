"""The one seam onto a language model (design 2026-11-09, incoming documents inbox, § 3.2).

PigroCRM had no model client before this: its seam to the network is `http.HttpCall`, which Gmail,
Drive and Slack share and which tests replace, so the model's is built on it and no SDK is added.
`ModelCall` is the protocol the gateway and the reader depend on; `HttpModelCall` is the one
real implementation (Anthropic's Messages API and OpenAI's Chat Completions, at their own fixed
addresses: the host is never a setting, so a key cannot be sent anywhere an administrator
types), and a fake of either layer is what the tests pass, never a live key.

A call never raises anything but `ModelUnavailable`, carrying the one Italian sentence a page
can show as it stands: a provider's own error carries no Italian and can quote what it was
sent (a document's text), so neither its body nor its text is read into an exception, a log
or a return value. A request is retried only for a 429, which the provider answers before it
runs the model; a 5xx or a lost connection may have been paid for, and is not asked twice.
"""

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pigrocrm.core.errors import Conflict
from pigrocrm.core.http import (
    NETWORK_ERROR_STATUS,
    RATE_LIMITED_ONLY,
    HttpCall,
    Response,
    SleepFn,
    call_with_retry,
)

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
RESPONSE_MAX_BYTES = 1024 * 1024
CALL_TIMEOUT_SECONDS = 60

IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})
PDF_TYPE = "application/pdf"


class ModelUnavailable(Conflict):
    """The model did not answer with a response: an outage, a refused key, a body that is not
    one. It carries one sentence and nothing the provider said. `maybe_billed` is whether the
    provider may have run the model anyway (a timeout, a server error, a 200 whose body is not
    a response): the gateway then keeps the reservation as spent instead of releasing it."""

    def __init__(self, sentence: str, *, maybe_billed: bool = False) -> None:
        super().__init__("ai_model", sentence)
        self.maybe_billed = maybe_billed


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file the model reads as it is: a PDF with no text layer, or an image."""

    media_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    provider: str
    model: str
    # Kept out of `repr`: a traceback or a failed assertion prints a request, and neither the
    # key nor the bytes of a file belong in one.
    api_key: str = field(repr=False)
    system: str
    text: str
    attachments: Sequence[Attachment] = field(repr=False)
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    text: str
    input_tokens: int
    output_tokens: int


class ModelCall(Protocol):
    def __call__(self, request: ModelRequest) -> ModelAnswer: ...


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _anthropic_body(request: ModelRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for attachment in request.attachments:
        kind = "document" if attachment.media_type == PDF_TYPE else "image"
        content.append(
            {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": attachment.media_type,
                    "data": _b64(attachment.data),
                },
            }
        )
    content.append({"type": "text", "text": request.text})
    return {
        "model": request.model,
        "max_tokens": request.max_output_tokens,
        "system": request.system,
        "messages": [{"role": "user", "content": content}],
    }


def _openai_body(request: ModelRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.text}]
    for attachment in request.attachments:
        data_url = f"data:{attachment.media_type};base64,{_b64(attachment.data)}"
        if attachment.media_type == PDF_TYPE:
            content.append(
                {"type": "file", "file": {"filename": "documento.pdf", "file_data": data_url}}
            )
        else:
            content.append({"type": "image_url", "image_url": {"url": data_url}})
    return {
        "model": request.model,
        "max_completion_tokens": request.max_output_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": content},
        ],
    }


def _refusal(status: int) -> ModelUnavailable:
    if status in (401, 403):
        return ModelUnavailable("il fornitore non accetta la chiave del modello")
    if status == 429:
        return ModelUnavailable(
            "il fornitore del modello ha limitato le richieste: riprova piu' tardi"
        )
    if status == NETWORK_ERROR_STATUS or status >= 500:
        return ModelUnavailable(
            "il fornitore del modello non risponde: riprova piu' tardi", maybe_billed=True
        )
    return ModelUnavailable("il fornitore del modello ha rifiutato la richiesta")


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The provider's address is a constant and a model call is never redirected: a redirect
    would carry the key's header to wherever it points."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _model_http(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    max_bytes: int | None = None,
) -> Response:
    """The real network: `urllib` with a timeout that fits a model reading a document, and no
    redirects (a 3xx is an answer that is not a response, like any other)."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    limit = None if max_bytes is None else max_bytes + 1
    try:
        with _OPENER.open(request, timeout=CALL_TIMEOUT_SECONDS) as response:
            payload = response.read() if limit is None else response.read(limit)
            return int(response.status), payload, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        error_body = exc.read() if limit is None else exc.read(limit)
        return int(exc.code), error_body, dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, OSError):
        return NETWORK_ERROR_STATUS, b"{}", {}


class HttpModelCall:
    """`ModelCall` over `HttpCall`. The network and the clock are the two things a test passes."""

    def __init__(self, http: HttpCall = _model_http, sleep: SleepFn = time.sleep) -> None:
        self.http = http
        self.sleep = sleep

    def __call__(self, request: ModelRequest) -> ModelAnswer:
        if request.provider == "anthropic":
            url = ANTHROPIC_URL
            headers = {
                "content-type": "application/json",
                "x-api-key": request.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            }
            body = _anthropic_body(request)
        elif request.provider == "openai":
            url = OPENAI_URL
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {request.api_key}",
            }
            body = _openai_body(request)
        else:
            raise ModelUnavailable("il fornitore del modello non e' tra quelli supportati")
        status, payload, _ = call_with_retry(
            self.http,
            self.sleep,
            "POST",
            url,
            headers,
            json.dumps(body).encode(),
            retryable=RATE_LIMITED_ONLY,
            max_bytes=RESPONSE_MAX_BYTES,
        )
        if status != 200:
            logger.warning("ai model: %s answered %s", request.provider, status)
            raise _refusal(status)
        return self._answer(request.provider, payload)

    @staticmethod
    def _answer(provider: str, payload: bytes) -> ModelAnswer:
        try:
            data = json.loads(payload)
            if provider == "anthropic":
                text = "".join(
                    block["text"] for block in data["content"] if block.get("type") == "text"
                )
                usage = data["usage"]
                return ModelAnswer(
                    text, _count(usage.get("input_tokens")), _count(usage.get("output_tokens"))
                )
            text = data["choices"][0]["message"]["content"]
            usage = data["usage"]
            if not isinstance(text, str):
                raise TypeError
            return ModelAnswer(
                text, _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            logger.warning("ai model: %s answered a body that is not a response", provider)
            raise ModelUnavailable(
                "il fornitore del modello ha risposto con qualcosa che non e' una risposta",
                maybe_billed=True,
            ) from exc
