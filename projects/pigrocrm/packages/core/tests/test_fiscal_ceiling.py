"""The ceiling engine and the rivalsa invoice line, against real invoice rows --
REB-361, REB-344 §8-9, REB-352 §2/§6.

No `InvoiceService` here on purpose: what these tests exercise is the arithmetic
against rows that already satisfy the schema's own constraints, the same way
`fakes/invoice_fixtures.py` builds an issued invoice as a row rather than through an
emission. The full create-issue-export cycle, including the rivalsa line surviving
`issue()`'s own natura/riferimento_normativo consistency check and a real FatturaPA
export, is `test_invoice_rivalsa_line.py`.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.fiscal.ceiling import (
    evaluate_ceiling,
    evaluate_pack,
    excluded_from_coefficiente_base,
    paid_revenue_for_calendar_year,
    rivalsa_line_for_contract,
    taxable_ricavi,
)
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, RIVALSA_INPS_CHARGE_ID
from pigrocrm.core.invoices.models import Invoice, InvoiceLine

RICAVI_CEILING = IT_FLAT_RATE_PACK.ceilings[0]
FUORIUSCITA_CEILING = IT_FLAT_RATE_PACK.ceilings[1]
assert RICAVI_CEILING.id == "soglia_ricavi"
assert FUORIUSCITA_CEILING.id == "soglia_fuoriuscita_immediata"

ANNO = 2024


def _customer(session: Session) -> Customer:
    customer = Customer(ragione_sociale=f"Cliente {uuid4().hex[:8]}")
    session.add(customer)
    session.flush()
    return customer


def _invoice(
    session: Session,
    *,
    numero: int,
    anno: int,
    lines: list[tuple[str, Decimal]],
    stato_pagamento: str = "incassato",
    data_incasso: date | None,
) -> Invoice:
    """An `emessa` `fattura` built as a row, with `lines` as `(descrizione,
    prezzo_totale)` pairs, each a zero-rate `N2.2` line -- the shape every line under
    the forfettario already carries (`fiscal/regime.py::_Forfettario`)."""
    imponibile = sum((prezzo for _, prezzo in lines), start=Decimal("0.00"))
    invoice = Invoice(
        customer_id=_customer(session).id,
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        data_emissione=date(anno, 6, 15),
        imponibile=imponibile,
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=imponibile,
        stato_pagamento=stato_pagamento,
        data_incasso=data_incasso,
    )
    session.add(invoice)
    session.flush()
    for i, (descrizione, prezzo) in enumerate(lines, start=1):
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                numero_linea=i,
                descrizione=descrizione,
                quantita=Decimal("1.000000"),
                prezzo_unitario=prezzo,
                prezzo_totale=prezzo,
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
            )
        )
    session.flush()
    return invoice


def _paid(session: Session, *, numero: int, anno: int, lines: list[tuple[str, Decimal]]) -> Invoice:
    return _invoice(session, numero=numero, anno=anno, lines=lines, data_incasso=date(anno, 6, 20))


# --- evaluate_ceiling: the pure half, no session ------------------------------------


def test_no_alert_below_the_lowest_ratio() -> None:
    status = evaluate_ceiling(RICAVI_CEILING, Decimal("50000.00"))
    assert status.livello_allerta is None
    assert status.superata is False
    assert status.residuo == Decimal("35000.00")


def test_approaching_at_the_lower_alert_ratio() -> None:
    status = evaluate_ceiling(RICAVI_CEILING, Decimal("68000.00"))  # 0.8 * 85000
    assert status.livello_allerta == "In avvicinamento alla soglia di ricavi"
    assert status.superata is False


def test_reached_at_the_threshold_itself() -> None:
    status = evaluate_ceiling(RICAVI_CEILING, Decimal("85000.00"))
    assert status.livello_allerta == "Soglia di ricavi raggiunta"
    assert status.superata is True
    assert status.residuo == Decimal("0.00")


def test_the_immediate_exit_ceiling_uses_its_own_higher_alert_ratio() -> None:
    below = evaluate_ceiling(FUORIUSCITA_CEILING, Decimal("89999.99"))
    at_ratio = evaluate_ceiling(FUORIUSCITA_CEILING, Decimal("90000.00"))  # 0.9 * 100000
    assert below.livello_allerta is None
    assert at_ratio.livello_allerta == ("In avvicinamento alla fuoriuscita immediata dal regime")


def test_a_revenue_over_the_hard_ceiling_breaches_both() -> None:
    assert evaluate_ceiling(RICAVI_CEILING, Decimal("120000.00")).superata is True
    assert evaluate_ceiling(FUORIUSCITA_CEILING, Decimal("120000.00")).superata is True


# --- paid_revenue_for_calendar_year / evaluate_pack: real rows ---------------------


def test_paid_revenue_excludes_unpaid_invoices_and_other_years(db_session: Session) -> None:
    _paid(db_session, numero=1, anno=ANNO, lines=[("Consulenza", Decimal("1000.00"))])
    _invoice(
        db_session,
        numero=2,
        anno=ANNO,
        lines=[("Consulenza", Decimal("5000.00"))],
        stato_pagamento="da_incassare",
        data_incasso=None,
    )
    _paid(db_session, numero=1, anno=ANNO + 1, lines=[("Consulenza", Decimal("2000.00"))])

    assert paid_revenue_for_calendar_year(db_session, ANNO) == Decimal("1000.00")


def test_paid_revenue_falls_back_to_data_emissione_when_data_incasso_is_unset(
    db_session: Session,
) -> None:
    """`stato_pagamento = 'incassato'` with no `data_incasso` recorded still counts,
    read by `data_emissione` -- the same fallback `AnalyticsRepository.monthly_incassato`
    already uses, mirrored here rather than reinvented."""
    _invoice(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("300.00"))],
        stato_pagamento="incassato",
        data_incasso=None,
    )
    assert paid_revenue_for_calendar_year(db_session, ANNO) == Decimal("300.00")


def test_evaluate_pack_evaluates_both_ceilings_off_one_paid_revenue_figure(
    db_session: Session,
) -> None:
    _paid(db_session, numero=1, anno=ANNO, lines=[("Consulenza", Decimal("90000.00"))])
    statuses = {s.ceiling.id: s for s in evaluate_pack(IT_FLAT_RATE_PACK, db_session, ANNO)}

    assert statuses["soglia_ricavi"].superata is True
    assert statuses["soglia_fuoriuscita_immediata"].superata is False
    assert statuses["soglia_fuoriuscita_immediata"].livello_allerta == (
        "In avvicinamento alla fuoriuscita immediata dal regime"
    )


def test_a_rivalsa_line_counts_toward_the_paid_revenue_in_full(db_session: Session) -> None:
    """REB-352 §2's own "no code change is needed for the ceiling side" -- a rivalsa
    line is one more line inside `Invoice.imponibile`, no filtering needed."""
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    _paid(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("1000.00")), (charge.descrizione_riga, Decimal("40.00"))],
    )
    assert paid_revenue_for_calendar_year(db_session, ANNO) == Decimal("1040.00")


# --- rivalsa_line_for_contract: pure, no session ------------------------------------


def test_no_line_is_produced_without_the_contracts_election() -> None:
    assert (
        rivalsa_line_for_contract(
            IT_FLAT_RATE_PACK, applies_social_charge=False, fee_subtotal=Decimal("1000.00")
        )
        is None
    )


def test_the_line_is_four_percent_of_the_fee_subtotal() -> None:
    line = rivalsa_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("1000.00")
    )
    assert line is not None
    assert line.prezzo_unitario == Decimal("40.00")
    assert line.quantita == Decimal("1.000000")
    assert line.aliquota_iva == Decimal("0.00")
    assert line.descrizione == IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID).descrizione_riga


def test_the_line_rounds_half_up_like_every_other_money_figure() -> None:
    # 4% of 12.345 = 0.4938 -> 0.49.
    line = rivalsa_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("12.345")
    )
    assert line is not None
    assert line.prezzo_unitario == Decimal("0.49")


def test_a_zero_fee_subtotal_still_produces_a_zero_rivalsa_line() -> None:
    """`None` means "not elected"; an elected contract with nothing invoiced yet is a
    real, zero-amount line, not the same thing as no election at all."""
    line = rivalsa_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=True, fee_subtotal=Decimal("0.00")
    )
    assert line is not None
    assert line.prezzo_unitario == Decimal("0.00")


# --- excluded_from_coefficiente_base / taxable_ricavi: real rows -------------------


def test_excluded_from_coefficiente_base_sums_only_rivalsa_tagged_lines(
    db_session: Session,
) -> None:
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    _invoice(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("1000.00")), (charge.descrizione_riga, Decimal("40.00"))],
        data_incasso=date(ANNO, 6, 20),
    )
    # No rivalsa line at all -- must not contribute anything.
    _invoice(
        db_session,
        numero=2,
        anno=ANNO,
        lines=[("Consulenza", Decimal("500.00"))],
        data_incasso=date(ANNO, 7, 1),
    )

    assert excluded_from_coefficiente_base(IT_FLAT_RATE_PACK, db_session, ANNO) == Decimal("40.00")


def test_excluded_from_coefficiente_base_ignores_a_different_years_invoices(
    db_session: Session,
) -> None:
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    _invoice(
        db_session,
        numero=1,
        anno=ANNO - 1,
        lines=[("Consulenza", Decimal("1000.00")), (charge.descrizione_riga, Decimal("40.00"))],
        data_incasso=date(ANNO - 1, 6, 20),
    )
    assert excluded_from_coefficiente_base(IT_FLAT_RATE_PACK, db_session, ANNO) == Decimal("0.00")


def test_taxable_ricavi_subtracts_exactly_the_rivalsa_amount(db_session: Session) -> None:
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    _invoice(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("1000.00")), (charge.descrizione_riga, Decimal("40.00"))],
        data_incasso=date(ANNO, 6, 20),
    )
    assert taxable_ricavi(IT_FLAT_RATE_PACK, db_session, ANNO, Decimal("1040.00")) == Decimal(
        "1000.00"
    )


def test_the_ceiling_and_the_tax_estimate_diverge_by_exactly_the_rivalsa_amount(
    db_session: Session,
) -> None:
    """REB-352 §2's own tension, proven end to end on one invoice: the ceiling's own
    revenue sum and the coefficiente base must diverge by exactly the tagged amount,
    and by nothing else."""
    charge = IT_FLAT_RATE_PACK.charge(RIVALSA_INPS_CHARGE_ID)
    _paid(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("1000.00")), (charge.descrizione_riga, Decimal("40.00"))],
    )

    ricavi_per_il_limite = paid_revenue_for_calendar_year(db_session, ANNO)
    ricavi_per_il_coefficiente = taxable_ricavi(
        IT_FLAT_RATE_PACK, db_session, ANNO, ricavi_per_il_limite
    )

    assert ricavi_per_il_limite == Decimal("1040.00")
    assert ricavi_per_il_coefficiente == Decimal("1000.00")
    assert ricavi_per_il_limite - ricavi_per_il_coefficiente == Decimal("40.00")


def test_a_contract_without_the_election_leaves_nothing_to_exclude(
    db_session: Session,
) -> None:
    """The other half of the same Done-when: a contract that never elects the
    rivalsa produces no line, so there is nothing for the tagged-figure query to
    find either -- the two functions agree by construction, not by coincidence."""
    line = rivalsa_line_for_contract(
        IT_FLAT_RATE_PACK, applies_social_charge=False, fee_subtotal=Decimal("1000.00")
    )
    assert line is None
    _invoice(
        db_session,
        numero=1,
        anno=ANNO,
        lines=[("Consulenza", Decimal("1000.00"))],
        data_incasso=date(ANNO, 6, 20),
    )
    assert excluded_from_coefficiente_base(IT_FLAT_RATE_PACK, db_session, ANNO) == Decimal("0.00")
