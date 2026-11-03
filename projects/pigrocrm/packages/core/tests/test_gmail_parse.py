"""Gmail's `format=full` JSON, read the way this slice needs to read it.

Every test here is a claim about a shape Gmail really produces -- unpadded base64url,
`multipart/alternative` for text+HTML, an attachment whose bytes live behind an
`attachmentId` -- because those are the shapes a parser written against a tidy example
gets wrong, silently, on somebody's real mailbox.
"""

import base64
from datetime import UTC, datetime
from typing import Any

from pigrocrm.core.gmail.parse import BODY_TRUNCATION_MARKER, header_addresses, parse_message


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "snippet": "Ciao",
        "internalDate": "1723766400000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Ada Byron <ada@acme.it>"},
                {"name": "To", "value": "io@example.it"},
                {"name": "Subject", "value": "Offerta"},
                {"name": "Message-ID", "value": "<abc@acme.it>"},
            ],
            "parts": [],
            "body": {"data": _b64("Ciao, però è già così."), "size": 24},
        },
    }
    return {**base, **overrides}


def test_it_reads_the_headers_the_slice_actually_uses() -> None:
    parsed = parse_message(_payload(), body_max_bytes=262_144, store_bodies=True)
    assert parsed.gmail_message_id == "m1"
    assert parsed.gmail_thread_id == "t1"
    assert parsed.from_address == "ada@acme.it"
    assert parsed.to_addresses == ["io@example.it"]
    assert parsed.subject == "Offerta"
    assert parsed.message_id_header == "<abc@acme.it>"
    assert parsed.internal_date == datetime(2024, 8, 16, 0, 0, tzinfo=UTC)


def test_the_display_name_is_dropped_and_only_the_address_is_kept() -> None:
    """`From: Ada Byron <ada@acme.it>` must yield the address alone: it is compared
    against the roster and against the connected mailbox, and a comparison against
    `"Ada Byron <ada@acme.it>"` matches nothing at all."""
    parsed = parse_message(_payload(), body_max_bytes=262_144, store_bodies=True)
    assert parsed.from_address == "ada@acme.it"
    assert "Ada" not in parsed.from_address


def test_accented_and_typographic_text_survives_base64url_decoding() -> None:
    parsed = parse_message(_payload(), body_max_bytes=262_144, store_bodies=True)
    assert parsed.body_text == "Ciao, però è già così."


def test_base64url_without_padding_still_decodes() -> None:
    """Gmail strips the `=` padding. A decoder that requires it fails on roughly two
    messages in three."""
    unpadded = _b64("x" * 10)
    assert not unpadded.endswith("=")
    payload = _payload()
    payload["payload"]["body"] = {"data": unpadded, "size": 10}
    assert parse_message(payload, body_max_bytes=262_144, store_bodies=True).body_text == "x" * 10


def test_an_html_only_message_stores_the_text_conversion_and_flags_it() -> None:
    """Email HTML carries tracking pixels, remote CSS and script. Rendering it would
    make the CRM a beacon and an XSS surface. For the original there is 'open in
    Gmail'."""
    payload = _payload()
    payload["payload"] = {
        "mimeType": "text/html",
        "headers": [{"name": "From", "value": "ada@acme.it"}, {"name": "Subject", "value": "X"}],
        "parts": [],
        "body": {"data": _b64("<p>Ciao <b>Ada</b></p><script>evil()</script>"), "size": 44},
    }
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.body_html_discarded is True
    assert "<script>" not in parsed.body_text
    assert "evil()" not in parsed.body_text
    assert "Ciao Ada" in parsed.body_text


def test_a_multipart_alternative_prefers_the_plain_part_and_flags_nothing() -> None:
    payload = _payload()
    payload["payload"] = {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "From", "value": "ada@acme.it"}],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("testo semplice"), "size": 14}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>testo</p>"), "size": 12}},
        ],
        "body": {"size": 0},
    }
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.body_text == "testo semplice"
    assert parsed.body_html_discarded is False


def test_a_nested_multipart_still_yields_its_plain_part() -> None:
    """`multipart/mixed` containing a `multipart/alternative` containing the bodies is
    the ordinary shape of a message with an attachment. A parser that looks only one
    level down stores an empty body for every one of them."""
    payload = _payload()
    payload["payload"] = {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "From", "value": "ada@acme.it"}],
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("annidato"), "size": 8}},
                    {"mimeType": "text/html", "body": {"data": _b64("<p>x</p>"), "size": 8}},
                ],
                "body": {"size": 0},
            },
            {
                "mimeType": "application/pdf",
                "filename": "a.pdf",
                "body": {"attachmentId": "att-1", "size": 12},
            },
        ],
        "body": {"size": 0},
    }
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.body_text == "annidato"
    assert [a.filename for a in parsed.attachments] == ["a.pdf"]


def test_a_long_body_is_truncated_with_a_marker_not_silently_cut() -> None:
    payload = _payload()
    payload["payload"]["body"] = {"data": _b64("a" * 5_000), "size": 5_000}
    parsed = parse_message(payload, body_max_bytes=1_000, store_bodies=True)
    assert parsed.body_truncated is True
    assert parsed.body_text.endswith(BODY_TRUNCATION_MARKER)
    assert len(parsed.body_text.encode()) <= 1_000 + len(BODY_TRUNCATION_MARKER.encode())


def test_a_body_at_the_limit_is_not_marked_truncated() -> None:
    """The other side of the boundary: a parser that flagged every body as truncated
    would pass the test above and lie about every message in the mailbox."""
    payload = _payload()
    payload["payload"]["body"] = {"data": _b64("a" * 1_000), "size": 1_000}
    parsed = parse_message(payload, body_max_bytes=1_000, store_bodies=True)
    assert parsed.body_truncated is False
    assert parsed.body_text == "a" * 1_000


def test_truncation_never_splits_a_multibyte_character() -> None:
    payload = _payload()
    payload["payload"]["body"] = {"data": _b64("è" * 2_000), "size": 4_000}
    parsed = parse_message(payload, body_max_bytes=1_001, store_bodies=True)
    # A byte-slice at an odd offset inside a two-byte character would raise on decode,
    # or worse, store a replacement character. Neither is acceptable in stored mail.
    assert "�" not in parsed.body_text
    assert parsed.body_text.replace(BODY_TRUNCATION_MARKER, "") == "è" * 500


def test_with_bodies_off_only_the_snippet_survives() -> None:
    parsed = parse_message(_payload(), body_max_bytes=262_144, store_bodies=False)
    assert parsed.body_text == ""
    assert parsed.snippet == "Ciao"


def test_attachments_keep_their_metadata_and_none_of_their_bytes() -> None:
    payload = _payload()
    payload["payload"] = {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "From", "value": "ada@acme.it"}],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("vedi allegato"), "size": 13}},
            {
                "mimeType": "application/pdf",
                "filename": "offerta.pdf",
                "body": {"attachmentId": "att-1", "size": 91_234},
            },
        ],
        "body": {"size": 0},
    }
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert [(a.filename, a.mime, a.size) for a in parsed.attachments] == [
        ("offerta.pdf", "application/pdf", 91_234)
    ]
    # Not one byte: those would be gigabytes in slice 2's storage. The attachment that
    # matters is saved by an explicit action, through DocumentStorage.
    assert "attachmentId" not in str(parsed.attachments)
    assert "att-1" not in str(parsed.attachments)
    # And the attachment part is not mistaken for the body.
    assert parsed.body_text == "vedi allegato"


def test_header_addresses_splits_display_names_and_lowercases() -> None:
    assert header_addresses('Ada Byron <Ada@Acme.IT>, "Rossi, Bob" <bob@acme.it>') == [
        "ada@acme.it",
        "bob@acme.it",
    ]


def test_header_addresses_deduplicates_rather_than_repeating_a_recipient() -> None:
    assert header_addresses("ada@acme.it, Ada <ADA@acme.it>") == ["ada@acme.it"]


def test_cc_is_read_as_well_as_to() -> None:
    """A colleague in copy is a participant of the conversation, and B1-9 files the
    message against every address it can see. Losing `Cc` loses those links."""
    payload = _payload()
    payload["payload"]["headers"] = [
        {"name": "From", "value": "ada@acme.it"},
        {"name": "To", "value": "io@example.it"},
        {"name": "Cc", "value": "bob@acme.it, carla@acme.it"},
    ]
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.cc_addresses == ["bob@acme.it", "carla@acme.it"]


def test_threading_headers_are_kept_verbatim() -> None:
    """`In-Reply-To` and `References` are what 5B-2 uses to attach a reply to the
    conversation it answers, and they are the *original* strings, angle brackets
    included, because that is how they are compared."""
    payload = _payload()
    payload["payload"]["headers"] = [
        {"name": "From", "value": "ada@acme.it"},
        {"name": "In-Reply-To", "value": "<uno@acme.it>"},
        {"name": "References", "value": "<uno@acme.it> <due@acme.it>"},
    ]
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.in_reply_to == "<uno@acme.it>"
    assert parsed.references == "<uno@acme.it> <due@acme.it>"


def test_a_message_with_no_headers_at_all_parses_rather_than_raising() -> None:
    """Gmail returns thin payloads for drafts and for some system messages. A parser
    that raises here stops the whole sync on one odd row."""
    parsed = parse_message(
        {"id": "m9", "threadId": "t9", "internalDate": "0", "payload": {"headers": []}},
        body_max_bytes=262_144,
        store_bodies=True,
    )
    assert parsed.from_address == ""
    assert parsed.subject == ""
    assert parsed.internal_date == datetime(1970, 1, 1, tzinfo=UTC)


def test_a_body_that_will_not_decode_is_stored_empty_not_mangled() -> None:
    """Undecodable bytes are rare and real -- a mislabelled charset, a truncated part.
    Storing `U+FFFD` soup would put unreadable text in front of the user and would
    also make the truncation byte count meaningless."""
    payload = _payload()
    payload["payload"]["body"] = {
        "data": base64.urlsafe_b64encode(b"\xff\xfe\xfa").decode().rstrip("="),
        "size": 3,
    }
    parsed = parse_message(payload, body_max_bytes=262_144, store_bodies=True)
    assert parsed.body_text == ""
    assert parsed.body_truncated is False
