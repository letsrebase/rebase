"""The fake's send/lookup behaviour must match whatever the note recorded.

This does not verify Gmail -- no test in this slice touches the network, and the root
`conftest.py` makes that a property of the suite rather than a convention. What it
verifies is that `FakeGmail` models the behaviour the note *records*, so every test built
on the fake is testing the recorded contract rather than a convenient one, and so that
changing the record forces the fake (and the setting) to change with it.

The note may record `YES`, `NO` or `UNVERIFIED`, and the third is a real state, not a
placeholder: it is what the record says while the one manual step of plan 5B-2 is still
outstanding. Asserting only on `YES`/`NO` would leave exactly two ways out -- a red suite
forever, or somebody writing `YES` to make it green -- and the second is how an
assumption becomes a fact nobody can find again.
"""

import base64
import json
import re
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

import pytest
from fakes.fake_gmail import FakeGmail
from fakes.gmail_fixtures import gmail_settings

from pigrocrm.core.gmail.query import GMAIL_API_ROOT, messages_list_url, rfc822msgid_query

NOTE = (
    Path(__file__).parents[3] / "docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md"
)
_STATUS = re.compile(r"^\*\*Status:\*\* (YES|NO|UNVERIFIED)$", re.MULTILINE)
# The exact sentence the note owes the reader while nothing has been measured. Matched
# literally so that deleting the caveat while leaving the status at UNVERIFIED fails
# here, which is the edit most likely to be made in a hurry.
_CAVEAT = "**No live verification has been performed.**"
SEND_URL = f"{GMAIL_API_ROOT}/messages/send"


def recorded_status() -> str:
    match = _STATUS.search(NOTE.read_text(encoding="utf-8"))
    assert match is not None, (
        "the verification note must carry a line `**Status:** YES|NO|UNVERIFIED`: a "
        "design that rests on a third party's behaviour needs its check written down"
    )
    return match.group(1)


def _raw_request(message_id: str, *, to: str = "ada@acme.it", subject: str = "Offerta") -> bytes:
    """The body `users.messages.send` receives: `{"raw": base64url(RFC822)}`.

    Assembled here with the standard library rather than with `build_rfc822` (B2-2, which
    does not exist yet) so this test constrains the *fake* and nothing else.
    """
    message = EmailMessage(policy=SMTP)
    message["From"] = "io@example.it"
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content("ciao", subtype="plain", charset="utf-8", cte="quoted-printable")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    return json.dumps({"raw": raw}).encode()


def _send(fake: FakeGmail, message_id: str, **kwargs: str) -> tuple[int, bytes]:
    status, body, _ = fake("POST", SEND_URL, {}, _raw_request(message_id, **kwargs))
    return status, body


def _lookup(fake: FakeGmail, message_id: str) -> list[dict[str, str]]:
    status, body, _ = fake("GET", messages_list_url(rfc822msgid_query(message_id)), {}, None)
    assert status == 200
    found: list[dict[str, str]] = json.loads(body)["messages"]
    return found


def test_the_verification_was_recorded_with_a_status_and_no_placeholders() -> None:
    """An unrecorded verification is an assumption, and a half-filled template is worse
    than either: it reads as a result."""
    text = NOTE.read_text(encoding="utf-8")
    assert recorded_status() in {"YES", "NO", "UNVERIFIED"}
    for placeholder in ("<date>", "<name>", "<n>", "<gmail message id>", "YES / NO"):
        assert placeholder not in text, f"the note still has its placeholder {placeholder!r}"


def test_an_unmeasured_result_is_never_dressed_up_as_a_measured_one() -> None:
    """The failure this whole task exists to prevent: `UNVERIFIED` quietly reading like
    `YES` to whoever skims the note next month."""
    text = NOTE.read_text(encoding="utf-8")
    if recorded_status() == "UNVERIFIED":
        assert _CAVEAT in text
        assert "Message-ID preserved: **UNVERIFIED**" in text
    else:
        assert _CAVEAT not in text, (
            "the note claims a result and still carries the 'not verified' caveat"
        )


def test_the_setting_default_follows_the_recorded_status() -> None:
    """The record and the code cannot drift apart: recording `NO` and leaving the exact
    path switched on would make the reconciliation of spec 6.3 look for a Message-ID
    Gmail threw away, and report `failed` on mail that was actually delivered."""
    settings = gmail_settings()
    assert settings.gmail_reconcile_by_message_id is (recorded_status() != "NO")


def test_the_fake_keeps_the_message_id_we_supplied_and_the_note_still_allows_that() -> None:
    """`FakeGmail` models "Gmail preserves our header". That is an assumption while the
    status is UNVERIFIED, and recording `NO` must break this test rather than leave every
    downstream test asserting a contract Gmail does not offer."""
    assert recorded_status() != "NO", (
        "the note now records that Gmail replaces our Message-ID: FakeGmail._register_sent "
        "has to mint its own id, and the reconciliation of B2-6 has to take the fallback"
    )
    fake = FakeGmail()
    status, body = _send(fake, "<ours.1@crm.example.it>")
    assert status == 200
    assigned = json.loads(body)["id"]
    found = _lookup(fake, "<ours.1@crm.example.it>")
    assert [message["id"] for message in found] == [assigned]


def test_the_fake_finds_nothing_for_a_message_id_that_was_never_sent() -> None:
    fake = FakeGmail()
    _send(fake, "<ours.2@crm.example.it>")
    assert _lookup(fake, "<never.sent@crm.example.it>") == []


def test_a_send_that_never_answered_may_still_have_delivered() -> None:
    """The whole of spec 6.3(b): a lost answer is not a lost message. With
    `deliver_on_timeout` the mail really did arrive and only the response was lost, which
    is the case the previous system gets wrong in production -- and a fake that cannot express it
    would let B2-6's reconciliation tests pass against a mailbox that can never surprise them.
    """
    delivered = FakeGmail(timeout_on_send=True, deliver_on_timeout=True)
    status, _ = _send(delivered, "<ours.3@crm.example.it>")
    assert status == 599
    assert len(_lookup(delivered, "<ours.3@crm.example.it>")) == 1

    lost = FakeGmail(timeout_on_send=True)
    status, _ = _send(lost, "<ours.4@crm.example.it>")
    assert status == 599
    assert _lookup(lost, "<ours.4@crm.example.it>") == []


def test_the_recipient_and_the_subject_survive_into_the_stored_message() -> None:
    """The fallback of spec 6.3 matches on recipient + subject + internalDate. If the
    note ever records `NO`, that path is the one B2-6 takes, and a fake that dropped
    those headers on send would make it untestable."""
    fake = FakeGmail()
    _send(fake, "<ours.5@crm.example.it>", to="bob@acme.it", subject="Sollecito")
    stored = next(iter(fake.messages.values()))
    assert stored.headers["To"] == "bob@acme.it"
    assert stored.headers["Subject"] == "Sollecito"
    assert stored.internal_date_ms > 0


def test_a_send_without_a_message_id_is_refused_by_the_fake() -> None:
    """Gmail would mint one itself, and a fake that did the same would hide the very
    defect this slice fixes: the previous system's builder emits no Message-ID at all, and the
    reconciliation has nothing to look the message up by."""
    fake = FakeGmail()
    message = EmailMessage(policy=SMTP)
    message["To"] = "ada@acme.it"
    message.set_content("ciao")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    with pytest.raises(AssertionError, match="Message-ID"):
        fake("POST", SEND_URL, {}, json.dumps({"raw": raw}).encode())
