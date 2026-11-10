"""The signing tools over the MCP client (REB-1019, collaborators design § 6): a full-access admin
token sends an engagement for signature (the framework agreement first, only the freelancer
signs), a token without `full_access` does not even see the tool, refreshing a document's
signature is the admin's own token, and cancelling an engagement cancels its envelope on Documenso
too.

Driven through a Documenso fake and a `RecordingSender`: nothing here calls a real Documenso or
mails anyone. The service is built the way the tools build it, from the server's context; only the
two network collaborators are handed in.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fakes.fake_documenso import FakeDocumenso
from fakes.letter_world import ADMIN, LEGAL_ENTITY_FIELDS, World, needs_binaries
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings
from pigrocrm.core.documents.models import Document
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.http import McpHttpApp
from pigrocrm_mcp.server import build_server
from pigrocrm_mcp.tools import collaborators as collaborator_tools

TENANT = UUID("018f0000-0000-7000-8000-000000000002")
SIGNED_AT = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC)
PRIVILEGED = {
    "send_engagement_for_signature",
    "resend_signing_mail",
    "cancel_framework_agreement",
    "record_framework_notice",
}


class Desk:
    """A world with a freelancer, a draft engagement and its stored letter, a Documenso fake and a
    mail recorder, and servers that build their signing service on those two."""

    def __init__(self, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = session
        self.tmp_path = tmp_path
        self.world = World(session, tmp_path)
        LegalEntityService(session).upsert_default(
            LegalEntityUpsert(**LEGAL_ENTITY_FIELDS, email="contratti@rebase.test"), ADMIN
        )
        self.fake = FakeDocumenso()
        self.mails = RecordingSender()
        self.who = self.world.freelancer()
        self.engagement = self.world.engagement(self.who)
        self.world.letters.create_letter(self.engagement.id, {"attivita": "Sviluppo"}, ADMIN)
        real = SigningService
        fake, mails = self.fake, self.mails

        def build(
            session: Session, storage: Any, settings: Settings | None, *, tenant_id: UUID | None
        ) -> SigningService:
            return real(
                session,
                storage,
                settings,
                documenso=fake.client(),
                sender=mails,
                tenant_id=tenant_id,
            )

        monkeypatch.setattr(collaborator_tools, "SigningService", build)

    def server(self, *, full_access: bool, ceiling: bool = True, signing: bool = True) -> Any:
        """`ceiling` is the space's `mcp_full_access`; `full_access` is the token's own flag."""
        return build_server(
            lambda: self.session,
            lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),
            LocalFileStorage(self.tmp_path),
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                mcp_full_access=ceiling,
                module_collaborators=True,
                # The tools check the server holds what signing needs: the service itself is the
                # fake one `Desk` builds.
                documenso_url="http://documenso.test" if signing else "",
                documenso_api_token="api_fake0123456789" if signing else "",
                resend_api_key="re_fake" if signing else "",
            ),
            tenant_id=TENANT,
        )

    def document(self, kind: str) -> Document:
        self.session.expire_all()
        return self.session.scalars(
            select(Document).where(Document.type == kind, Document.customer_id == self.who.id)
        ).one()

    def engagement_state(self) -> str:
        self.session.expire_all()
        found = self.session.get(Engagement, self.engagement.id)
        assert found is not None
        return found.state

    def a_person_writes_the_framework_agreement(self) -> None:
        """An agent reaches only a framework agreement a person already wrote."""
        self.world.letters.create_framework_agreement(self.who.id, None, ADMIN)

    async def send(self) -> dict[str, Any]:
        self.a_person_writes_the_framework_agreement()
        async with Client(self.server(full_access=True)) as client:
            return _payload(
                await client.call_tool(
                    "send_engagement_for_signature", {"engagement_id": str(self.engagement.id)}
                )
            )

    async def refresh(self, document: Document) -> dict[str, Any]:
        async with Client(self.server(full_access=False)) as client:
            return _payload(
                await client.call_tool(
                    "refresh_document_signature", {"document_id": str(document.id)}
                )
            )

    def freelancer_signs_the_framework(self) -> Document:
        framework = self.document("framework_agreement")
        assert framework.envelope_id is not None
        self.fake.sign(framework.envelope_id, SIGNED_AT)
        return framework


@pytest.fixture
def desk(mcp_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Desk:
    return Desk(mcp_session, tmp_path, monkeypatch)


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return result.structured_content or json.loads(result.content[0].text)


async def _names(server: Any) -> set[str]:
    return {tool.name for tool in await server.list_tools()}


# ---- who sees what ----------------------------------------------------------------------------


@needs_binaries
async def test_a_full_access_token_sees_every_signing_tool_and_a_plain_one_only_the_refresh(
    desk: Desk,
) -> None:
    full = await _names(desk.server(full_access=True))
    plain = await _names(desk.server(full_access=False))

    assert PRIVILEGED | {"refresh_document_signature"} <= full
    assert "refresh_document_signature" in plain and not PRIVILEGED & plain


@needs_binaries
async def test_a_token_without_full_access_is_refused_and_nothing_leaves(desk: Desk) -> None:
    async with Client(desk.server(full_access=False)) as client:
        results = [
            await client.call_tool(
                tool, {"engagement_id" if "send" in tool else "document_id": "x"}
            )
            for tool in sorted(PRIVILEGED)
        ]

    assert all(result.is_error for result in results)
    assert {result.content[0].text for result in results} == {  # type: ignore[union-attr]
        f"Unknown tool: {tool}" for tool in PRIVILEGED
    }
    assert desk.fake.calls == [] and desk.mails.sent == []


# ---- sending ----------------------------------------------------------------------------------


@needs_binaries
async def test_a_full_access_admin_sends_the_framework_agreement_first_to_the_freelancer_alone(
    desk: Desk,
) -> None:
    report = await desk.send()

    assert (report["sent"], report["mail_sent"]) == ("framework_agreement", True)
    assert report["engagement"]["state"] == "awaiting_signature"
    assert {(d["kind"], d["state"]) for d in report["documents"]} == {
        ("framework_agreement", "sent"),
        ("engagement_letter", "pending"),
    }
    assert "provenance" in report
    # One envelope, for the framework agreement, named after this space and its only signer.
    (payload,) = desk.fake.created()
    assert payload["externalId"] == f"{TENANT}:{desk.document('framework_agreement').id}"
    (recipient,) = payload["recipients"]
    assert (recipient["role"], recipient["email"]) == ("SIGNER", "ada@example.com")
    (mail,) = desk.mails.sent
    assert mail.to == "ada@example.com"
    # The letter waits: it has no envelope until the agreement is signed.
    assert desk.document("engagement_letter").signature_state == "pending"


@needs_binaries
async def test_an_agent_cannot_write_the_framework_agreement_so_a_send_without_one_is_refused(
    desk: Desk,
) -> None:
    async with Client(desk.server(full_access=True)) as client:
        result = await client.call_tool(
            "send_engagement_for_signature", {"engagement_id": str(desk.engagement.id)}
        )

    assert result.is_error
    assert "create_framework_agreement" in result.content[0].text  # type: ignore[union-attr]
    assert desk.fake.calls == [] and desk.mails.sent == []
    assert desk.engagement_state() == "draft"


@needs_binaries
async def test_a_server_without_the_documenso_token_or_the_mail_key_sends_the_person_to_the_app(
    desk: Desk,
) -> None:
    """The MCP container holds neither, to keep the secrets apart: the tools that send, resend,
    cancel or refresh say so and name the app, and nothing is called or changed."""
    desk.a_person_writes_the_framework_agreement()
    framework = desk.document("framework_agreement")
    calls = {
        "send_engagement_for_signature": {"engagement_id": str(desk.engagement.id)},
        "resend_signing_mail": {"document_id": str(framework.id)},
        "cancel_framework_agreement": {"document_id": str(framework.id)},
        "refresh_document_signature": {"document_id": str(framework.id)},
    }
    async with Client(desk.server(full_access=True, signing=False)) as client:
        for name, arguments in calls.items():
            result = await client.call_tool(name, arguments)
            assert result.is_error, name
            assert "Fallo dall'app" in result.content[0].text, name  # type: ignore[union-attr]
    assert desk.engagement_state() == "draft"
    assert desk.fake.calls == [] and desk.mails.sent == []


# ---- refreshing -------------------------------------------------------------------------------


@needs_binaries
async def test_a_plain_admin_token_refreshes_a_signature_and_the_waiting_letter_leaves(
    desk: Desk,
) -> None:
    await desk.send()
    framework = desk.freelancer_signs_the_framework()

    refreshed = await desk.refresh(framework)

    assert (refreshed["state"], refreshed["kind"]) == ("signed", "framework_agreement")
    assert refreshed["signed_at"].startswith("2026-11-03T10:00")
    assert desk.document("engagement_letter").signature_state == "sent"
    assert len(desk.fake.created()) == 2
    assert desk.engagement_state() == "awaiting_signature"


@needs_binaries
async def test_refreshing_a_document_that_never_left_is_refused(desk: Desk) -> None:
    await desk.send()
    letter = desk.document("engagement_letter")
    asked = desk.fake.called("get")

    async with Client(desk.server(full_access=False)) as client:
        result = await client.call_tool(
            "refresh_document_signature", {"document_id": str(letter.id)}
        )

    assert result.is_error
    assert desk.fake.called("get") == asked


# ---- cancelling and the rest ------------------------------------------------------------------


@needs_binaries
async def test_cancelling_an_engagement_cancels_its_envelope_on_documenso(desk: Desk) -> None:
    await desk.send()
    await desk.refresh(desk.freelancer_signs_the_framework())
    letter = desk.document("engagement_letter")
    assert letter.signature_state == "sent" and letter.envelope_id is not None

    async with Client(desk.server(full_access=True)) as client:
        cancelled = _payload(
            await client.call_tool("cancel_engagement", {"engagement_id": str(desk.engagement.id)})
        )

    assert cancelled["state"] == "cancelled"
    assert desk.fake.envelopes[letter.envelope_id].status == "CANCELLED"
    assert desk.fake.called("cancel") == 1
    assert desk.document("engagement_letter").signature_state == "cancelled"


@needs_binaries
async def test_resending_the_mail_and_cancelling_an_unsigned_framework_agreement(
    desk: Desk,
) -> None:
    await desk.send()
    framework = desk.document("framework_agreement")
    assert framework.envelope_id is not None

    async with Client(desk.server(full_access=True)) as client:
        resent = _payload(
            await client.call_tool("resend_signing_mail", {"document_id": str(framework.id)})
        )
        cancelled = _payload(
            await client.call_tool("cancel_framework_agreement", {"document_id": str(framework.id)})
        )

    assert resent["state"] == "sent"
    assert [mail.to for mail in desk.mails.sent[:2]] == ["ada@example.com", "ada@example.com"]
    assert cancelled["state"] == "cancelled"
    assert desk.fake.envelopes[framework.envelope_id].status == "CANCELLED"


@needs_binaries
async def test_a_notice_ends_a_signed_framework_agreement(desk: Desk) -> None:
    await desk.send()
    framework = desk.freelancer_signs_the_framework()
    await desk.refresh(framework)

    async with Client(desk.server(full_access=True)) as client:
        served = _payload(
            await client.call_tool("record_framework_notice", {"document_id": str(framework.id)})
        )

    assert (served["state"], served["active"]) == ("notice_served", False)
    assert served["notice_at"] is not None


# ---- the space's id for the envelope's externalId ---------------------------------------------


def test_the_http_server_reads_the_registry_id_only_when_signing_is_on() -> None:
    off = McpHttpApp(Settings(_env_file=None))  # type: ignore[call-arg]
    on = McpHttpApp(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            documenso_url="http://documenso.test",
            documenso_api_token="api_fake0123456789",
        )
    )

    # A space with signing off needs no registry at all: not even a slug that does not exist.
    assert off._tenant_id("nowhere", off._settings) is None
    # The root installation has no registry id: its envelopes carry the bare document id.
    assert on._tenant_id(None, on._settings) is None
