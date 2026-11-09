"""The matches and the contracts as actions (REB-478) and the Pigro hours report (REB-501):
each tool runs the guard the admin API runs, then the service it calls, with the calling
admin as the actor, so the trail reads the same whichever door was used."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import MatchCreate
from rebase_core.contracts.fields import signer_data
from rebase_core.errors import ValidationFailed
from rebase_core.match_rows import ENTITY
from rebase_core.match_words import send_report_sentence
from rebase_core.matches import MatchService, require_live_document, require_live_match
from rebase_core.pigro_door import PigroUnavailable
from rebase_core.regimes import OCCASIONAL, Regime
from rebase_core.signing import SigningService
from rebase_mcp.tools_common import (
    Deps,
    EngagementsFactory,
    day,
    field_refusal,
    not_configured,
    to_uuid,
)

# `MatchDrafts.proposal` names the letter's fields as the admin API's body does
# (`letter.fee`); the tools take them as `terms`, and a refusal says so.
LETTER_PART, CONDITIONS_PART = "letter.", "terms."


def _proposal(
    service: MatchService,
    freelancer_id: UUID,
    company_id: UUID,
    customer: dict[str, Any] | None,
    terms: dict[str, Any] | None,
    match_id: UUID | None = None,
    regime: Regime | None = None,
) -> MatchCreate:
    """The hub's proposal with the given fields laid over it, a refusal naming a letter's
    field by the parameter it came in (`terms.fee`). `regime` `None` takes the
    letter type the hub proposes (REB-687)."""
    try:
        return service.drafts.proposal(freelancer_id, company_id, customer, terms, match_id, regime)
    except ValidationFailed as exc:
        field = str(exc.details["field"])
        if not field.startswith(LETTER_PART):
            raise
        raise ValidationFailed(
            ENTITY, CONDITIONS_PART + field.removeprefix(LETTER_PART), str(exc.details["reason"])
        ) from exc


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""
    hub = urlsplit(deps.settings.hub_url) if deps.settings is not None else None
    # The PDFs are links an admin opens with their own session, never bytes in an
    # agent's context: the API's origin is the SPA's (`REBASE_HUB_URL`), or none at all.
    api_origin = f"{hub.scheme}://{hub.netloc}" if hub is not None and hub.netloc else ""

    def _document_links(document: dict[str, Any]) -> None:
        path = f"/api/hub/contract-documents/{document['id']}/pdf"
        document["pdf_url"] = f"{api_origin}{path}"
        if document.get("has_pdf_signed"):
            document["pdf_signed_url"] = f"{api_origin}{path}?signed=true"

    def _on_match(match_id: str, action: Callable[[Session, UUID], BaseModel]) -> dict[str, Any]:
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            return action(session, key).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body["letter"])
        return body

    def _on_document(
        document_id: str, action: Callable[[SigningService, UUID], BaseModel]
    ) -> dict[str, Any]:
        key = to_uuid(document_id, "document_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_document(session, key)
            return action(deps.contracts(session), key).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body)
        return body

    def _require_engagements() -> EngagementsFactory:
        """The sentence «Consuntivo» shows when this environment has no token for the
        CRM's engagements door: without a token an admin's action is refused plainly,
        unlike the sweep's own quiet wait."""
        if (
            deps.engagements is None
            or deps.settings is None
            or not deps.settings.pigro_engagements_token
        ):
            raise not_configured("The Pigro report", "REBASE_PIGRO_ENGAGEMENTS_TOKEN")
        return deps.engagements

    @mcp.tool()
    def list_matches(freelancer_id: str) -> dict[str, Any]:
        """The matches and contracts of a freelancer, by card id, as the «Match e contratti» page
        shows them: `framework` is the framework agreement (the active one, otherwise the last
        one still to be sent or sent for signature, even if later cancelled or refused; a
        draft cancelled before it was sent does not count) with its status, signing date, next
        renewal, last day for notice and text version; `frameworks` lists them all;
        `occasional_framework` is the framework agreement for occasional services, with the
        same rule (a freelancer can have one per type); `matches` are the matches, newest
        first, each with the company, the status and the engagement letter with its number.
        Every document carries `pdf_url`, the link to the PDF to open with admin access: never
        the bytes, never the tax data. Every match and every document also carries
        `situation`, where it stands, and `next_action` and `other_actions`, the steps ahead
        of it. On a match `send` is `send_match_for_signature`, `cancel` is `cancel_match` and
        `close` is `close_match`, while `resend_email` and `status_refresh` act on its letter
        (`letter.id`) with `resend_signing_mail` and `refresh_contract`; on a framework
        agreement the same two tools take its id, `cancel` is `cancel_contract` and
        `record_notice` is `record_notice`. Read-only; a new match is prepared with
        `preview_match` and saved with `create_match`."""
        body = deps.run(
            lambda s: deps.reading(s).for_freelancer(to_uuid(freelancer_id, "freelancer_id"))
        )
        body.pop("tax", None)
        for document in (body["framework"], body["occasional_framework"], *body["frameworks"]):
            if document is not None:
                _document_links(document)
        for match in body["matches"]:
            _document_links(match["letter"])
        return body

    @mcp.tool()
    def get_match(match_id: str) -> dict[str, Any]:
        """One match, by id: the company and the requested role, the customer data as the letter
        prints it, the status (draft, awaiting_signature, active, concluded, cancelled), the
        engagement letter with number, status and `label` («Lettera di incarico» or «Lettera
        di incarico occasionale»), and in `framework` the freelancer's framework agreement of
        the same type as the letter. Every document carries `pdf_url`, never the bytes. The
        match and the documents carry `situation`, `next_action` and `other_actions`, and
        `list_matches` says which tool performs each action. Read-only."""
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            # A soft-deleted freelancer's match reads as «match not found», the answer
            # the admin API gives (REB-417), rather than `for_freelancer` refusing with
            # the freelancer's own message.
            freelancer_id = require_live_match(session, key).freelancer_id
            service = deps.reading(session)
            body = service.get(key).model_dump(mode="json")
            page = service.for_freelancer(freelancer_id)
            # The framework agreement of the letter's own regime (REB-687).
            framework = (
                page.occasional_framework
                if body["letter"]["regime"] == OCCASIONAL
                else page.framework
            )
            body["framework"] = framework.model_dump(mode="json") if framework is not None else None
            return body

        body = deps.call(call)
        _document_links(body["letter"])
        if body["framework"] is not None:
            _document_links(body["framework"])
        return body

    @mcp.tool()
    def preview_match(
        freelancer_id: str,
        company_id: str,
        customer: dict[str, Any] | None = None,
        terms: dict[str, Any] | None = None,
        regime: Regime | None = None,
    ) -> dict[str, Any]:
        """What `create_match` would do with this data, without writing anything: the sentences
        of step 3 of «Crea match». `recap` is the engagement letter in three or four
        sentences, `what_happens` which document goes out first, `required_framework`
        whether a new framework agreement is needed, `missing_tax_data` whether the
        freelancer's tax data is missing (save it with `set_freelancer_tax_data`), `regime`
        the letter type. `regime` is `vat_registered` (engagement letter, for those who work
        with a VAT number) or `occasional` (occasional engagement letter, occasional
        service); if omitted, what the hub proposes: occasional if the tax data is saved
        without a VAT number. A VAT-registered letter without a saved VAT number is refused.
        It starts from the hub's proposal: the customer as the last match of the same company
        named it (otherwise just the company name), the letter from the request, the card and
        rebase's conditions. `customer` and `terms` change it field by field, with the API's
        names: for the customer `customer_legal_name`, `customer_vat_number`,
        `customer_registered_office`; for the conditions the letter's fields, such as `role`,
        `activities`, `start_date` and `end_date` (`YYYY-MM-DD`), `commitment`, `location`,
        `fee` (excluding VAT in the VAT-registered letter, gross in the occasional one; also
        in Italian format: «1.500», «1.234,50»), `unit`, `payment_days`, `month_end`,
        `collection_days` (optional, 1 to 60: pay that many days after the client has paid
        the matching invoice, and in any case within 60 days of the freelancer's invoice or
        receipt; it replaces the fixed term the letter prints). An
        omitted field stays as the hub proposes it, `null` clears it. The answer also
        includes `customer` and `terms` as they would be written. An unknown field, or a
        required one left empty, is refused by name. Read-only."""
        freelancer, company = (
            to_uuid(freelancer_id, "freelancer_id"),
            to_uuid(company_id, "company_id"),
        )

        def call(session: Session) -> dict[str, Any]:
            service = MatchService(session)
            proposal = _proposal(service, freelancer, company, customer, terms, regime=regime)
            body = service.drafts.check(freelancer, proposal).model_dump(mode="json")
            body["customer"] = proposal.customer.model_dump(mode="json")
            body["terms"] = proposal.letter.model_dump(mode="json")
            return body

        return deps.call(call)

    @mcp.tool()
    def create_match(
        freelancer_id: str,
        company_id: str,
        customer: dict[str, Any] | None = None,
        terms: dict[str, Any] | None = None,
        match_id: str | None = None,
        expected_days: int | None = None,
        regime: Regime | None = None,
    ) -> dict[str, Any]:
        """Saves the match as a draft, like «Salva senza inviare» at step 3 of «Crea match»: the
        engagement letter with its number and, if the freelancer has no active framework
        agreement nor one already out for signature of the same type as the letter, a new
        framework agreement of that type. Nothing is sent: for signing there is
        `send_match_for_signature`. `customer`, `terms` and `regime` work as in
        `preview_match`, which you should call first to reread the sentences. The freelancer's tax
        data is required (`set_freelancer_tax_data`). `expected_days`, the days expected for
        the Pigro report (between 1 and 366, optional), lives on the match and not on the
        letter: its text stays that of «Impegno». `match_id`, a UUID chosen by the caller,
        makes retrying safe: with the same id the already written match comes back and not a
        second one, but only if the proposal is unchanged. If the hub's proposal has changed
        in the meantime (the request, the card or the company's last match), the attempt is
        refused: to retry, explicitly pass the first-time values in `customer` and `terms`.
        The answer is the match with `situation`, `next_action` and the letter with
        `pdf_url`, in the name of the admin behind the token."""
        freelancer, company = (
            to_uuid(freelancer_id, "freelancer_id"),
            to_uuid(company_id, "company_id"),
        )
        key = to_uuid(match_id, "match_id") if match_id else None

        def call(session: Session) -> dict[str, Any]:
            service = MatchService(session, deps.renderer, signer_data(deps.signer_json))
            proposal = _proposal(service, freelancer, company, customer, terms, key, regime)
            try:
                proposal = MatchCreate.model_validate(
                    {**proposal.model_dump(mode="json"), "expected_days": expected_days}
                )
            except PydanticValidationError as exc:
                raise field_refusal(exc) from None
            return service.create(freelancer, proposal, deps.admin().id).model_dump(mode="json")

        body = deps.call(call)
        _document_links(body["letter"])
        return body

    @mcp.tool()
    def send_match_for_signature(match_id: str) -> dict[str, Any]:
        """«Invia per la firma»: the document that can go out now goes to Documenso and the
        freelancer receives the email with the link to sign it. If there is no active
        framework agreement, that one goes first, and the engagement letter goes out by itself
        after it is signed. `message` says what went out, in the page's words; `sent` is
        `framework`, `letter` or `null` (the letter waits for a framework agreement already
        out for signature), `mail_sent` whether the email went out (otherwise
        `resend_signing_mail`). There is no going back: the document reaches the freelancer,
        and from there it can only be cancelled. Refused for a text still in draft or a match
        that has nothing left to send."""
        key = to_uuid(match_id, "match_id")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            report = deps.contracts(session).send_match(key, deps.admin().id)
            return {**report.model_dump(mode="json"), "message": send_report_sentence(report)}

        body = deps.call(call)
        _document_links(body["match"]["letter"])
        return body

    @mcp.tool()
    def resend_signing_mail(document_id: str) -> dict[str, Any]:
        """«Reinvia email»: the email with the signing link again, the same as before, for a
        document still waiting for the freelancer's signature. The document id is in
        `list_matches` or `get_match`. The answer is the document with `pdf_url`."""
        return _on_document(
            document_id, lambda service, key: service.admin.resend_mail(key, deps.admin().id)
        )

    @mcp.tool()
    def refresh_contract(document_id: str) -> dict[str, Any]:
        """«Aggiorna stato»: asks Documenso where the document stands and applies it as its
        notification would, then does what a signature leaves to do (saves and sends the
        signed copy, releases the letters that were waiting for this framework agreement).
        Needed when a notification did not arrive. The answer is the document with
        `situation`, `pdf_url` and, when there is a signed copy, `pdf_signed_url`."""
        return _on_document(document_id, lambda service, key: service.admin.refresh(key))

    @mcp.tool()
    def cancel_contract(document_id: str) -> dict[str, Any]:
        """«Annulla» on a framework agreement not yet signed: if it had gone out it is also
        cancelled on Documenso, and the freelancer receives an email saying the link is no
        longer valid. An engagement letter is cancelled with its match (`cancel_match`). There
        is no going back: the letters that were waiting for it stay waiting, and «Invia per la
        firma» on their match writes a new one."""
        return _on_document(
            document_id, lambda service, key: service.admin.cancel_document(key, deps.admin().id)
        )

    @mcp.tool()
    def record_notice(document_id: str) -> dict[str, Any]:
        """«Registra disdetta» on an active framework agreement, for a notice of termination or
        withdrawal: from now on the freelancer has no active framework agreement, and their
        next match writes a new one. There is no going back."""
        return _on_document(
            document_id, lambda service, key: service.admin.record_notice(key, deps.admin().id)
        )

    @mcp.tool()
    def cancel_match(match_id: str) -> dict[str, Any]:
        """«Annulla» on a draft match or one out for signature: the match and its engagement
        letter become cancelled, and the letter's number is not reused. A letter already sent
        is also cancelled on Documenso: the freelancer receives an email and their link stops
        working. The framework agreement stays as it is. There is no going back."""
        return _on_match(
            match_id,
            lambda session, key: deps.contracts(session).admin.cancel_match(key, deps.admin().id),
        )

    @mcp.tool()
    def close_match(match_id: str) -> dict[str, Any]:
        """«Chiudi match» on an active match: the engagement ends and the match becomes
        concluded. The framework agreement stays as it is. There is no going back."""
        return _on_match(
            match_id, lambda session, key: MatchService(session).close(key, deps.admin().id)
        )

    @mcp.tool()
    def link_match_to_pigro(match_id: str) -> dict[str, Any]:
        """«Riprova su Pigro»: links an active match to its deal on PigroCRM now, in the name of
        the admin behind the token, or records why it did not succeed (`error`, `rejected`
        with the CRM's sentence) so that it can be retried. Refused for a match that is not
        active or not found. It also answers with an error when Pigro is not configured in
        this environment or does not respond."""
        build_engagements = _require_engagements()
        try:
            return _on_match(
                match_id, lambda session, key: build_engagements(session).link(key, deps.admin().id)
            )
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_match_report(
        match_id: str, from_date: str | None = None, to_date: str | None = None
    ) -> dict[str, Any]:
        """The hours report of a match, like «Consuntivo» in the admin area: total hours and
        days, progress against the expected days when there are any, the per-day detail with
        each day's invoices and the hours on each, the totals per week and per month, and the
        invoices of the period. `from_date` and `to_date` (`YYYY-MM-DD`) narrow the period,
        the whole engagement by default (from the start of the letter to today). Refused for
        a match not linked to Pigro, with the sentence saying where the link stands. It also
        answers with an error when Pigro is not configured in this environment or does not
        respond. Read-only, with one exception: a deleted deal in the freelancer's space marks
        the match as rejected, until the deal comes back and «Riprova» links it again."""
        build_engagements = _require_engagements()
        key = to_uuid(match_id, "match_id")
        start = day(from_date, "from_date")
        end = day(to_date, "to_date")

        def call(session: Session) -> dict[str, Any]:
            require_live_match(session, key)
            return build_engagements(session).report(key, start, end).model_dump(mode="json")

        try:
            return deps.call(call)
        except PigroUnavailable as exc:
            raise ToolError(str(exc)) from exc
