"""`SigningRecovery.sweep` (REB-391, REB-431) and the link to Pigro once a letter is signed
(REB-499). Helpers: `signing_fixtures`."""

from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fakes_pigro import DEAL_URL, PIGRO, RecordedPigro, linked_body
from match_fixtures import (
    _setup,
)
from signing_fixtures import (
    NOW,
    SIGNED_AT,
    FailingEngagements,
    RaisingEngagements,
    SpyEngagements,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _letter_out,
    _match_row,
    _sent,
    _signing,
    _webhook,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy.orm import Session

from rebase_core.mail import RecordingSender
from rebase_core.models import Match
from rebase_core.pigro_door import CAUSE_REFUSED, CAUSE_TIMEOUT
from rebase_core.signing_recovery import SweepResult


# ---- the sweep (REB-391) ------------------------------------------------------------------
def test_sweep_finishes_a_signature_a_crashed_background_task_left_undone(
    clean: Session,
) -> None:
    """The webhook's own `apply` committed, but left the document `inviato` for
    `finish` to confirm (REB-431); its background `finish` never ran (the process died
    first). `sweep` finds the document through `_to_finish` and runs `finish` on it:
    confirmed `firmato`, its sealed copy stored and mailed, and the letter that waited
    for this framework agreement released."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    assert _framework_of(clean, freelancer_id).stato == "inviato"
    assert _framework_of(clean, freelancer_id).signed_pdf is None
    assert _letter_of(clean, match.id).stato == "in_attesa"

    result = signing.recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0)
    quadro = _framework_of(clean, freelancer_id)
    assert quadro.stato == "firmato"
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    assert (quadro.signed_copy_to_freelancer_at, quadro.signed_copy_to_rebase_at) == (NOW, NOW)
    assert _letter_of(clean, match.id).stato == "inviato"


def test_sweep_releases_a_waiting_letter_of_an_already_finished_framework(
    clean: Session,
) -> None:
    """The framework agreement's own copy is already stored and mailed (`finish` ran for
    it already); only the release of a letter that started waiting afterwards was lost
    -- the shape a release `finish` itself retries
    (`test_a_release_documenso_refuses_waits_for_the_next_finish`) never even ran for,
    because nothing ever called `finish` on this framework agreement again."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.recovery.finish(signed)
    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    assert (quadro.signed_copy_to_freelancer_at, quadro.signed_copy_to_rebase_at) == (NOW, NOW)
    # A letter `in_attesa` on a match `in_firma`, its framework agreement already active:
    # the exact row shape `_release_letters` looks for, forged directly (as
    # `test_a_letter_whose_framework_was_refused_gets_a_new_one_when_its_match_is_sent`
    # forges its own scenario) rather than reached through a second real send, which
    # would dispatch it immediately since the framework is active by then.
    second = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    second_letter = _letter_of(clean, second.id)
    second_letter.stato = "in_attesa"
    second_match = clean.get(Match, second.id)
    assert second_match is not None
    second_match.stato = "in_firma"
    clean.commit()

    result = signing.recovery.sweep()

    # 1, not 2: the first match's own letter, released by the `finish` above, is
    # itself `inviato` with an envelope still pending on Documenso, so REB-431's wider
    # `_to_finish` visits it too, but nothing moves for it, and REB-433 counts only
    # what actually moved.
    assert result == SweepResult(touched=1, unconfirmed=0)
    assert _letter_of(clean, second.id).stato == "inviato"


def test_sweep_with_nothing_left_to_do_returns_zero(clean: Session) -> None:
    """A document still `inviato`, not yet signed on Documenso, is in `_to_finish` now
    too (REB-431): `finish` confirms nothing and moves nothing, so it still counts for
    zero (REB-433); still `PENDING` on Documenso is not a failed confirmation either, so
    `unconfirmed` stays zero too."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)

    assert _signing(clean, renderer, fake, sender).recovery.sweep() == SweepResult(
        touched=0, unconfirmed=0
    )
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_sweep_does_not_keep_counting_a_document_stuck_on_a_failing_download(
    clean: Session,
) -> None:
    """REB-433: `rebase contracts-sweep` must not print one more «documento ripreso»
    for a document that keeps failing the same way every ten minutes -- only a sweep
    that actually moves something (here, the confirmation to `firmato`, on the first
    run) counts."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    fake.fail("download", 500, "Internal server error")

    first = signing.recovery.sweep()

    # the confirmation to `firmato` itself moved something, and it succeeded, so
    # unconfirmed stays zero even though the download that follows keeps failing.
    assert first == SweepResult(touched=1, unconfirmed=0)
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.signed_pdf) == ("firmato", None)

    fake.fail("download", 500, "Internal server error")

    second = signing.recovery.sweep()

    # already confirmed; only the still-failing download was tried, and that is not a
    # confirmation failure, so unconfirmed stays zero too.
    assert second == SweepResult(touched=0, unconfirmed=0)
    assert _framework_of(clean, freelancer_id).signed_pdf is None


def test_sweep_counts_an_unreachable_documenso_as_unconfirmed_not_touched(
    clean: Session,
) -> None:
    """REB-431: Documenso not answering at all during the confirmation still leaves the
    document `inviato`, as before, but the sweep no longer looks the same as one with
    nothing to do: the attempt counts as `unconfirmed`, not `touched`, so a dead network
    shows up instead of «0 documenti ripresi» printed every ten minutes with nothing to
    say why."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    fake.down("get")

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=0, unconfirmed=1)
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_sweep_counts_a_refused_token_as_unconfirmed_not_touched(clean: Session) -> None:
    """REB-431: an expired, revoked or wrong token, or an envelope Documenso itself no
    longer knows (404), refuses the same GET the confirmation reads with an HTTP error --
    counted the same way as an unreachable instance, never silently as nothing to do."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    fake.fail("get", 401, "Invalid session or API token.")

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=0, unconfirmed=1)
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_sweep_on_an_environment_with_no_documenso_configured_counts_nothing(
    clean: Session,
) -> None:
    """REB-431: a preview with signing off is a routine state, not a failure -- it must
    not inflate `unconfirmed` merely because every document `_to_finish` finds stays
    `inviato`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)

    result = _signing(clean, renderer, None, sender).recovery.sweep()

    assert result == SweepResult(touched=0, unconfirmed=0)


def test_confirm_completion_marks_the_match_da_collegare(clean: Session) -> None:
    """The commit that turns a match `attivo` also marks its link to Pigro due, so the
    sweep finds it whether or not anything links it now."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)

    assert _signing(clean, renderer, fake, sender).recovery._confirm_completion(letter.id) is True

    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato, match.pigro_attempted_at) == (
        "attivo",
        "da_collegare",
        None,
    )


def test_finish_links_a_match_that_just_turned_active(clean: Session) -> None:
    """The webhook's `finish` (and «Aggiorna stato») links the match its signature turned
    active, once; a second `finish` of the same letter asks the CRM nothing more."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    http = RecordedPigro([(201, linked_body())])
    engagements = SpyEngagements(clean, http)
    signing = _signing(clean, renderer, fake, sender, engagements=engagements)

    assert signing.recovery.finish(letter.id) is True

    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato, match.pigro_url) == ("attivo", "collegato", DEAL_URL)
    [call] = http.calls
    assert call[:2] == ("PUT", f"{PIGRO}/api/rebase/engagements/{match_id}")
    # The signature's own steps ran all the same: the sealed copy stored and mailed.
    stored = _letter_of(clean, match_id)
    assert (stored.stato, stored.signed_pdf) == ("firmato", fake.signed_pdf(envelope))

    signing.recovery.finish(letter.id)

    assert engagements.asked == [match_id]
    assert len(http.calls) == 1


def test_finish_does_not_link_a_refused_letter(clean: Session) -> None:
    """`_confirm_completion` answers `True` for a rejection too: the match's state, read
    again, decides, and a match still `in_firma` is never handed to `link`."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.reject(envelope, "Il compenso non è quello concordato")
    http = RecordedPigro([(201, linked_body())])
    engagements = SpyEngagements(clean, http)

    assert _signing(clean, renderer, fake, sender, engagements=engagements).recovery.finish(
        letter.id
    )

    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato) == ("in_firma", None)
    assert _letter_of(clean, match_id).stato == "annullato"
    assert engagements.asked == []
    assert http.calls == []


def test_refresh_does_not_link(clean: Session) -> None:
    """«Aggiorna stato» (and the MCP `refresh_contract`) is an admin waiting on the page:
    it applies the signature and turns the match active, but never waits on the CRM to
    open a space. The match stays `da_collegare` for the sweep or «Riprova»."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    http = RecordedPigro([(201, linked_body())])
    engagements = SpyEngagements(clean, http)

    read = _signing(clean, renderer, fake, sender, engagements=engagements).admin.refresh(letter.id)

    assert read.stato == "firmato"
    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato) == ("attivo", "da_collegare")
    assert engagements.asked == []
    assert http.calls == []


def test_finish_survives_any_exception_from_the_link(clean: Session) -> None:
    """The link is `finish`'s last step and nothing depends on it: even an error that is
    not the domain's is logged and left for the sweep, never raised past a signature
    that is already done."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    engagements = RaisingEngagements(clean, RecordedPigro([(201, linked_body())]))

    assert _signing(clean, renderer, fake, sender, engagements=engagements).recovery.finish(
        letter.id
    )

    assert engagements.asked == [match_id]
    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato) == ("attivo", "da_collegare")
    assert _letter_of(clean, match_id).stato == "firmato"


def test_finish_leaves_a_link_that_fails_for_the_sweep(clean: Session) -> None:
    """A link that raises is logged and left for the sweep, as a signed copy that is not
    stored yet is: the signature is done, and the match waits `da_collegare`."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    engagements = FailingEngagements(clean, RecordedPigro([(201, linked_body())]))

    assert _signing(clean, renderer, fake, sender, engagements=engagements).recovery.finish(
        letter.id
    )

    assert engagements.asked == [match_id]
    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato) == ("attivo", "da_collegare")
    assert _letter_of(clean, match_id).signed_pdf == fake.signed_pdf(envelope)


def test_sweep_retries_errore_matches(clean: Session) -> None:
    """A match whose last attempt failed is asked again by the sweep, after the
    documents, and counted as linked."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    _signing(clean, renderer, fake, sender).recovery.finish(letter.id)
    match = _match_row(clean, match_id)
    match.pigro_stato, match.pigro_errore = "errore", CAUSE_TIMEOUT
    clean.commit()
    http = RecordedPigro([(201, linked_body())])

    result = _signing(
        clean, renderer, fake, sender, engagements=SpyEngagements(clean, http)
    ).recovery.sweep()

    assert result == SweepResult(touched=0, unconfirmed=0, linked=1, link_failed=0)
    match = _match_row(clean, match_id)
    assert (match.pigro_stato, match.pigro_errore, match.pigro_url) == (
        "collegato",
        None,
        DEAL_URL,
    )


def test_sweep_links_a_signature_it_confirms_once_and_counts_a_failure(clean: Session) -> None:
    """The webhook never came: the sweep confirms the letter, and the match it turned
    active is asked for by the round after the documents, once in the run. A CRM that
    does not answer is one attempt and one failure, not two."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, _letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)
    http = RecordedPigro([ConnectionRefusedError("refused")])
    engagements = SpyEngagements(clean, http)

    result = _signing(clean, renderer, fake, sender, engagements=engagements).recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0, linked=0, link_failed=1)
    assert engagements.asked == [match_id]
    assert len(http.calls) == 1
    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato, match.pigro_errore) == (
        "attivo",
        "errore",
        CAUSE_REFUSED,
    )


def test_sweep_without_engagements_links_nothing(clean: Session) -> None:
    """A `SigningService` built without the engagement service links nothing, as one
    without Documenso signs nothing: the match it turns active waits `da_collegare`."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match_id, _letter, envelope = _letter_out(clean, renderer, fake, sender)
    fake.sign(envelope, SIGNED_AT)

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0, linked=0, link_failed=0)
    match = _match_row(clean, match_id)
    assert (match.stato, match.pigro_stato) == ("attivo", "da_collegare")
