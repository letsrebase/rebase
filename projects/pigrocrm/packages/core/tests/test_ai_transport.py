"""REB-972: the model's transport over a fake `HttpCall`, never a provider. What is proved: the
request each provider gets (the key in the header it expects, the file as the provider takes it,
the host a constant), the answer read back, and that a failure becomes one sentence that carries
nothing the provider said."""

import json
import logging

import pytest

from pigrocrm.core.ai.transport import (
    ANTHROPIC_URL,
    OPENAI_URL,
    Attachment,
    HttpModelCall,
    ModelRequest,
    ModelUnavailable,
)
from pigrocrm.core.http import NETWORK_ERROR_STATUS, Response

KEY = "placeholder-key-do-not-leak-01"


class FakeHttp:
    def __init__(self, *answers: Response) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict[str, str], dict[str, object]]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Response:
        assert body is not None
        self.calls.append((method, url, headers, json.loads(body)))
        return self.answers.pop(0)


def _request(provider: str, *attachments: Attachment) -> ModelRequest:
    return ModelRequest(
        provider=provider,
        model="m-1",
        api_key=KEY,
        system="read it",
        text="the document",
        attachments=attachments,
        max_output_tokens=100,
    )


def _anthropic(text: str = "{}", input_tokens: int = 7, output_tokens: int = 3) -> Response:
    body = {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return 200, json.dumps(body).encode(), {}


def test_anthropic_gets_its_key_header_its_fixed_host_and_a_pdf_as_a_document() -> None:
    http = FakeHttp(_anthropic("hello"))
    answer = HttpModelCall(http, lambda _: None)(
        _request("anthropic", Attachment("application/pdf", b"%PDF-1"))
    )
    assert (answer.text, answer.input_tokens, answer.output_tokens) == ("hello", 7, 3)
    ((method, url, headers, body),) = http.calls
    assert (method, url) == ("POST", ANTHROPIC_URL)
    assert headers["x-api-key"] == KEY and "authorization" not in headers
    content = body["messages"][0]["content"]  # type: ignore[index]
    assert content[0]["type"] == "document" and content[1] == {
        "type": "text",
        "text": "the document",
    }
    assert body["max_tokens"] == 100 and body["system"] == "read it"


def test_openai_gets_a_bearer_token_json_mode_and_an_image_as_a_data_url() -> None:
    reply = {
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    http = FakeHttp((200, json.dumps(reply).encode(), {}))
    answer = HttpModelCall(http, lambda _: None)(
        _request("openai", Attachment("image/png", b"\x89PNG"))
    )
    assert (answer.text, answer.input_tokens, answer.output_tokens) == ("hi", 5, 2)
    ((_, url, headers, body),) = http.calls
    assert url == OPENAI_URL and headers["authorization"] == f"Bearer {KEY}"
    assert body["response_format"] == {"type": "json_object"}
    parts = body["messages"][1]["content"]  # type: ignore[index]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_only_a_rate_limit_is_asked_again_and_the_sleep_is_not_real() -> None:
    sleeps: list[float] = []
    http = FakeHttp((429, b"{}", {"retry-after": "2"}), _anthropic("ok"))
    answer = HttpModelCall(http, sleeps.append)(_request("anthropic"))
    assert answer.text == "ok" and len(http.calls) == 2 and sleeps == [2.0]

    # A server error may have been paid for: it is not asked twice.
    http = FakeHttp((500, b"{}", {}), _anthropic("never"))
    with pytest.raises(ModelUnavailable):
        HttpModelCall(http, sleeps.append)(_request("anthropic"))
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("status", "sentence"),
    [
        (401, "non accetta la chiave"),
        (403, "non accetta la chiave"),
        (429, "ha limitato"),
        (500, "non risponde"),
        (NETWORK_ERROR_STATUS, "non risponde"),
        (400, "ha rifiutato"),
    ],
)
def test_a_failure_is_one_sentence_and_nothing_the_provider_said(
    status: int, sentence: str, caplog: pytest.LogCaptureFixture
) -> None:
    secret_body = json.dumps({"error": {"message": f"your document says: {KEY} and Totale 48"}})
    http = FakeHttp(
        (status, secret_body.encode(), {}),
        (status, secret_body.encode(), {}),
        (status, secret_body.encode(), {}),
        (status, secret_body.encode(), {}),
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelUnavailable) as raised:
        HttpModelCall(http, lambda _: None)(_request("anthropic"))
    assert sentence in raised.value.message
    assert "Totale 48" not in raised.value.message and KEY not in raised.value.message
    assert "Totale 48" not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("payload", [b"not json", b"{}", b"[]"])
def test_a_body_that_is_not_a_response_is_unavailable_not_a_crash(payload: bytes) -> None:
    http = FakeHttp((200, payload, {}))
    with pytest.raises(ModelUnavailable):
        HttpModelCall(http, lambda _: None)(_request("anthropic"))


def test_a_provider_that_is_not_supported_is_refused_before_any_network() -> None:
    http = FakeHttp()
    with pytest.raises(ModelUnavailable):
        HttpModelCall(http, lambda _: None)(_request("somewhere-else"))
    assert http.calls == []


def test_the_real_network_never_follows_a_redirect_and_so_never_carries_the_key_elsewhere() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from pigrocrm.core.ai.transport import _model_http

    followed: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path == "/elsewhere":
                followed.append(self.headers.get("x-api-key", ""))
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(307)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        status, _, _ = _model_http(
            "POST",
            f"http://127.0.0.1:{server.server_port}/start",
            {"x-api-key": KEY},
            b"{}",
        )
    finally:
        thread.join(timeout=5)
        server.server_close()
    assert status == 307 and followed == []


def test_a_request_never_prints_the_key_or_the_bytes_it_carries() -> None:
    request = _request("anthropic", Attachment("image/png", b"SECRET-BYTES"))
    assert KEY not in repr(request) and "SECRET-BYTES" not in repr(request)
