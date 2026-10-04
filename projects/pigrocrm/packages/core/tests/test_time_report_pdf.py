"""Criterion 10, PDF half. The report is a document produced by the slice 2 pipeline,
not a string built by concatenating Typst source in Python -- which is what the previous system did
(`[ENTRIES_PLACEHOLDER]` filled by string concatenation) and is why its descriptions
arrived at clients double-escaped."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from orologio import congela
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import PermissionDenied, ValidationFailed
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.timetracking import report as report_module
from pigrocrm.core.timetracking.report import (
    TIME_REPORT_TEMPLATE_NOME,
    TimeReportService,
    parse_period,
    report_variables,
)
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="system", role="admin")
WRITER = Actor(id=None, type="user", role="collaboratore")

HOSTILE = 'Call con @mario su [fase 1] & #2 — "urgente"\nseconda riga'

ASSETS = Path(__file__).resolve().parents[1] / "src" / "pigrocrm" / "core" / "render" / "assets"


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "202603", "2026-3", "2026-03\n", ""])
def test_a_malformed_period_is_refused(bad: str) -> None:
    """`re.fullmatch`, never `re.match` with `$`: `$` matches before a trailing newline,
    so `"2026-03\\n"` would slip through a `$`-anchored pattern and reach the query as a
    value nobody validated. The standing project rule, on a pattern that touches no
    database column at all -- the habit is what protects the ones that do."""
    with pytest.raises(ValidationFailed):
        parse_period(bad)


def test_a_well_formed_period_parses() -> None:
    assert parse_period("2026-03") == (2026, 3)


def test_the_seeded_template_exists_and_names_no_freelancer(db_session: Session) -> None:
    """Criterion 10's last clause: a grep over the template sources finds no
    freelancer's name. Every identity value is `{{emittente.*}}`, read from
    `emitter_profile`."""
    from pigrocrm.core.gmail.solleciti_template import SOLLECITO_TEMPLATE_NOME
    from pigrocrm.core.templates.service import OFFERTA_TEMPLATE_NOME

    created = TemplateService(db_session).seed_defaults(ADMIN)
    # Three since ORB-171: the default offer a space is born with joined the two.
    assert [t.nome for t in created] == [
        TIME_REPORT_TEMPLATE_NOME,
        SOLLECITO_TEMPLATE_NOME,
        OFFERTA_TEMPLATE_NOME,
    ]
    assert [t.tipo for t in created] == ["rapporto_ore", "sollecito", "offerta"]
    # Idempotent, deduplicating case-insensitively on `nome` to match uq_templates_nome.
    assert TemplateService(db_session).seed_defaults(ADMIN) == []

    body = (ASSETS / "template-time-report.md").read_text(encoding="utf-8")
    header = (ASSETS / "header.typ.template").read_text(encoding="utf-8")
    for source in (body, header):
        assert "studiorossi" not in source.lower()
        assert "mariorossi" not in source.lower()
        assert not re.search(r"P\.IVA\s+\d{11}", source)
    # The carried-over layout, asserted where it is expressible as text.
    assert "0.7fr" in body
    assert "DATA" in body and "ORE" in body and "DESCRIZIONE" in body
    assert "{{#each voci}}" in body
    assert "{{totale_ore}}" in body and "{{numero_voci}}" in body


def test_seed_defaults_requires_admin(db_session: Session) -> None:
    """`create`, `update` and `deactivate`/`activate` on `TemplateService` all call
    `actor.require_admin(...)` themselves -- `seed_defaults` must too, on
    `PipelineService.seed_defaults`'s own model (see that method's docstring and
    `test_pipeline.py`'s `test_seed_defaults_requires_admin`): the admin check has to
    live on the shared service, not merely in whichever caller happens to invoke it
    first (today, only the `seed-templates` CLI command, which always passes
    `Actor.system()` -- an admin actor by construction, so this gap was latent, not
    exercised, until something non-admin calls this method directly)."""
    with pytest.raises(PermissionDenied):
        TemplateService(db_session).seed_defaults(WRITER)


def test_report_variables_carry_raw_values_and_a_finished_total(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The service hands the template finished figures and raw text: the total is
    already summed (§6 forbids arithmetic downstream) and the description is unescaped,
    because `escape_for` prepares it once, at render, for the context it lands in."""
    service = TimeEntryService(db_session)
    service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 3, 4),
            ore=Decimal("2.50"),
            descrizione=HOSTILE,
        ),
        WRITER,
    )
    service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 3, 20),
            ore=Decimal("1.25"),
            descrizione="Revisione",
        ),
        WRITER,
    )
    # An entry in the next month must not appear.
    service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 4, 1),
            ore=Decimal("8.00"),
            descrizione="Aprile",
        ),
        WRITER,
    )

    report = TimeReportService(db_session, storage=None)
    variables = report.variables_for(seeded_deal_id, "2026-03", WRITER)
    assert variables["periodo"] == "marzo 2026"
    assert variables["totale_ore"] == "3.75"
    assert variables["numero_voci"] == "2"
    assert [v["data"] for v in variables["voci"]] == ["04/03/2026", "20/03/2026"]
    assert variables["voci"][0]["descrizione"] == HOSTILE
    assert variables["voci"][0]["ore"] == "2.50"
    # `report_variables` is the pure function `variables_for` delegates the actual
    # figure-building to, once deal/customer/entries are already fetched -- imported
    # here (unused otherwise in this file) so the module surface this task's own
    # interface declares is actually exercised at import time, not merely assumed.
    assert callable(report_variables)


def test_the_pdf_renders_and_contains_the_hostile_description_verbatim(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    local_storage,
    extract_pdf_text,
) -> None:
    """Renders through Pandoc and Typst for real, then reads the text back out of the
    produced PDF. The assertion is on the round trip, because the whole point is that
    the value reaches the page as the literal text somebody typed."""
    # The brief's own sample for this test omitted the emitter profile the render
    # pipeline requires (`DocumentService._template_scope` calls
    # `AziendaService.as_template_values`, which raises `NotFound` with no
    # singleton row) -- every other slice-2 PDF test sets one up
    # (`test_documents_from_template.py`'s `setup` fixture); this one needs it too.
    AziendaService(db_session).upsert_default(
        AziendaUpsert(ragione_sociale="Studio di prova", partita_iva="01234567890"),
        ADMIN,
    )
    TemplateService(db_session).seed_defaults(ADMIN)
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 3, 4),
            ore=Decimal("2.50"),
            descrizione=HOSTILE,
        ),
        WRITER,
    )
    document = TimeReportService(db_session, local_storage).render_pdf(
        seeded_deal_id, "2026-03", WRITER
    )
    assert document.tipo == "rapporto_ore"
    assert document.deal_id == seeded_deal_id
    assert document.versione_corrente == 1

    text = extract_pdf_text(local_storage, db_session, document.id)
    # Newlines and layout break lines unpredictably in a PDF, so each fragment is
    # asserted on its own -- the escaping bug this guards against corrupts characters,
    # not line breaks.
    for fragment in ("@mario", "[fase 1]", "#2", '"urgente"', "seconda riga"):
        assert fragment in text, fragment
    assert "\\@mario" not in text and "\\[fase 1\\]" not in text
    assert "Totale ore" in text and "3,75" not in text  # the PDF prints 2.50 for one entry


def test_the_compilation_date_is_italys_own_day_not_the_processs(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`oggi` is the date printed at the head of the timesheet the client receives next
    to the invoice, so it is a date in the issuer's calendar -- `clock.py`'s rule, and
    the same rule `_italian_date` already states for every row.

    Frozen at 00:30 on 1 January in Rome, which a process running in UTC (the API image
    does) still reads as 31 December. The entries are written before the freeze, because
    what is under test is the compilation date and not the rows.
    """
    service = TimeEntryService(db_session)
    service.create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2025, 12, 3),
            ore=Decimal("2.00"),
            descrizione="Dicembre",
        ),
        WRITER,
    )

    congela(monkeypatch, report_module)
    variables = TimeReportService(db_session, storage=None).variables_for(
        seeded_deal_id, "2025-12", WRITER
    )
    assert variables["oggi"] == "01/01/2026"
