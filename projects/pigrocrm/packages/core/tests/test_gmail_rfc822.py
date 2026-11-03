"""The three defects of the previous system's `buildRawEmailMessage`, none of them repeated.

The last test is the one that matters most: the message this builder produces is fed to
`FakeGmail` exactly as `users.messages.send` would receive it, read back in Gmail's own
`format=full` shape, and run through `parse.parse_message` -- the parser this slice
already ships. Either side alone can be self-consistently wrong; the round trip cannot.
"""

import base64
import json
import re
from email import message_from_bytes
from email.message import Message
from email.policy import default as default_policy

import pytest
from fakes.fake_gmail import FakeGmail

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail.parse import parse_message
from pigrocrm.core.gmail.query import GMAIL_API_ROOT, message_get_url
from pigrocrm.core.gmail.rfc822 import (
    OutgoingAttachment,
    build_rfc822,
    new_message_id,
    to_base64url,
)

# Accents, typographic quotes, an em dash and an emoji. The previous system declares 7bit over text
# like this; it works by accident until the first mail client that takes the
# declaration literally.
TRICKY = "Però è già così — l’offerta “definitiva” costa 1.200 € 🎉"


def _parsed(raw: bytes) -> Message:
    return message_from_bytes(raw, policy=default_policy)


def _body(message: Message) -> str:
    """The body as the recipient's client will show it.

    The line endings are CRLF and that is correct rather than incidental: this byte
    string goes straight into `users.messages.send`, so it is wire format (RFC 5322
    2.1), not a local file. Normalising here rather than building with `\\n` keeps the
    test honest about which of the two the builder produces.
    """
    content = message.get_content()
    assert isinstance(content, str)
    return content.replace("\r\n", "\n").rstrip("\n")


def test_the_body_round_trips_character_for_character() -> None:
    """Spec 13, criterion 12."""
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject=TRICKY,
        body_text=TRICKY,
        message_id="<a.1@crm.example.it>",
    )
    message = _parsed(raw)
    assert _body(message) == TRICKY
    assert message["Subject"] == TRICKY


def test_a_long_unbroken_line_survives_the_encoding() -> None:
    """RFC 5322 caps a line at 998 octets, so a paragraph typed without a newline has to
    be soft-wrapped by the transfer encoding and unwrapped on the way out. Getting this
    wrong does not raise -- it silently inserts newlines into somebody's text."""
    paragraph = "Buongiorno, " + "è un testo lunghissimo senza a capo. " * 80
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="x",
        body_text=paragraph,
        message_id="<a.long@crm.example.it>",
    )
    assert max(len(line) for line in raw.split(b"\r\n")) <= 998
    assert _body(_parsed(raw)) == paragraph


def test_no_branch_declares_seven_bit_over_non_ascii() -> None:
    """The live defect of the previous system, banned by name. Checked over every branch: plain,
    with a cc, and with an attachment."""
    for attachments in (
        (),
        (OutgoingAttachment("offerta.pdf", "application/pdf", b"%PDF-1.7\n"),),
    ):
        raw = build_rfc822(
            from_address="io@example.it",
            from_name="Io",
            to=["ada@acme.it"],
            cc=["bob@acme.it"],
            subject=TRICKY,
            body_text=TRICKY,
            message_id="<a.2@crm.example.it>",
            attachments=attachments,
        )
        text_parts = [part for part in _parsed(raw).walk() if part.get_content_maintype() == "text"]
        assert text_parts
        for part in text_parts:
            assert part["Content-Transfer-Encoding"] in {"quoted-printable", "base64"}
            assert part.get_content_charset() == "utf-8"
        assert b"7bit" not in raw


def test_an_ascii_body_is_still_not_declared_seven_bit() -> None:
    """The branch that hides the defect: a body that happens to be ASCII today lets a
    builder pick 7bit and look correct, and the Italian edit one keystroke later does not
    change the declaration. So the choice is unconditional."""
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Offerta",
        body_text="Buongiorno, in allegato.",
        message_id="<a.ascii@crm.example.it>",
    )
    assert _parsed(raw)["Content-Transfer-Encoding"] == "quoted-printable"
    assert b"7bit" not in raw


def test_a_message_id_is_always_present_and_is_the_one_we_chose() -> None:
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="x",
        body_text="y",
        message_id="<chosen.1@crm.example.it>",
    )
    assert _parsed(raw)["Message-ID"] == "<chosen.1@crm.example.it>"


def test_a_generated_message_id_is_unique_and_uses_the_configured_domain() -> None:
    ids = {new_message_id("crm.example.it") for _ in range(100)}
    assert len(ids) == 100
    for value in ids:
        assert re.fullmatch(r"<[0-9a-f]+\.\d+@crm\.example\.it>", value), value


def test_a_generated_message_id_is_one_the_lookup_can_actually_use() -> None:
    """The id exists to be handed to `rfc822msgid:` later. A shape that mints cleanly and
    then fails the query builder's own validation would be discovered in B2-6, on the
    error path, which is the worst place to discover it."""
    from pigrocrm.core.gmail.query import rfc822msgid_query

    minted = new_message_id("crm.example.it")
    assert rfc822msgid_query(minted) == f"rfc822msgid:{minted.strip('<>')}"


def test_a_domain_that_is_not_a_domain_is_refused() -> None:
    for hostile in ["", "not a domain", "crm.example.it>", "localhost"]:
        with pytest.raises(ValidationFailed):
            new_message_id(hostile)


def test_a_reply_carries_in_reply_to_and_references() -> None:
    """Spec 6.2 rule 2 and criterion 13. Without these, a reminder arrives detached:
    whoever receives it cannot see the invoice above it, and the first thing they do is
    ask for it to be resent."""
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Re: Fattura 2026/14",
        body_text="Sollecito",
        message_id="<reminder.1@crm.example.it>",
        in_reply_to="<original.1@crm.example.it>",
        references="<thread.root@acme.it> <original.1@crm.example.it>",
    )
    message = _parsed(raw)
    assert message["In-Reply-To"] == "<original.1@crm.example.it>"
    assert message["References"] == "<thread.root@acme.it> <original.1@crm.example.it>"


def test_a_message_that_is_not_a_reply_carries_neither_header() -> None:
    """An empty `In-Reply-To` is not the same as no `In-Reply-To`: a client reading the
    first threads a fresh message onto nothing."""
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Offerta",
        body_text="Testo",
        message_id="<fresh.1@crm.example.it>",
    )
    message = _parsed(raw)
    assert message["In-Reply-To"] is None
    assert message["References"] is None


def test_an_attachment_keeps_its_name_its_type_and_its_bytes() -> None:
    pdf = b"%PDF-1.7\n%\xc3\xa8\xc3\xa9 binary\n"
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Offerta",
        body_text="In allegato.",
        message_id="<a.3@crm.example.it>",
        attachments=[OutgoingAttachment("Offerta — così.pdf", "application/pdf", pdf)],
    )
    message = _parsed(raw)
    assert message.get_content_maintype() == "multipart"
    parts = [p for p in message.walk() if p.get_filename()]
    assert len(parts) == 1
    # A non-ASCII filename has to survive too: Italian document titles have accents.
    assert parts[0].get_filename() == "Offerta — così.pdf"
    assert parts[0].get_payload(decode=True) == pdf


def test_two_attachments_both_arrive_whole() -> None:
    """One attachment is the case a single-element loop gets right by accident."""
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Documenti",
        body_text="Due allegati.",
        message_id="<a.7@crm.example.it>",
        attachments=[
            OutgoingAttachment("uno.pdf", "application/pdf", b"%PDF-uno"),
            OutgoingAttachment("due.csv", "text/csv", "totale;1.200 €\n".encode()),
        ],
    )
    parts = [p for p in _parsed(raw).walk() if p.get_filename()]
    assert [p.get_filename() for p in parts] == ["uno.pdf", "due.csv"]
    assert [p.get_payload(decode=True) for p in parts] == [
        b"%PDF-uno",
        "totale;1.200 €\n".encode(),
    ]


def test_a_newline_in_a_header_is_refused_rather_than_injected() -> None:
    """A subject carrying CRLF would let a caller append arbitrary headers -- a Bcc, for
    instance. Escaping is decided by context, and the context here is an RFC822 header."""
    for hostile in ["Offerta\r\nBcc: chiunque@altrove.it", "Offerta\nX-Header: x", "a\rb"]:
        with pytest.raises(ValidationFailed) as caught:
            build_rfc822(
                from_address="io@example.it",
                from_name="Io",
                to=["ada@acme.it"],
                cc=[],
                subject=hostile,
                body_text="x",
                message_id="<a.4@crm.example.it>",
            )
        assert caught.value.details["field"] == "subject"


def test_every_other_header_field_refuses_a_newline_too() -> None:
    """The subject is the obvious one and therefore the one that gets a guard. The
    display name, the thread headers and an attachment's filename all reach a header
    field just the same, and each names itself when it refuses."""
    hostile = "x\r\nBcc: chiunque@altrove.it"
    base = {
        "from_address": "io@example.it",
        "from_name": "Io",
        "to": ["ada@acme.it"],
        "cc": [],
        "subject": "Offerta",
        "body_text": "x",
        "message_id": "<a.8@crm.example.it>",
    }
    for field, override in (
        ("from_name", {"from_name": hostile}),
        ("in_reply_to", {"in_reply_to": hostile}),
        ("references", {"references": hostile}),
        (
            "attachment_filename",
            {"attachments": [OutgoingAttachment(hostile, "application/pdf", b"%PDF")]},
        ),
    ):
        with pytest.raises(ValidationFailed) as caught:
            build_rfc822(**{**base, **override})  # type: ignore[arg-type]
        assert caught.value.details["field"] == field


def test_an_attachment_type_that_is_not_type_slash_subtype_is_refused_not_injected() -> None:
    """`EmailMessage` takes the subtype verbatim: `pdf; name="evil"` would become a
    header parameter, and a CRLF raises a bare `ValueError` that reaches the caller as
    a 500. Both are a `ValidationFailed` naming the field."""
    base = {
        "from_address": "io@example.it",
        "from_name": "Io",
        "to": ["ada@acme.it"],
        "cc": [],
        "subject": "Offerta",
        "body_text": "x",
        "message_id": "<a.9@crm.example.it>",
    }
    for hostile in (
        'application/pdf; name="evil"',
        "application/pdf\r\nBcc: chiunque@altrove.it",
        "application/pdf\n",
        "pdf",
        "",
        "application/",
        "/pdf",
    ):
        attachments = [OutgoingAttachment("offerta.pdf", hostile, b"%PDF")]
        with pytest.raises(ValidationFailed) as caught:
            build_rfc822(**{**base, "attachments": attachments})  # type: ignore[arg-type]
        assert caught.value.details["field"] == "attachment_mime", repr(hostile)


def test_an_attachment_type_is_case_insensitive() -> None:
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Io",
        to=["ada@acme.it"],
        cc=[],
        subject="Offerta",
        body_text="x",
        message_id="<a.10@crm.example.it>",
        attachments=[OutgoingAttachment("offerta.pdf", "Application/PDF", b"%PDF")],
    )
    assert b"Content-Type: application/pdf" in raw


def test_a_recipient_that_is_not_an_address_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        build_rfc822(
            from_address="io@example.it",
            from_name="Io",
            to=["ada@acme.it, bcc@altrove.it"],
            cc=[],
            subject="x",
            body_text="y",
            message_id="<a.5@crm.example.it>",
        )


def test_a_bad_address_names_the_field_it_came_from() -> None:
    """`to` and `cc` fail differently for the caller: one is the send, the other is who
    else sees it. A single `address` field would make the composer highlight the wrong
    box."""
    base = {
        "from_address": "io@example.it",
        "from_name": "Io",
        "to": ["ada@acme.it"],
        "cc": [],
        "subject": "x",
        "body_text": "y",
        "message_id": "<a.9@crm.example.it>",
    }
    for field, override in (
        ("to", {"to": ["nope"]}),
        ("cc", {"cc": ["nope"]}),
        ("from_address", {"from_address": "nope"}),
    ):
        with pytest.raises(ValidationFailed) as caught:
            build_rfc822(**{**base, **override})  # type: ignore[arg-type]
        assert caught.value.details["field"] == field


def test_a_malformed_message_id_is_refused() -> None:
    for hostile in ["a.1@crm.example.it", "<a.1@crm>", "<>", "<a.1@crm.example.it"]:
        with pytest.raises(ValidationFailed) as caught:
            build_rfc822(
                from_address="io@example.it",
                from_name="Io",
                to=["ada@acme.it"],
                cc=[],
                subject="x",
                body_text="y",
                message_id=hostile,
            )
        assert caught.value.details["field"] == "message_id"


def test_at_least_one_recipient_is_required() -> None:
    with pytest.raises(ValidationFailed):
        build_rfc822(
            from_address="io@example.it",
            from_name="Io",
            to=[],
            cc=[],
            subject="x",
            body_text="y",
            message_id="<a.6@crm.example.it>",
        )


def test_base64url_output_is_what_the_send_endpoint_expects() -> None:
    encoded = to_base64url(b"ciao \xc3\xa8")
    assert "+" not in encoded and "/" not in encoded and "=" not in encoded
    assert base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)) == b"ciao \xc3\xa8"


def test_what_we_build_is_what_the_parser_reads_back() -> None:
    """The round trip, and the only test here that exercises both halves of the slice.

    Builder -> base64url -> the send endpoint (FakeGmail, which decodes and stores the
    message exactly as Gmail would file it in Sent) -> `format=full` -> `parse_message`.
    A builder and a parser that agree only with themselves are two bugs that cancel; this
    is the assertion that neither can be quietly wrong.
    """
    fake = FakeGmail()
    raw = build_rfc822(
        from_address="io@example.it",
        from_name="Società Ròssi",
        to=["ada@acme.it"],
        cc=["bob@acme.it"],
        subject=TRICKY,
        body_text=TRICKY,
        message_id="<roundtrip.1@crm.example.it>",
        in_reply_to="<original.1@acme.it>",
        references="<thread.root@acme.it> <original.1@acme.it>",
    )
    status, body, _ = fake(
        "POST",
        f"{GMAIL_API_ROOT}/messages/send",
        {},
        json.dumps({"raw": to_base64url(raw)}).encode(),
    )
    assert status == 200
    gmail_id = json.loads(body)["id"]

    status, body, _ = fake("GET", message_get_url(gmail_id), {}, None)
    assert status == 200
    parsed = parse_message(json.loads(body), body_max_bytes=262_144, store_bodies=True)

    assert parsed.message_id_header == "<roundtrip.1@crm.example.it>"
    assert parsed.subject == TRICKY
    assert parsed.body_text.replace("\r\n", "\n").rstrip("\n") == TRICKY
    assert parsed.from_address == "io@example.it"
    assert parsed.to_addresses == ["ada@acme.it"]
    assert parsed.cc_addresses == ["bob@acme.it"]
    assert parsed.in_reply_to == "<original.1@acme.it>"
    assert parsed.references == "<thread.root@acme.it> <original.1@acme.it>"
    # The message we sent is ours, so the sync must not file it as something that
    # arrived.
    assert parsed.direction_is_inbound("io@example.it") is False
