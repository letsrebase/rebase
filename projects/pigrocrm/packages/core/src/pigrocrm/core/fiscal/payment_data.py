"""The payment rules the three Italian packs carry (REB-1067).

The shapes are `fiscal/law_payment.py`'s; `fiscal/pack.py` only attaches what is built here. Every
figure and code was read from its primary source on 2026-11-11 and is a verified row of the frame's
A5 register («verified 2026-11-11 (REB-1067)»). What no primary source states is not here and is
on the accountant's list (REB-975): see that register for the rows left «to verify».
"""

from __future__ import annotations

from decimal import Decimal

from pigrocrm.core.fiscal.law import MonthDay
from pigrocrm.core.fiscal.law_payment import (
    DeferralLaw,
    F24Code,
    F24Line,
    ForecastAdvanceLaw,
    InstalmentLaw,
    LegalInterestRate,
    PaymentRulesLaw,
    RegularisationTier,
    ReturnFilingRule,
    ShortfallLaw,
    WithholdingCreditLaw,
)

# --- instalments, deferral, shortfall: the same for every taxpayer ----------------------

INSTALMENTS = InstalmentLaw(
    rateable=("balance", "first_advance"),
    last_instalment=MonthDay(month=12, day=16),
    instalment_day=16,
    august_last_day=20,
    annual_interest_percent=Decimal("4.00"),
    later_instalment_interest_percent=Decimal("0.33"),
    source=(
        "D.Lgs. 241/1997 art. 20 c. 1 and 4 (Normattiva, read 2026-11-11; art. 10 c. 1 and 4 "
        "D.Lgs. 33/2025 from 2027); D.M. 21 May 2009 art. 5 c. 1 (GU n. 136 of 15-6-2009: «sono "
        "dovuti nella misura del 4 per cento annuo»); Agenzia delle Entrate, Redditi PF 2026 "
        "fascicolo 1, «Rateazione» («lo 0,33 per cento in misura forfetaria»), Redditi SC 2026 "
        "«Parte generale» § 4.4 and IRAP 2026 istruzioni § 5 (read 2026-11-11)"
    ),
)

DEFERRAL = DeferralLaw(surcharge_percent=Decimal("0.40"), window_days=30)

SHORTFALL = ShortfallLaw(
    penalty_percent=Decimal("25"),
    reduced_if_late_up_to_days=90,
    reduced_penalty_fraction=Decimal("0.5"),
    daily_if_late_up_to_days=15,
    daily_fraction_denominator=15,
    # Art. 13 c. 1 lett. a) and a-bis) D.Lgs. 472/1997 (Normattiva in force 2026-11-12, violations
    # from 1 September 2024): «ad un decimo del minimo ... entro il termine di trenta giorni»,
    # «ad un nono del minimo ... entro novanta giorni». Beyond 90 days the pack states no tier.
    regularisation_tiers=(
        RegularisationTier(max_days_late=30, divisor=10),
        RegularisationTier(max_days_late=90, divisor=9),
    ),
    legal_interest=(
        LegalInterestRate(
            year=2025,
            percent=Decimal("2.00"),
            source=(
                "D.M. 10 December 2025 (MEF, GU n. 289 of 13-12-2025), which recalls the rate it "
                "replaces: «fissata al 2 per cento in ragione d'anno, con decorrenza dal 1° "
                "gennaio 2025» (read 2026-11-11)"
            ),
        ),
        LegalInterestRate(
            year=2026,
            percent=Decimal("1.60"),
            source=(
                "D.M. 10 December 2025 (MEF, GU n. 289 of 13-12-2025) art. 1: «fissata all'1,60 "
                "per cento in ragione d'anno, con decorrenza dal 1° gennaio 2026» (read "
                "2026-11-11)"
            ),
        ),
    ),
)

# --- the forecast method ---------------------------------------------------------------

# 100,000 lire (D.L. 69/1989 art. 4 c. 2 lett. a) in euro, as the Agenzia prints it for IRPEF
# («superiore a 51,65 euro») and 40,000 lire for IRES and IRAP («non superiore a 20,66 euro»).
FORECAST_PERSON = ForecastAdvanceLaw(
    first_instalment_min_percent=Decimal("40"),
    total_min_percent=Decimal("100"),
    omitted_regular_if_tax_not_above=Decimal("51.65"),
)
FORECAST_COMPANY = ForecastAdvanceLaw(
    first_instalment_min_percent=Decimal("40"),
    total_min_percent=Decimal("100"),
    omitted_regular_if_tax_not_above=Decimal("20.66"),
)

# --- a withholding suffered -------------------------------------------------------------

WITHHOLDING_CREDIT = WithholdingCreditLaw(
    alternative_in_year_after_before_filing=True,
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 22 c. 1 lett. c), Normattiva, text in force "
        "on 2026-11-11 (art. 24 c. 1 lett. c) of D.Lgs. 19 giugno 2026 n. 117 from 1 January 2027)"
    ),
)

# A company's withholdings (art. 79 c. 1 TUIR sends them to art. 22; art. 88 c. 1 of the testo
# unico of D.Lgs. 117/2026 from 1 January 2027), credited to the period the income accrues in.
COMPANY_WITHHOLDING_CREDIT = WithholdingCreditLaw(
    alternative_in_year_after_before_filing=True,
    income_basis="accrual",
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 79 c. 1 («Le ritenute alla fonte a titolo di "
        "acconto si scomputano dall'imposta a norma dell'articolo 22»), art. 22 c. 1 lett. c) and "
        "art. 109 c. 1 and c. 2 lett. b) («i corrispettivi delle prestazioni di servizi si "
        "considerano conseguiti ... alla data in cui le prestazioni sono ultimate»), Normattiva, "
        "text in force on 2026-11-12 (art. 88 c. 1 and art. 24 c. 1 lett. c) of D.Lgs. 19 giugno "
        "2026 n. 117 from 1 January 2027); Agenzia delle Entrate, Redditi SC 2026, quadro RN, "
        "rigo RN15 (read 2026-11-12)"
    ),
)

# --- F24 codes --------------------------------------------------------------------------

_SRC_ADE = "Agenzia delle Entrate, «Ricerca codici tributo», codice {code} (read {read})"
_SRC_PF = "Agenzia delle Entrate, Redditi PF 2026 fascicolo 1, «Principali codici tributo»"
_SRC_INPS_GS = (
    "INPS, «F24 per professionisti iscritti alla Gestione Separata» (updated 8 July 2025, read "
    "2026-11-11), «Causali di versamento»"
)
_SRC_INPS_CIRC62 = (
    "INPS, circolare n. 62 of 27 May 2026 (Quadro RR del modello Redditi 2026-PF), § 3 «Termini e "
    "modalità di versamento» and § 4 «La rateizzazione» (read 2026-11-12)"
)
_SRC_INPS_AC = (
    "INPS, «F24 per artigiani e commercianti» (updated 8 July 2025, read 2026-11-11), «Causali "
    "di versamento»"
)


def _tax(
    line: F24Line,
    code: str,
    description: str,
    extra: str = "",
    *,
    period: int | None = None,
    read: str = "2026-11-11",
) -> F24Code:
    source = _SRC_ADE.format(code=code, read=read) + (f"; {extra}" if extra else "")
    return F24Code(
        line=line,
        code=code,
        kind="tax_code",
        description=description,
        source=source,
        period=period,
    )


def _inps(line: F24Line, code: str, description: str, source: str) -> F24Code:
    return F24Code(
        line=line, code=code, kind="inps_causale", description=description, source=source
    )


_IRPEF_CODES = (
    _tax("irpef_balance", "4001", "IRPEF - saldo", _SRC_PF),
    _tax("irpef_first_advance", "4033", "IRPEF - acconto prima rata", _SRC_PF),
    _tax(
        "irpef_second_advance",
        "4034",
        "IRPEF - acconto seconda rata o acconto in unica soluzione",
        _SRC_PF,
    ),
)
_SURCHARGE_CODES = (
    _tax(
        "regional_surcharge_balance",
        "3801",
        "Addizionale regionale all'imposta sul reddito delle persone fisiche",
        _SRC_PF,
    ),
    _tax(
        "municipal_surcharge_balance",
        "3844",
        "Addizionale comunale all'IRPEF - autotassazione - saldo",
        f"{_SRC_PF}; risoluzione 368/E of 12 December 2007 (read 2026-11-11)",
    ),
    _tax(
        "municipal_surcharge_advance",
        "3843",
        "Addizionale comunale all'IRPEF - autotassazione - acconto",
        f"{_SRC_PF}; risoluzione 368/E of 12 December 2007 (read 2026-11-11)",
    ),
)
_SUBSTITUTE_TAX_CODES = (
    _tax(
        "substitute_tax_balance",
        "1792",
        "Imposta sostitutiva sul regime forfetario - saldo - art. 1, c. 64, legge n. 190/2014",
        f"{_SRC_PF}; risoluzione 59/E of 11 June 2015 (read 2026-11-11)",
    ),
    _tax(
        "substitute_tax_first_advance",
        "1790",
        "Imposta sostitutiva sul regime forfetario - acconto prima rata - art. 1, c. 64, legge "
        "n. 190/2014",
        f"{_SRC_PF}; risoluzione 59/E of 11 June 2015 (read 2026-11-11)",
    ),
    _tax(
        "substitute_tax_second_advance",
        "1791",
        "Imposta sostitutiva sul regime forfetario - acconto seconda rata o in unica soluzione - "
        "art. 1, c. 64, legge n. 190/2014",
        f"{_SRC_PF}; risoluzione 59/E of 11 June 2015 (read 2026-11-11)",
    ),
)
_IRES_CODES = (
    _tax(
        "ires_balance",
        "2003",
        "IRES saldo - articolo 72 del DPR 917/86 come modificato dal DLGS 344/03",
        "risoluzione 76/E of 27 May 2004 (read 2026-11-11)",
    ),
    _tax(
        "ires_first_advance",
        "2001",
        "IRES - acconto prima rata - art. 72 del DPR 917/86 come modificato dal DLGS 344/03",
        "risoluzione 76/E of 27 May 2004 (read 2026-11-11)",
    ),
    _tax(
        "ires_second_advance",
        "2002",
        "IRES - acconto seconda rata o in unica soluzione - art. 72 del DPR 917/86 come "
        "modificato dal DLGS 344/03",
        "risoluzione 76/E of 27 May 2004 (read 2026-11-11)",
    ),
)
_IRAP_CODES = (
    _tax("irap_balance", "3800", "Imposta regionale sulle attività produttive - saldo"),
    _tax("irap_first_advance", "3812", "IRAP - acconto prima rata"),
    _tax(
        "irap_second_advance",
        "3813",
        "IRAP - acconto seconda rata o acconto in unica soluzione",
    ),
)
_CHAMBER_FEE_CODE = _tax(
    "chamber_fee", "3850", "Diritto camerale", "risoluzione 46 of 10 April 2001 (read 2026-11-11)"
)
_INTEREST_ERARIO_CODE = _tax(
    "instalment_interest_erario",
    "1668",
    "Interessi pagamento dilazionato - importi rateizzabili sezione 2 del modello di versamento "
    "unitario",
    _SRC_PF,
)
_INTEREST_REGIONAL_CODE = _tax(
    "instalment_interest_regional", "3805", "Interessi pagamento dilazionato tributi regionali"
)
_INTEREST_MUNICIPAL_CODE = _tax(
    "instalment_interest_municipal",
    "3857",
    "Interessi pagamento dilazionato - autotassazione - addizionale comunale all'IRPEF",
    "risoluzione 368/E of 12 December 2007: the code 3804 it replaces is abrogated from 1 "
    "January 2008 (read 2026-11-11)",
)

# The periodic IVA (REB-1100, A5 row «F24 tax codes for the periodic IVA payments», verified
# 2026-11-12). The Agenzia's code pages give one code per month (6001 January to 6012 December)
# and one per quarter for the first three (6031 to 6033, «Art. 74»), each paid «in unica
# soluzione», with no instalment column and no credit («importi a credito compensati: NON
# COMPILARE»); the `anno di riferimento` is the «Anno d'imposta per cui si effettua il
# pagamento». That a payment of December made in January is written with the year of the
# December it pays is an inference from that wording (the pages' own example is a payment in
# the same year): to confirm on a real F24 of January. The fourth quarter of a quarterly payer is
# paid with the annual settlement, 6099 («VERSAMENTO IVA SULLA BASE DELLA DICHIARAZIONE
# ANNUALE», the only IVA code that «puo' essere versato in forma rateale» and «utilizzabile anche
# per indicare un importo a credito da compensare»). 6034 («VERSAMENTO IVA QUARTO TRIMESTRE») and
# 6035 («VERSAMENTO IVA ACCONTO») are on the Agenzia's list, and its pages do not say which
# payer uses them: they stay out, to verify with the accountant (REB-975). The advance of a
# monthly payer is 6013 («VERSAMENTO ACCONTO PER IVA MENSILE»).
_MONTHS_IT = (
    "GENNAIO",
    "FEBBRAIO",
    "MARZO",
    "APRILE",
    "MAGGIO",
    "GIUGNO",
    "LUGLIO",
    "AGOSTO",
    "SETTEMBRE",
    "OTTOBRE",
    "NOVEMBRE",
    "DICEMBRE",
)
_IVA_CODES = (
    *(
        _tax(
            "iva_monthly",
            str(6000 + month),
            f"VERSAMENTO IVA MENSILE {name}",
            read="2026-11-12",
            period=month,
        )
        for month, name in enumerate(_MONTHS_IT, start=1)
    ),
    *(
        _tax(
            "iva_quarterly",
            str(6030 + quarter),
            f"VERSAMENTO IVA TRIMESTRALE {quarter} TRIMESTRE",
            "art. 74 D.P.R. 633/1972",
            read="2026-11-12",
            period=quarter,
        )
        for quarter in (1, 2, 3)
    ),
    _tax(
        "iva_annual",
        "6099",
        "VERSAMENTO IVA SULLA BASE DELLA DICHIARAZIONE ANNUALE",
        read="2026-11-12",
    ),
    _tax(
        "iva_advance_monthly",
        "6013",
        "VERSAMENTO ACCONTO PER IVA MENSILE",
        read="2026-11-12",
    ),
)

# The withholding a payer operates (REB-1100, verified 2026-11-12): 1001 on employees' pay (art.
# 23 c. 2 D.P.R. 600/1973), 1040 on self-employed fees (art. 25), both «in unica soluzione», no
# credit, with the month the withholding was operated in the `mese rif.` column and the tax
# year in `anno di riferimento`. A flat-rate taxpayer owes only the first: it is a withholding
# agent on employees and not on fees (art. 1 c. 69 L. 190/2014, A5).
_WITHHOLDING_EMPLOYEES_CODE = _tax(
    "withholding_employees",
    "1001",
    "RITENUTE SU RETRIBUZIONI PENSIONI TRASFERTE MENSILITA' AGGIUNTIVE E RELATIVO CONGUAGLIO",
    "D.P.R. 600/1973 art. 23 c. 2",
    read="2026-11-12",
)
_WITHHOLDING_SELF_EMPLOYED_CODE = _tax(
    "withholding_self_employed",
    "1040",
    "RITENUTE SU REDDITI DI LAVORO AUTONOMO COMPENSI PER L'ESERCIZIO DI ARTI E PROFESSIONI",
    "D.P.R. 600/1973 art. 25",
    read="2026-11-12",
)

# An employer's INPS contributions on payroll (REB-1100, verified 2026-11-12): the causale DM10
# of the F24's INPS section, with the matricola INPS (format 1, ten digits), the month of the
# payroll as «periodo da» (`MM/AAAA`) and nothing in «a». INAIL's premium is not here: no source
# read gives the fields of the INAIL section, so it stays to verify (REB-975).
_PAYROLL_CODES = (
    _inps(
        "inps_payroll",
        "DM10",
        "DM10 attivi, passivi, insoluti: an employer's contributions on payroll; matricola INPS "
        "of ten digits (format 1), periodo «da» MM/AAAA, «a» empty",
        "INPS, circolare n. 137 of 28 July 2003, allegato 1 («causale contributo: DM10»; periodo "
        "«da»: the month and year of competence; periodo «a»: not filled in); INPS, circolare "
        "n. 6 of 22 January 2025; Agenzia delle Entrate, «Causali INPS» (table updated "
        "11-11-2026, row «DM10 | DM10 attivi, passivi, insoluti | Formato 1», read 2026-11-12) "
        "and «Tabella dei valori che può assumere il campo matricola INPS» (format 1: «10 "
        "numerici»)",
    ),
)

_INPS_CODES = (
    _inps(
        "inps_gs_contribution",
        "PXX",
        "Gestione separata, professional not retired and not insured elsewhere: balance or "
        "advance in one payment",
        _SRC_INPS_GS,
    ),
    _inps(
        "inps_gs_contribution_other_cover",
        "P10",
        "Gestione separata, professional retired or insured elsewhere: balance or advance in "
        "one payment",
        _SRC_INPS_GS,
    ),
    _inps(
        "inps_gs_instalment",
        "PXXR",
        "Gestione separata, professional not retired and not insured elsewhere: instalment",
        _SRC_INPS_GS,
    ),
    _inps(
        "inps_gs_instalment_other_cover",
        "P10R",
        "Gestione separata, professional retired or insured elsewhere: instalment",
        _SRC_INPS_GS,
    ),
    _inps(
        "inps_gs_interest",
        "DPPI",
        "Gestione separata: the 0.4% of a deferral and the interest of an instalment, a line of "
        "their own",
        f"{_SRC_INPS_GS}; {_SRC_INPS_CIRC62}: «DPPI» for the interest of the «Gestione separata»",
    ),
    _inps(
        "inps_artigiani_interest",
        "API",
        "Artigiani: the interest of an instalment and the 0.4% of a deferral on the contributions "
        "above the minimum income, a line of their own with the codeline of the contribution",
        f"{_SRC_INPS_CIRC62}: «API» (artigiani) with the codeline of the contribution it belongs "
        "to",
    ),
    _inps(
        "inps_commercianti_interest",
        "CPI",
        "Commercianti: the interest of an instalment and the 0.4% of a deferral on the "
        "contributions above the minimum income, a line of their own with the codeline of the "
        "contribution",
        f"{_SRC_INPS_CIRC62}: «CPI» (commercianti) with the codeline of the contribution it "
        "belongs to",
    ),
    _inps(
        "inps_artigiani_minimum",
        "AF",
        "Artigiani: contributions on the minimum income",
        _SRC_INPS_AC,
    ),
    _inps(
        "inps_commercianti_minimum",
        "CF",
        "Commercianti: contributions on the minimum income",
        _SRC_INPS_AC,
    ),
    _inps(
        "inps_artigiani_excess",
        "AP",
        "Artigiani: contributions above the minimum income (a debit, or a credit when overpaid)",
        _SRC_INPS_AC,
    ),
    _inps(
        "inps_commercianti_excess",
        "CP",
        "Commercianti: contributions above the minimum income (a debit, or a credit when overpaid)",
        _SRC_INPS_AC,
    ),
    _inps(
        "inps_artigiani_excess_instalment",
        "APR",
        "Artigiani: instalment of the contributions above the minimum income",
        _SRC_INPS_AC,
    ),
    _inps(
        "inps_commercianti_excess_instalment",
        "CPR",
        "Commercianti: instalment of the contributions above the minimum income",
        _SRC_INPS_AC,
    ),
)

# --- return filing -----------------------------------------------------------------------

_SRC_PF_FILING = (
    "D.P.R. 322/1998 art. 2 c. 1 (Normattiva, read 2026-11-11: «in via telematica tra il 15 "
    "aprile e il 31 ottobre dell'anno successivo»); Agenzia delle Entrate, Redditi PF 2026 "
    "fascicolo 1 § 6 («dal 15 aprile 2026 al 2 novembre 2026 (dal momento che il 31 ottobre 2026 "
    "è sabato)»)"
)
_PF_FILING = ReturnFilingRule(
    form="redditi_pf",
    fixed_day=MonthDay(month=10, day=31),
    covers="the Redditi PF return of the tax year before, filed online",
    source=_SRC_PF_FILING,
)
_SC_FILING = ReturnFilingRule(
    form="redditi_sc",
    months_after_period_end=10,
    covers="the Redditi SC return of the tax period that closed, filed online",
    source=(
        "D.P.R. 322/1998 art. 2 c. 2 (Normattiva, read 2026-11-11: «entro l'ultimo giorno del "
        "decimo mese successivo a quello di chiusura del periodo d'imposta»); Agenzia delle "
        "Entrate, «Quando e come si presenta il modello Redditi SC» (2 November 2026 for a "
        "calendar-year company) and Redditi SC 2026 «Parte generale» § 3.1 (30 April "
        "2027 for the period 1 July 2025 - 30 June 2026)"
    ),
)
_IRAP_FILING = ReturnFilingRule(
    form="irap",
    months_after_period_end=10,
    covers="the IRAP return of a company, filed online",
    source=(
        "D.P.R. 322/1998 art. 2 c. 2 and 3; Agenzia delle Entrate, IRAP 2026 istruzioni § 3.1 "
        "(«a partire dal 15 aprile dell'anno successivo, se il periodo d'imposta coincide con "
        "l'anno solare, ed entro l'ultimo giorno del decimo mese successivo a quello di chiusura "
        "del periodo d'imposta»; 2 November 2026 and, for 1 July 2025 - 30 June 2026, 30 April "
        "2027) and «Come e quando si presenta l'Irap 2026» (read 2026-11-11)"
    ),
)

# --- the packs' entries -------------------------------------------------------------------

IT_STANDARD_PAYMENT_2026 = PaymentRulesLaw(
    from_year=2026,
    instalments=INSTALMENTS,
    deferral=DEFERRAL,
    forecast=FORECAST_PERSON,
    shortfall=SHORTFALL,
    f24_codes=(
        *_IRPEF_CODES,
        *_SURCHARGE_CODES,
        _INTEREST_ERARIO_CODE,
        _INTEREST_REGIONAL_CODE,
        _INTEREST_MUNICIPAL_CODE,
        *_INPS_CODES,
        *_IVA_CODES,
        _WITHHOLDING_EMPLOYEES_CODE,
        _WITHHOLDING_SELF_EMPLOYED_CODE,
        *_PAYROLL_CODES,
    ),
    filings=(_PF_FILING,),
    withholding_credit=WITHHOLDING_CREDIT,
)

IT_FLAT_RATE_PAYMENT_2026 = PaymentRulesLaw(
    from_year=2026,
    instalments=INSTALMENTS,
    deferral=DEFERRAL,
    forecast=FORECAST_PERSON,
    shortfall=SHORTFALL,
    f24_codes=(
        *_SUBSTITUTE_TAX_CODES,
        _INTEREST_ERARIO_CODE,
        *_INPS_CODES,
        _WITHHOLDING_EMPLOYEES_CODE,
        *_PAYROLL_CODES,
    ),
    filings=(_PF_FILING,),
)

IT_COMPANY_PAYMENT_2026 = PaymentRulesLaw(
    from_year=2026,
    instalments=INSTALMENTS,
    deferral=DEFERRAL,
    forecast=FORECAST_COMPANY,
    shortfall=SHORTFALL,
    withholding_credit=COMPANY_WITHHOLDING_CREDIT,
    f24_codes=(
        *_IRES_CODES,
        *_IRAP_CODES,
        _CHAMBER_FEE_CODE,
        _INTEREST_ERARIO_CODE,
        _INTEREST_REGIONAL_CODE,
        *_IVA_CODES,
        _WITHHOLDING_EMPLOYEES_CODE,
        _WITHHOLDING_SELF_EMPLOYED_CODE,
        *_PAYROLL_CODES,
    ),
    filings=(_SC_FILING, _IRAP_FILING),
)

__all__ = [
    "IT_COMPANY_PAYMENT_2026",
    "IT_FLAT_RATE_PAYMENT_2026",
    "IT_STANDARD_PAYMENT_2026",
]
