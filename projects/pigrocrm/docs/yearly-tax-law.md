# Entering a new tax year's law

Tax rules and rates change from year to year. PigroCRM keeps each year's law as data in the
fiscal pack, so a year computed once keeps reading the law it was computed with
(`projects/pigrocrm/packages/core/src/pigrocrm/core/fiscal/pack.py`, REB-1083). A ledger refuses a
silent edit of a recorded year (REB-1084). This page is the yearly pass that adds the next year.
REB-1085 is the card that enters 2027; open a card of the same shape for each later year.

## The two kinds of section

The module docstring of `pack.py` is the list; this is the summary.

- **Statutory** sections hold until a later entry replaces them: `laws`, `calendars`,
  `payment_rules`, `ceilings`, `charges`, `loss_carry`, `ires_variations` (and the pack's `sales_withholding`). The latest entry at
  or before the year governs. For each year you must **confirm the rule still holds** (below) and
  add an entry only where the law changed.
- **Yearly** sections hold for their own year alone and give no figure for a year with no entry:
  `inps`, `irpef`, `chamber_fees`, the IRAP regional rates, the municipal surcharge lists and the
  legal interest rates. For each year you **must add the entry**; until you do, the estimate says
  that the year's figures are not entered yet.

Never edit an earlier year's entry to make a later year work. A new year is a new entry.

## The yearly figures

| Figure | Primary source | Usually published | Where it goes |
|---|---|---|---|
| IRPEF brackets | The budget law (legge di bilancio) as published in the Gazzetta Ufficiale; TUIR art. 11 on Normattiva; Agenzia delle Entrate, «Aliquote e calcolo dell'Irpef» | December, with the budget law; the Agenzia page follows in January | A new `IrpefLaw(year=...)` module next to `irpef_2026.py`, appended to the pack's `irpef` tuple |
| Regional IRPEF surcharges | Dipartimento delle Finanze, «Addizionale regionale all'IRPEF», aliquote applicabili, one row per region | Regions deliberate through the year; the Finanze table changes when they do, so the figure is the one of the read date | The `regional` rows of the same `IrpefLaw` |
| IRPEF social security deduction | TUIR art. 10 c. 1 lett. e) on Normattiva | Confirm with the budget law | `social_security_deduction` of the same `IrpefLaw` |
| Municipal IRPEF surcharge | Dipartimento delle Finanze, «Addizionale comunale all'Irpef > Aliquote applicabili > Elenchi generali aggiornati quotidianamente» | The list is rebuilt daily; until 20 December a municipality that has not deliberated shows as pending, and a list read after it is `final` in its sidecar | `uv run python projects/pigrocrm/scripts/import_municipal_surcharge.py --year <year>` writes `datasets/municipal_surcharge_<year>.csv` and its `.source.json`; the list is a new file, never a rewrite of an old year |
| INPS contributions | INPS circulars: gestione separata (rates, ceiling, minimum) and artigiani e commercianti (rates, minimum, ceilings, dates, the reductions the circular names: over 65, first enrolment); the Agenzia's Redditi PF fascicolo 2 appendix «INPS - Modalità di calcolo degli acconti» for how the advances are computed | February | A new `InpsYearLaw(year=...)` next to `INPS_2026` in `law_inps.py`, appended to the `inps` tuple of each pack that has it. The first-enrolment reduction (`NewEnrolmentReduction`) is entered only if the year's circular still names it; the earlier entry's own year is never edited |
| Legal interest rate | Ministry of Economy and Finance decree fixing the rate for the next year, in the Gazzetta Ufficiale | December | A new `LegalInterestRate(year=...)` in the payment rules' `shortfall.legal_interest` (`payment_data.py`) |
| IRAP regional rates | Dipartimento delle Finanze, IRAP regional rates; each region's own law | The Finanze table changes when a region deliberates; the figure is the one of the read date | A new `IrapRegionalRates(year=...)` in `IRAP_LAW.regional_rates` (`law_irap.py`) |
| Chamber of commerce fee | The Ministry's (MIMIT) note on the annual fee, and the decree that sets the chambers' increases | The note is a January matter (the 2026 one is dated 16 January 2026); the increases decree follows | A new `ChamberFeeLaw(year=...)` appended to the `chamber_fees` tuple of `it-company` |

Every figure carries the primary source it was read from, with the day it was read, in the same
style as the entries already there. A figure you cannot cite is not entered: the estimate then
names what is missing, which is the right answer.

### How to add a yearly entry

1. Read the source and write the entry in the module the table names. Copy the structure of the
   2026 entry, not its numbers.
2. Append it to the pack's tuple in `pack.py` (years must increase; the pack refuses otherwise).
3. Run the scoped tests (`uv run pytest -q -n auto --dist loadfile
   projects/pigrocrm/packages/core/tests -k 'fiscal or estimate or tax or ceiling or pack'`),
   `uv run mypy` and `uv run ruff check projects/pigrocrm`. The 2026 expectations must not move.
4. Record the year in the ledger (below) in the same PR.

## Confirming that a statutory rule still holds

A statutory section carries into the new year if nobody says otherwise, which is exactly when it
goes wrong silently. For each one, open its source again and read the rule for the new year:

- `laws`: the income tax advances (percentages and thresholds), the IVA settlement, the company
  tax rates, the payroll days, the flat-rate regime's rates and coefficients. Source: the
  citations in each entry. If something changed, add a `TaxYearLaw(from_year=<year>)` entry that
  states the new law in full.
- `calendars`: every date and the rule that moves it (a decree that moves a deadline is a new
  `TaxCalendarLaw` entry with its own `from_year`). Source: the Agenzia delle Entrate deadline
  calendar of the year.
- `payment_rules`: instalments, the forecast method, the shortfall penalties, the F24 codes and
  the filing dates. Source: the Agenzia pages and the resolutions cited in `payment_data.py`.
- `loss_carry`: the limit on how much of a year's income an earlier loss may reduce (80%, and 100%
  for the first three periods of a new activity) and the rule that a professional's loss is not
  carried. Source: TUIR art. 84 and art. 8 on Normattiva; the renumbering of the TUIR from 2027
  (D.Lgs. 117/2026) is a new entry that cites the new articles.
- `ceilings` and `charges`: the thresholds of the flat-rate regime and the 4% INPS surcharge. A
  change is a new entry with its own `from_year`; the earlier entry stays.

When the rule holds, write nothing: the earlier entry governs the new year, and the ledger
records that it does (the next section). When you read it and it holds, say so in the PR body with
the source and the day, because the code cannot show that you looked.

## The ledger

`fiscal/law_ledger.json` holds a fingerprint of every section of every pack for every year that
has law, and `tests/test_law_ledger.py` recomputes them. A recorded year may not change.

- **Add the year.** After adding the entries, run
  `uv run python -m pigrocrm.core.fiscal.law_ledger --add-year` and commit the file. It records
  what is not recorded and never rewrites what is. A new year is an addition and passes the test
  even before you run it, but it is unprotected until it is recorded.
- **Check.** `uv run python -m pigrocrm.core.fiscal.law_ledger` prints each violation as
  `pack / section / year` and exits 1.
- **Deliberate fix of a recorded year.** Edit the data, then
  `uv run python -m pigrocrm.core.fiscal.law_ledger --correct <pack> <section> <year> --source
  "<primary source>" --reason "<why the recorded law was wrong>"`. The correction is committed in
  the same diff as the fix and stays in the ledger for good.
- **A municipal list that is not final** (its sidecar says `"final": false`) is not recorded; a
  refresh of it is not a correction. Record it with `--add-year` once the sidecar says it is final.

A change to a statutory entry shows as a change to every year it governs, so a correction for it
names each of those years.

## The cadence

The pass is once a year, in this order:

1. **December:** the budget law and the legal interest decree. Enter the IRPEF and the legal
   interest rate. A first municipal list may be read now; it is not final.
2. **January:** the chamber fee note, the Agenzia pages that follow the budget law, the IRAP and
   regional tables as they stand.
3. **February:** the INPS circulars. Enter the INPS year.
4. **After 20 December of the year itself:** read the municipal list again; the importer marks it
   `final` in its sidecar, and then `--add-year` records it.

REB-1085 is the pass for 2027. Until a yearly section has the year, the product says that the
year's figures are not entered yet; it does not use the year before's.
