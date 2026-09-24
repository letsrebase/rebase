"""The Documenso client against a Documenso in a dict (REB-387): the probe's exact calls,
what the hub reads back, and the one sentence an admin reads when it fails."""

import json
from datetime import UTC, datetime

import pytest
from fakes_documenso import BASE, TOKEN, FakeDocumenso

from rebase_core.config import Settings
from rebase_core.contracts.fields import ContractFailed
from rebase_core.contracts.render import SignatureBlank
from rebase_core.documenso import (
    CANCELLED,
    COMPLETED,
    EMAIL_SETTINGS_OFF,
    REJECTED,
    UNREACHABLE,
    DocumensoClient,
    Outcome,
    WebhookBody,
    client_from_settings,
    fields_from_blanks,
    outcome_from_envelope,
    outcome_from_webhook,
)
from rebase_core.errors import DocumensoFailed
from rebase_core.http import NETWORK_ERROR_STATUS

PDF = b"%PDF-1.7 lettera"
EXTERNAL = "0192a0c0-0000-7000-8000-000000000001"
# Where the probe measured the letter's two blanks (probe § 9), in points.
SIGNATURE = SignatureBlank("firma professionista", 2, 303.638, 707.281, 155.906, 22.660)
DATE = SignatureBlank("data firma", 2, 165.811, 647.241, 85.039, 6.660)
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


def _create(fake: FakeDocumenso) -> str:
    return fake.client().create(
        title="Lettera di incarico n. 2026-001",
        external_id=EXTERNAL,
        filename="lettera-di-incarico-2026-001.pdf",
        pdf=PDF,
        signer_email="ada@studio.it",
        signer_name="Ada Lovelace",
        fields=fields_from_blanks([DATE, SIGNATURE]),
    )


def _outcome(fake: FakeDocumenso, envelope_id: str, event: str) -> Outcome | None:
    return outcome_from_webhook(WebhookBody.model_validate(fake.webhook(envelope_id, event)))


def test_the_create_sends_the_probes_payload_with_every_documenso_mail_off() -> None:
    fake = FakeDocumenso()
    envelope = fake.envelopes[_create(fake)]
    assert envelope.payload == {
        "type": "DOCUMENT",
        "title": "Lettera di incarico n. 2026-001",
        "externalId": EXTERNAL,
        "recipients": [
            {
                "email": "ada@studio.it",
                "name": "Ada Lovelace",
                "role": "SIGNER",
                "fields": [
                    {
                        "type": "DATE",
                        "page": 2,
                        "positionX": 27.854,
                        "positionY": 76.88,
                        "width": 14.286,
                        "height": 0.791,
                        "fieldMeta": {"type": "date", "fontSize": 9, "textAlign": "left"},
                    },
                    {
                        "type": "SIGNATURE",
                        "page": 2,
                        "positionX": 51.008,
                        "positionY": 84.011,
                        "width": 26.191,
                        "height": 2.692,
                    },
                ],
            }
        ],
        "meta": {
            "distributionMethod": "NONE",
            "language": "it",
            "timezone": "Europe/Rome",
            "dateFormat": "dd/MM/yyyy",
            "envelopeExpirationPeriod": {"disabled": True},
            "emailSettings": dict.fromkeys(EMAIL_SETTINGS_OFF, False),
        },
    }
    # The owner's «Signing Complete!» is the one Documenso mail the probe still saw.
    assert EMAIL_SETTINGS_OFF["ownerDocumentCompleted"] is False
    assert len(EMAIL_SETTINGS_OFF) == 9
    assert (envelope.filename, envelope.pdf) == ("lettera-di-incarico-2026-001.pdf", PDF)
    assert fake.calls == [("POST", "/envelope/create")]
    assert fake.headers[0]["Authorization"] == TOKEN
    assert fake.headers[0]["Content-Type"].startswith("multipart/form-data; boundary=")


def test_get_then_distribute_answer_the_item_and_the_signing_url() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    envelope = client.get(envelope_id)
    assert (envelope.id, envelope.status) == (envelope_id, "DRAFT")
    assert envelope.item_id == fake.envelopes[envelope_id].item_id
    assert envelope.external_id == EXTERNAL
    url = client.distribute(envelope_id)
    assert url == f"https://firma.letsrebase.test/sign/{fake.envelopes[envelope_id].token}"
    assert json.loads(fake.bodies[-1]) == {
        "envelopeId": envelope_id,
        "meta": {"distributionMethod": "NONE"},
    }
    assert client.get(envelope_id).status == "PENDING"


def test_the_signed_copy_is_downloaded_by_item_and_the_signers_date_read_back() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    client.distribute(envelope_id)
    fake.sign(envelope_id, SIGNED_AT)
    envelope = client.get(envelope_id)
    assert (envelope.status, envelope.signed_at) == ("COMPLETED", SIGNED_AT)
    assert client.download_signed(envelope.item_id) == fake.signed_pdf(envelope_id)
    assert fake.calls[-1] == ("GET", f"/envelope/item/{envelope.item_id}/download?version=signed")


def test_cancel_takes_only_an_envelope_out_for_signature() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    with pytest.raises(DocumensoFailed, match="Only pending documents can be cancelled"):
        client.cancel(envelope_id, "Annullato da rebase.")
    client.distribute(envelope_id)
    client.cancel(envelope_id, "Annullato da rebase.")
    assert json.loads(fake.bodies[-1]) == {
        "envelopeId": envelope_id,
        "reason": "Annullato da rebase.",
    }
    assert client.get(envelope_id).status == "CANCELLED"


def test_a_refusal_carries_documensos_sentence_and_never_its_stack() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    fake.fail("distribute", 400, "Recipient is missing a signature field")
    with pytest.raises(DocumensoFailed) as caught:
        fake.client().distribute(envelope_id)
    message = caught.value.message
    assert message == "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    assert "node_modules" not in message and "stack" not in message
    assert "HTTP 400" in caught.value.detail


def test_a_wrong_token_is_refused_in_documensos_words() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    wrong = DocumensoClient(BASE, "api_sbagliato", http=fake)
    with pytest.raises(DocumensoFailed, match="Invalid session or API token"):
        wrong.get(envelope_id)


def test_no_answer_is_one_sentence_whatever_the_network_did() -> None:
    fake = FakeDocumenso()
    fake.down("create")
    with pytest.raises(DocumensoFailed) as caught:
        _create(fake)
    assert caught.value.message == UNREACHABLE
    silent = DocumensoClient(BASE, TOKEN, http=lambda m, u, h, b: (NETWORK_ERROR_STATUS, b""))
    with pytest.raises(DocumensoFailed) as again:
        silent.get("envelope_0001")
    assert again.value.message == UNREACHABLE


def test_an_answer_the_hub_cannot_read_is_refused_not_guessed() -> None:
    odd = DocumensoClient(BASE, TOKEN, http=lambda m, u, h, b: (200, b"<html>proxy</html>"))
    with pytest.raises(DocumensoFailed, match="non riconosco"):
        odd.get("envelope_0001")
    with pytest.raises(DocumensoFailed, match="non riconosco"):
        odd.download_signed("envelope_item_0001")


def test_an_unknown_blank_or_a_document_without_a_signature_is_refused() -> None:
    with pytest.raises(ContractFailed):
        fields_from_blanks([SignatureBlank("firma rebase", 2, 10, 10, 10, 10), SIGNATURE])
    with pytest.raises(ContractFailed):
        fields_from_blanks([DATE])


def test_the_webhooks_outcome_is_the_signers_never_the_cancellations() -> None:
    fake = FakeDocumenso()
    client = fake.client()
    signed, refused, cancelled = _create(fake), _create(fake), _create(fake)
    for envelope_id in (signed, refused, cancelled):
        client.distribute(envelope_id)
    fake.sign(signed, SIGNED_AT)
    fake.reject(refused, "Il compenso non è quello concordato")
    client.cancel(cancelled, "Annullato da rebase.")
    # `completedAt` is a few minutes after `signedAt` (fix round 1, M6): the outcome must
    # come from the signer's own timestamp, never the envelope's.
    assert fake.envelopes[signed].completed_at != SIGNED_AT
    assert _outcome(fake, signed, "DOCUMENT_COMPLETED") == Outcome(
        signed, COMPLETED, signed_at=SIGNED_AT
    )
    assert _outcome(fake, refused, "DOCUMENT_REJECTED") == Outcome(
        refused, REJECTED, reason="Il compenso non è quello concordato"
    )
    # A cancellation sets `completedAt` too: it is not a signature date.
    assert fake.webhook(cancelled, "DOCUMENT_CANCELLED")["payload"]["completedAt"] is not None
    assert _outcome(fake, cancelled, "DOCUMENT_CANCELLED") == Outcome(cancelled, CANCELLED)
    for event in ("DOCUMENT_OPENED", "DOCUMENT_SENT", "DOCUMENT_SIGNED", "RECIPIENT_EXPIRED"):
        assert _outcome(fake, signed, event) is None


def test_refresh_reads_the_same_outcomes_from_the_envelope() -> None:
    fake = FakeDocumenso()
    client = fake.client()
    pending, signed, refused, cancelled = (_create(fake) for _ in range(4))
    for envelope_id in (pending, signed, refused, cancelled):
        client.distribute(envelope_id)
    fake.sign(signed, SIGNED_AT)
    fake.reject(refused, "No")
    client.cancel(cancelled, "Annullato da rebase.")
    assert outcome_from_envelope(client.get(pending)) is None
    assert outcome_from_envelope(client.get(signed)) == Outcome(signed, COMPLETED, SIGNED_AT)
    assert outcome_from_envelope(client.get(refused)) == Outcome(refused, REJECTED, reason="No")
    assert outcome_from_envelope(client.get(cancelled)) == Outcome(cancelled, CANCELLED)


def test_no_url_or_no_token_means_no_client() -> None:
    assert client_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    assert client_from_settings(Settings(_env_file=None, documenso_url=BASE)) is None  # type: ignore[call-arg]
    both = Settings(_env_file=None, documenso_url=BASE, documenso_api_token=TOKEN)  # type: ignore[call-arg]
    assert isinstance(client_from_settings(both), DocumensoClient)
    assert Settings(_env_file=None).contracts_mail == "ciao@letsrebase.com"  # type: ignore[call-arg]
