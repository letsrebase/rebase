"""The Documenso client, the envelope's `externalId`, which space an envelope belongs to, the two
check commands and the settings that switch signing on (REB-1019). A test here never calls a real
Documenso: the fake is `fakes/fake_documenso.py`.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fakes.fake_documenso import BASE, TOKEN, FakeDocumenso
from fakes.letter_world import needs_binaries
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pigrocrm.core.cli import main as cli_main
from pigrocrm.core.cli.signing import contracts_check, documenso_check
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.documenso.client import (
    COMPLETED,
    REJECTED,
    DocumensoClient,
    Envelope,
    WebhookBody,
    client_from_settings,
    external_id,
    fields_from_blanks,
    outcome_from_envelope,
    outcome_from_webhook,
    parse_external_id,
)
from pigrocrm.core.documenso.routing import route_envelope, tenant_id_for
from pigrocrm.core.errors import DocumensoFailed, ValidationFailed
from pigrocrm.core.http import NETWORK_ERROR_STATUS
from pigrocrm.core.tenants import Tenant, ensure_tenants_database

DOCUMENT = UUID("018f0000-0000-7000-8000-0000000000aa")
TENANT = UUID("018f0000-0000-7000-8000-0000000000bb")


# ---- the external id ---------------------------------------------------------------------------


def test_an_external_id_names_the_space_and_the_document_and_reads_back() -> None:
    written = external_id(TENANT, DOCUMENT)
    assert written == f"{TENANT}:{DOCUMENT}"
    parsed = parse_external_id(written)
    assert parsed is not None and parsed.qualified
    assert (parsed.tenant, parsed.document_id) == (str(TENANT), DOCUMENT)
    # Documenso takes any string up to 255 characters there: ours is 73.
    assert len(written) == 73


def test_the_root_installation_writes_root_where_a_space_writes_its_id() -> None:
    written = external_id(None, DOCUMENT)
    assert written == f"root:{DOCUMENT}"
    parsed = parse_external_id(written)
    assert parsed is not None and parsed.tenant == "root"


def test_the_hubs_bare_document_id_still_reads_and_says_it_names_no_space() -> None:
    parsed = parse_external_id(str(DOCUMENT))
    assert parsed is not None and not parsed.qualified and parsed.document_id == DOCUMENT


@pytest.mark.parametrize(
    "value",
    [None, "", "prova-reb-393", "not-a-tenant:" + str(DOCUMENT), f"{TENANT}:nope", f"{TENANT}:"],
)
def test_anything_else_is_not_an_id_this_code_wrote(value: str | None) -> None:
    assert parse_external_id(value) is None


# ---- the fields ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Blank:
    name: str
    page: int = 2
    x: float = 297.6378
    y: float = 420.94445
    width: float = 155.9
    height: float = 22.7


def test_the_blanks_become_percentages_of_the_a4_page() -> None:
    fields = fields_from_blanks([Blank("firma professionista"), Blank("data firma", width=85.0)])
    assert [field["type"] for field in fields] == ["SIGNATURE", "DATE"]
    assert (fields[0]["positionX"], fields[0]["positionY"]) == (50.0, 50.0)
    assert fields[0]["page"] == 2
    assert fields[1]["fieldMeta"] == {"type": "date", "fontSize": 9, "textAlign": "left"}


def test_a_blank_nobody_fills_and_a_document_with_no_signature_are_refused() -> None:
    with pytest.raises(ValidationFailed, match="firma cliente"):
        fields_from_blanks([Blank("firma professionista"), Blank("firma cliente")])
    with pytest.raises(ValidationFailed, match="firma del freelance"):
        fields_from_blanks([Blank("data firma")])


# ---- the client ---------------------------------------------------------------------------------


def test_the_client_sends_its_token_and_reads_a_refusal_by_its_message() -> None:
    fake = FakeDocumenso()
    stranger = DocumensoClient(BASE, "wrong-token", http=fake)
    with pytest.raises(DocumensoFailed) as refused:
        stranger.ping()
    assert (
        refused.value.message
        == "Documenso ha rifiutato la richiesta: Invalid session or API token."
    )
    # The stack trace Documenso's error body carries never reaches the sentence.
    assert "stack" not in refused.value.message and "node_modules" not in refused.value.message
    fake.client().ping()
    assert {call["Authorization"] for call in fake.headers} == {"wrong-token", TOKEN}


def test_no_answer_at_all_is_an_outage_and_not_a_refusal() -> None:
    def dead(*args: object, **kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return NETWORK_ERROR_STATUS, b'{"error": {"message": "no route"}}', {}

    with pytest.raises(DocumensoFailed, match="non risponde"):
        DocumensoClient(BASE, TOKEN, http=dead).ping()  # type: ignore[arg-type]

    def raising(*args: object, **kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        raise OSError("connection refused")

    with pytest.raises(DocumensoFailed, match="non risponde"):
        DocumensoClient(BASE, TOKEN, http=raising).ping()  # type: ignore[arg-type]


def test_a_200_that_is_not_documenso_does_not_pass_as_reachable() -> None:
    def portal(*args: object, **kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return 200, b"<html>log in</html>", {}

    with pytest.raises(DocumensoFailed, match="non riconosco"):
        DocumensoClient(BASE, TOKEN, http=portal).ping()  # type: ignore[arg-type]


def test_a_redirect_is_a_refusal_the_token_never_follows() -> None:
    def moved(*args: object, **kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return 301, b"", {"location": "https://elsewhere.test"}

    with pytest.raises(DocumensoFailed, match="rifiutato"):
        DocumensoClient(BASE, TOKEN, http=moved).ping()  # type: ignore[arg-type]


def test_a_signed_copy_must_be_a_pdf() -> None:
    def html(*args: object, **kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        return 200, b"<html>", {}

    with pytest.raises(DocumensoFailed, match="not a PDF|non riconosco"):
        DocumensoClient(BASE, TOKEN, http=html).download_signed("envelope_item_1")  # type: ignore[arg-type]


def test_an_envelope_is_created_for_one_signer_with_no_mail_of_its_own() -> None:
    fake = FakeDocumenso()
    client = fake.client()
    envelope_id = client.create(
        title="Lettera",
        external_id=external_id(TENANT, DOCUMENT),
        filename='lettera"\r\nX-Evil: 1.pdf',
        pdf=b"%PDF-1.7\nx",
        signer_email="ada@example.com",
        signer_name="Ada",
        fields=[{"type": "SIGNATURE", "page": 1}],
    )
    (payload,) = fake.created()
    assert payload["externalId"] == f"{TENANT}:{DOCUMENT}"
    assert [r["role"] for r in payload["recipients"]] == ["SIGNER"]
    assert payload["meta"]["distributionMethod"] == "NONE"
    # No quote or line break can add a header or a part of its own to the upload.
    assert "X-Evil" in fake.envelopes[envelope_id].filename
    assert '"' not in fake.envelopes[envelope_id].filename
    assert "\n" not in fake.envelopes[envelope_id].filename
    assert client.distribute(envelope_id).endswith("/sign/token0001")
    got = client.get(envelope_id)
    assert (got.status, got.external_id) == ("PENDING", f"{TENANT}:{DOCUMENT}")


# ---- outcomes ------------------------------------------------------------------------------------


def test_only_three_webhook_events_move_a_document() -> None:
    fake = FakeDocumenso()
    envelope_id = fake.client().create(
        title="T",
        external_id=external_id(None, DOCUMENT),
        filename="t.pdf",
        pdf=b"%PDF-1.7",
        signer_email="a@b.it",
        signer_name="A",
        fields=[{"type": "SIGNATURE", "page": 1}],
    )
    for event in ("DOCUMENT_CREATED", "DOCUMENT_SENT", "DOCUMENT_OPENED", "DOCUMENT_SIGNED"):
        body = WebhookBody.model_validate(fake.webhook(envelope_id, event))
        assert outcome_from_webhook(body) is None
    fake.reject(envelope_id, "no")
    rejected = outcome_from_webhook(
        WebhookBody.model_validate(fake.webhook(envelope_id, "DOCUMENT_REJECTED"))
    )
    assert rejected is not None and (rejected.kind, rejected.reason) == (REJECTED, "no")
    body = WebhookBody.model_validate(fake.webhook(envelope_id, "DOCUMENT_COMPLETED"))
    assert body.payload.external_id == external_id(None, DOCUMENT)


def test_a_refresh_reads_the_same_outcomes_from_the_envelope_status() -> None:
    envelope = Envelope("e", "PENDING", "i", None, None, None)
    assert outcome_from_envelope(envelope) is None
    done = Envelope("e", "COMPLETED", "i", None, None, None)
    outcome = outcome_from_envelope(done)
    assert outcome is not None and outcome.kind == COMPLETED


def test_a_webhook_envelope_id_must_be_a_short_string() -> None:
    with pytest.raises(ValidationError):
        WebhookBody.model_validate({"event": "DOCUMENT_COMPLETED", "payload": {"envelopeId": ""}})


# ---- the settings --------------------------------------------------------------------------------


def test_signing_is_off_until_both_the_url_and_the_token_are_set() -> None:
    assert client_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    only_url = Settings(documenso_url="http://documenso:3000", _env_file=None)  # type: ignore[call-arg]
    assert client_from_settings(only_url) is None
    only_token = Settings(documenso_api_token=TOKEN, _env_file=None)  # type: ignore[call-arg]
    assert client_from_settings(only_token) is None
    both = Settings(
        documenso_url="http://documenso:3000/",
        documenso_api_token=TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )
    client = client_from_settings(both)
    assert client is not None and client.api == "http://documenso:3000/api/v2"


def test_the_secret_and_the_address_are_checked_at_boot_when_they_are_set() -> None:
    with pytest.raises(ValidationError, match="documenso_webhook_secret"):
        Settings(documenso_webhook_secret="short", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="documenso_url"):
        Settings(documenso_url="documenso:3000", _env_file=None)  # type: ignore[call-arg]
    # Empty is legal and is what the compose file forwards by default.
    empty = Settings(
        documenso_url="",
        documenso_api_token="",
        documenso_webhook_secret="",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert (empty.documenso_url, empty.documenso_api_token, empty.documenso_webhook_secret) == (
        "",
        "",
        "",
    )


def test_the_credentials_never_reach_a_repr() -> None:
    shown = repr(
        Settings(  # type: ignore[call-arg]
            documenso_api_token="api_secret_value",
            documenso_webhook_secret="w" * 40,
            _env_file=None,
        )
    )
    assert "api_secret_value" not in shown and "w" * 40 not in shown


# ---- the commands --------------------------------------------------------------------------------


def test_documenso_check_says_whether_the_instance_answers_and_the_token_opens_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(documenso_url=BASE, documenso_api_token=TOKEN, _env_file=None)  # type: ignore[call-arg]
    fake = FakeDocumenso()
    assert documenso_check(settings, fake) == 0
    assert "answers" in capsys.readouterr().out
    wrong = Settings(documenso_url=BASE, documenso_api_token="nope", _env_file=None)  # type: ignore[call-arg]
    assert documenso_check(wrong, fake) == 1
    err = capsys.readouterr().err
    assert "rifiutato" in err and "nope" not in err
    assert documenso_check(Settings(_env_file=None), fake) == 1  # type: ignore[call-arg]
    assert "PIGROCRM_DOCUMENSO_URL" in capsys.readouterr().err


def test_the_check_commands_exist_in_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["documenso-check"]) == 1
    assert "signing is off" in capsys.readouterr().err


@needs_binaries
def test_contracts_check_typesets_every_text_and_finds_the_freelancers_blanks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert contracts_check(Settings(_env_file=None)) == 0  # type: ignore[call-arg]
    out = capsys.readouterr().out
    for name in (
        "framework-agreement",
        "framework-agreement-occasional",
        "engagement-letter",
        "engagement-letter-occasional",
    ):
        assert f"{name}: " in out
    assert "signing blanks" in out
    assert json.dumps(out).count("freelancer-nda") == 0


# ---- which space an envelope belongs to --------------------------------------------------------


@pytest.fixture(scope="module")
def registry(db_engine: Engine) -> Iterator[Engine]:
    settings = Settings(
        database_url=db_engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
    )
    engine = ensure_tenants_database(settings)
    yield engine
    engine.dispose()


@pytest.fixture
def registry_session(registry: Engine) -> Iterator[Session]:
    session = session_factory(registry)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _space(session: Session, slug: str) -> Tenant:
    tenant = Tenant(slug=slug, db_name=f"pigro_t_{slug.replace('-', '_')}", owner_email="a@b.it")
    session.add(tenant)
    session.flush()
    return tenant


def _settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_an_envelope_goes_to_the_space_its_external_id_names(registry_session: Session) -> None:
    uno = _space(registry_session, f"rotta-uno-{uuid4().hex[:6]}")
    due = _space(registry_session, f"rotta-due-{uuid4().hex[:6]}")
    settings = _settings(root_slug="radice")
    for tenant in (uno, due):
        route = route_envelope(
            registry_session, settings, parse_external_id(external_id(tenant.id, DOCUMENT))
        )
        assert route is not None
        assert (route.slug, route.tenant_id, route.document_id, route.bare) == (
            tenant.slug,
            tenant.id,
            DOCUMENT,
            False,
        )
    rooted = route_envelope(
        registry_session, settings, parse_external_id(external_id(None, DOCUMENT))
    )
    assert rooted is not None and rooted.slug is None and rooted.tenant_id is None


def test_an_envelope_this_installation_did_not_write_goes_nowhere(
    registry_session: Session,
) -> None:
    settings = _settings(root_slug="radice")
    assert route_envelope(registry_session, settings, None) is None
    gone = parse_external_id(external_id(uuid4(), DOCUMENT))
    assert route_envelope(registry_session, settings, gone) is None


def test_a_bare_document_id_resolves_only_in_the_leads_space(registry_session: Session) -> None:
    leads = _space(registry_session, f"rotta-lead-{uuid4().hex[:6]}")
    bare = parse_external_id(str(DOCUMENT))
    # No leads space named: the hub's envelope is not this installation's.
    assert route_envelope(registry_session, _settings(), bare) is None
    # A name no space has: nothing to resolve it in.
    assert route_envelope(registry_session, _settings(leads_space="nessuno-qui"), bare) is None
    route = route_envelope(registry_session, _settings(leads_space=leads.slug), bare)
    assert route is not None and (route.slug, route.bare) == (leads.slug, True)
    # The root's own name is the root, whose database is the leads space.
    rooted = route_envelope(
        registry_session, _settings(leads_space="radice", root_slug="radice"), bare
    )
    assert rooted is not None and rooted.slug is None and rooted.bare


def test_the_tenant_id_an_envelope_is_written_with(registry_session: Session) -> None:
    tenant = _space(registry_session, f"rotta-id-{uuid4().hex[:6]}")
    settings = _settings(root_slug="radice")
    assert tenant_id_for(registry_session, settings, tenant.slug) == tenant.id
    assert tenant_id_for(registry_session, settings, None) is None
    assert tenant_id_for(registry_session, settings, "radice") is None
