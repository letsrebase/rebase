# IRAP and company advances: what the primary sources settle (REB-1086, from REB-975)

All sources were fetched and read on 2026-11-12 (`read`/`curl` on the URL; Normattiva pages with `!vig=2026-11-12` where the article has a later version). Quotes are verbatim Italian. Nothing in the repo, Linear or the CRM was changed. Tags: `[INFERENCE]` marks a step the source does not state.

Code references: `law_irap.py`, `pack.py`, `advances.py`, `company_estimate.py`, `law.py` are in `projects/pigrocrm/packages/core/src/pigrocrm/core/fiscal/`; `staff/models.py` is in `.../core/staff/`; «A5» is the register in `projects/pigrocrm/docs/superpowers/specs/2026-11-09-space-profiles-modules-and-the-money-picture-design.md`.

## What changes in the code (short list, detail in each section)

1. **The «ordinary» IRAP rate is not what an ordinary srl pays in six regions, when the activity is on a regional ATECO list** (§1): Toscana 4.82 (not 3.90) for, among others, ATECO 61, 62, 63, 68 to 74, and 45 to 47, 55, 56 for a capital company; Umbria 4.82 (not 4.30); Piemonte, Liguria, Emilia-Romagna, Veneto and Basilicata also list codes. The pack has no ATECO code of the legal entity (`ateco_group` is the flat-rate annex 4 group only).
2. **IRAP has the same €103 rule as IRES, but it is tested on the first instalment, not on the whole advance** (§5). `pack.py` sets `irap_single_payment_at_or_below=None` and `ires_single_payment_at_or_below=103`, and `advances.AdvanceRule.is_single(total)` compares the **total** with 103. Art. 17 c. 3 DPR 435/2001 tests the payment due at the first instalment.
3. **A natural person owes no IRAP from tax year 2022** (§6): the A5 row «The filing date of the IRAP return of a natural person» has no object.
4. **Apprentices, disabled workers and training-contract staff are deducted in full (art. 11 c. 1 lett. a n. 5) and are outside the five-employee count** (§4). `company_estimate._staff_deductions` cannot tell them apart and gives them nothing (untyped) or 1,850 (typed `fixed_term`).
5. **Short periods** (§2): the per-employee deduction must be prorated on the calendar year, not on the period's own length (`company_estimate.py` divides by `period_days`), and the 400,000 test and the 4-bis table work on the annualised amounts.
6. **TUIR article numbers change from 2027** (§8): D.Lgs. 117/2026 (in force 4-7-2026) approves a new Testo unico imposte sui redditi that applies from 1 January 2027 (its art. 377); old art. 109 becomes art. 118, art. 83 becomes art. 92, art. 66 becomes art. 75, art. 54 becomes art. 56. The 2026 estimate still follows the old numbers.

---

## 1. Special-subject rates and regional reductions that depend on the company

**Question.** Which taxpayer facts select a rate other than the one `law_irap.py` carries, region by region, in the Finanze 2026 dataset `irp2026.csv`?

**Verdict: taxpayer fact.** Every row other than the pack's depends on something about the company (its legal kind, its prevailing ATECO code, when it was established, where its sites are, how many employees it has, which collective agreement it applies, what it did the previous years). The law does not decide any of them; the CRM can ask all of them. One of the facts, the ATECO code, moves the rate of an *ordinary* commercial srl in six regions, so the «ordinary» figure is a default, not a fact (see «Key findings» below).

**Answer.** The 2026 file holds 449 rate rows for 21 regions and autonomous provinces (155 are flagged `AIUTO_DI_STATO_AUTOMATICO = si`, that is, *de minimis* aid, so a reduced rate also needs the company's de minimis history). The pack carries one row per region; I checked all 21 pack values against the file as it stands on 2026-11-12 and every one equals the row the code calls ordinary (`CODICE_ALI` 001), and every `mef_id` equals the file's `ID_IRAP`. What follows lists, per region, the rows an ordinary commercial srl can meet. Rows for banks, insurers, SIM and fund managers, non-financial holdings, public bodies, ASP/IPAB, third-sector entities, cooperatives and concessionaires exist in every region and need only the legal kind of the entity (Appendix A has every row of every region, with the facts that select it).

**Source.**
- Dipartimento delle Finanze, «Fiscalità regionale e locale - IRAP - Aliquote applicabili», «Elenchi generali aggiornati quotidianamente», «Elenchi contenenti solo le pubblicazioni effettuate vigenti in data odierna: Elenco del 2026 (Ultimo aggiornamento del 09/10/2026)»: page https://www1.finanze.gov.it/finanze2/dipartimentopolitichefiscali/fiscalitalocale/aliquoteirap/download/tabella.htm/download.php?anno=2026&tipo=irp ; the CSV (580 lines, `;`-separated, one header line per region then one line per rate, note and deduction) is https://www1.finanze.gov.it/finanze2/dipartimentopolitichefiscali/fiscalitalocale/aliquoteirap/download/download.php?tipo=irp&anno=2026 . Read 2026-11-12. The file's `COD_REGIONE` is the Finanze's own alphabetical numbering (01 Abruzzo to 21 Veneto), not ISTAT's; the pack's keys are ISTAT's and are matched by `ID_IRAP` (`_rate(..., mef_id, ...)`).
- D.Lgs. 446/1997 art. 16 c. 1-bis and c. 3, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1997-12-15;446~art16): «a) all'articolo 5, che esercitano attività di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, si applica l'aliquota del 3,80 per cento» and «le regioni hanno facoltà di variare l'aliquota di cui al comma 1 e 1-bis fino ad un massimo di 0,92 punti percentuali. La variazione può essere differenziata per settori di attività e per categorie di soggetti passivi.»
- D.L. 20 febbraio 2026 n. 21 art. 3 c. 1, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legge:2026-02-20;21~art3): «Per il periodo d'imposta successivo a quello in corso al 31 dicembre 2025 e per quello successivo, l'aliquota di cui all'articolo 16, commi 1 e 1-bis, del decreto legislativo 15 dicembre 1997, n. 446, è aumentata di due punti percentuali per i soggetti esercenti, in via prevalente, le attività economiche individuate dai codici ATECO di cui alla tabella 1 allegata al presente decreto.» The file names those codes in the Veneto rows: «codici ATECO 06 e 49.50.1», «09.1», «19.2, 35.1, 35.2, 35.4».

### Key findings that change what «ordinary» means

Quotes are the dataset's `REGIME_ALI`, read 2026-11-12, from the CSV above.

- **Toscana 007, 4.82:** «Aliquota maggiorata per le attivita' economiche individuate dai codici ISTAT (Ateco 2007): 52.21.2, 61, 68, 05, 06, 07,08, 09, 21, 35,36,37,38, 39, 45, 46, 47, 49.10, 49.20, 49.31, 49.39, 49.50, 50, 51, 52.22, 52.23, 52.24, 52.29, 53, 55, 56, 58, 62, 63, 69, 70,71, 72, 73,74, 75, 77.21, 77.22, 77.29, 77.31, 77.32, 77.33, 77.34, 77.35, 77.39, 77.40, 78, 79, 80, 82, 86.10, 86.21, 86.22, 86.90, 86.23, 92, 95 . N.B. la maggiorazione per i codici 45, 46, 47, 55, 56, 86.10, 86.21, 86.22, 86.90, si applica solo alle societa' di capitali» (norm: art. 5 L.R. 22/12/06 n. 64; art. 1 c. 1 L.R. 27/12/12 n. 77). A Tuscan srl in software (62), consulting (70), engineering (71), advertising (73) or most services pays 4.82, not the 3.90 the pack carries.
- **Umbria 002, 4.82** (pack 4.30): «Aliquota maggiorata per i settori di attivita' economiche contraddistinti dai codici ATECO 2007: 53.10.00. 53.20.00. 61.10.00. 61.20.00. 61.30.00. 61.90.10. 61.90.20. 61.90.91. 61.90.99. 41.10.00. 42.99.01. 68.10.00. 68.20.01. 68.20.02. 68.31.00 e dai codici ATECO 2007: 66.11.00. 66.12.00. 66.30.00. 66.19.40. 66.29.01 qualora l'attivita' non sia svolta da banche ed altri enti e societa' finanziarie [...] e da imprese di assicurazione».
- **Piemonte:** 027 4.82 (ATECO 47.1), 028 4.82 (ATECO 64), 029 4.82 (ATECO 66.1), 030 4.82 (ATECO 66.2), 026 6.82 (ATECO 35.1); 013 and 014 3.00 (ATECO 90 and 91), 045 2.98 (ATECO 47.62.10).
- **Liguria 006, 4.82:** «...esercenti,in via prevalente, le attivita' economiche comprese nelle categorie e sottocategorie di attivita' economiche individuate dai codici ATECO: 19.10.00,24.46.00,35.30.00,53.10.00, 53.20.00, 61.10.01, 61.10.02».
- **Emilia-Romagna:** 002 4.20 («alcune attivita' economiche identificate dai soli codici ATECO 2007 (agg 2022) riportati nell' allegato indicato all'art.1 comma 1 della L.R. 31/3/2025, n. 1», the annex is not in the dataset); 004 4.82 («settori contraddistinti dai codici 23, 40, 64 e 67 della classificazione ATECOFIN 2004»).
- **Veneto:** 015 4.82 (ATECOFIN 2007 codes 19, 35, 53, 61, 64, 65, 66, for subjects that are not banks or insurers) and 017 4.55 (a list of ATECO 2007 codes, among them 08.xx, 20.xx, 46.24.20, 46.35.00, 47.11.10-30, 47.19.10, 47.26.00, 49.32.10; the list is cut in the file's text) on top of the general 4.08.
- **Basilicata:** 012 4.82, 013 6.82, 014 5.90 for energy and oil ATECO lists (pack 3.90).
- **Trento:** the pack's 2.68 is row 001, «Aliquota ridotta per i soggetti di cui all'art.16, co 1, D.Lgs. n. 446/97 che non rientrano nel codice 010 e che non esercitano, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, co 1, del D.L. 20 febbraio 2026, n. 21». Row 010 (4.82) is «...che non rispettano, nei confronti della generalita' o di intere categorie di dipendenti, gli accordi e i contratti collettivi nazionali, nonche' quelli regionali, territoriali o aziendali, stipulati dalle organizzazioni sindacali dei datori di lavoro e dei lavoratori comparativamente piu' rappresentative sul piano nazionale, salvo il caso in cui il trattamento normativo e retributivo, pur non discendendo da contratti collettivi, sia identico o migliorativo...». So 2.68 holds only for a company that respects the national/territorial collective agreements for its staff; one that does not pays 4.82. `[INFERENCE]` A company with no employees is not caught by 010.
- **Friuli-Venezia Giulia 010, 2.98:** «Aliquota ridotta per imprese aventi almeno 1 e non piu' di 5 dipendenti (Reg. UE 2831/2023)» (norm art. 2 c. 1 lett. a L.R. 30/12/08 n. 17). A srl with 1 to 5 employees in FVG pays 2.98, not 3.90. The dataset does not say how the employees are counted; the regional law does (art. 2 c. 1 lett. a L.R. 30 dicembre 2008 n. 17, Lexview, https://lexview-int.regione.fvg.it/fontinormative/xml/xmlLex.aspx?anno=2008&legge=17&ART=2&AG1=00&AG2=00&fx=lex , read 2026-11-12): the aliquota «è ridotta dello 0,92 per cento: a) per i soggetti passivi Irap di cui all'articolo 3, comma 1, lettere a) e b), del decreto legislativo 446/1997 che, alla data di chiusura del periodo d'imposta, si avvalgano di personale dipendente con una soglia massima di cinque dipendenti con contratto di lavoro a tempo indeterminato, anche a tempo parziale. Non rientrano nel computo dei dipendenti i soci lavoratori delle società cooperative». So the fact is the number of permanent employees on the last day of the tax period (part-time counted, at least one per the dataset's «almeno 1»), and comma 4 requires the company to file a *de minimis* declaration with the Region by the return deadline. The file also notes that FVG renumbered its codes from 021 on in 2026.
- **Veneto 001, 4.08, is the general rate** (the question in the 2026-11-10 comment, answered in §1b below).

### Per region: pack figure, and what moves an ordinary commercial srl

`Pack` is row 001 (all 21 equal the file today). `Rows` is the number of rate rows in the file. For each other row: code, rate, the fact that selects it.

| Region (key) | Pack | Rows | Rows an ordinary company can meet, and the fact that selects each |
|---|---|---|---|
| Piemonte (01) | 3.90 | 45 | 003 2.98 site in a «comune ad alta marginalità» (municipality of the site); 015 2.98 new company or transferred into the region; 002 2.98 local publishing; 045 2.98 ATECO 47.62.10; 013/014 3.00 ATECO 90/91; 004 4.82 gaming machines on the premises; 027/028/029/030 4.82 ATECO 47.1/64/66.1/66.2; 026 6.82 ATECO 35.1; D.L. 21 activity 031 5.90, 032 and 033 4.98 |
| Valle d'Aosta (02) | 3.90 | 25 | 002 2.98 value of production and staff cost each at least 5% above the three-year average, «su base nazionale»; 021 0.00 new initiative begun in the 2022 tax year, first five periods; 022 0.00 industrial firm or research centre in the areas of the Giunta decision with a funded R&D programme; 019 0.00 alpine refuge; 020 0.00 mutual cooperative in the regional register; 018 0.00 «centri polifunzionali di servizio» |
| Lombardia (03) | 3.90 | 19 | 002 2.98 new company in the small municipalities named by the implementing acts of L.R. 10/2003; 012 0.00 new company, site or local unit in a municipality up to 3,000 inhabitants (or a historic centre of a larger one) registered from 1 January 2021; 011 2.90 micro, small or medium company in ATECO 59.14.00 (cinemas) |
| Bolzano (BZ) | 3.90 | 15 | 009 2.68 pays extra pay to employees under territorial or company agreements or results bonuses; 002 4.20 concessionaire; D.L. 21 activity 010 5.90, 012 4.68 (with extra pay) |
| Trento (TN) | 2.68 | 19 | 002 2.00 applies provincial collective agreements or company/territorial agreements signed 1-1-2025 to 31-12-2026 with an extra pay element; 010 4.82 does not respect collective agreements; 012 0.00 new productive initiative, five years, with at least one person continuously in the province six months (first year) / twelve months (later years); 003 4.82 gaming machines; D.L. 21 activity 015 4.68, 016 4.00, 017 6.82 |
| Veneto (05) | 4.08 | 26 | 002/003 2.90 new youth/women company set up in the region 2024-2026 with the requisites of L.R. 57/1999 / L.R. 1/2000; 012 4.82 gaming machines; 015 4.82 ATECOFIN 19, 35, 53, 61, 64, 65, 66; 017 4.55 ATECO list; D.L. 21 activity 019 6.08, 020 6.55, 021 6.82; social cooperatives 004 to 008 |
| Friuli-Venezia Giulia (06) | 3.90 | 67 | 010 2.98 1 to 5 permanent employees on the last day of the period (L.R. 17/2008 art. 2); 011 2.98 professional with at least one employee and turnover up to 120,000; 015 3.50 increased R&D staff cost; 022 3.17 new craft company, up to 5 periods; 023 2.98 value of production and staff cost each at least 3% above the three-year average; 027 0.00 and 031 1.00 new or transferred company (0.00 the first three periods, 1.00 the fourth and fifth); 035 2.90 applies collective agreements and funds a welfare package for permanent staff; 006 0.00 site in a mountain area of socio-economic disadvantage; 019 2.98 / 020 4.82 gaming machines removed / installed; each of these again for D.L. 21 activities (049 to 066) |
| Liguria (07) | 3.90 | 15 | 006 4.82 ATECO 19.10.00, 24.46.00, 35.30.00, 53.10.00, 53.20.00, 61.10.01, 61.10.02; 012 5.90 and 014 6.82 fuel and energy ATECO lists; 005 4.20 concessionaire |
| Emilia-Romagna (08) | 3.90 | 18 | 002 4.20 ATECO in the annex of L.R. 1/2025; 003 6.20; 004 4.82 ATECOFIN 23, 40, 64, 67; 005 6.82; 006 to 009 concessionaires |
| Toscana (09) | 3.90 | 25 | **007 4.82 ATECO list above (capital companies only for 45, 46, 47, 55, 56)**; 005 2.98 ATECO 77.11, 77.12; 003 2.94 value produced in mountain territories; 006 2.98 subjects of art. 1-bis L.R. 79/2012; 019 0.00 retail trade in mountain territories; 008 4.20, 009 4.32, 017 3.40 gaming machines installed / removed |
| Umbria (10) | 4.30 | 22 | **002 4.82 ATECO list above**; 004 2.98 work cooperatives (institutional activity); D.L. 21 activity 013 6.30, 014 6.82 |
| Marche (11) | 4.73 | 22 | 002 3.90 at least 50% of turnover is contract work in textile and clothing (INAIL codes 8140 to 8220); 005 3.90 ATECO 72.11.00, 72.19.01, 72.19.09, 72.20.00; 006 3.90 entertainment ATECO list; 009 and 012 4.13 footwear, bags, leather ATECO; 010 4.13 seat in the region, sections C to G, INAIL premium rate not increased, at least 50% of last year's turnover exported; 011 4.13 same, EU-definition SME that in the three periods after 2025 hires permanent staff or meets another listed condition |
| Lazio (12) | 4.82 | 13 | 010 3.90 operates only in the mountain municipalities of Annex B L.R. 20/2025 and value of production in the region up to 1,000,000; 012 3.90 already active elsewhere on 1-1-2026 and transfers the legal and operational seat to Lazio in 2026; 013 4.36 social cooperatives |
| Abruzzo (13) | 4.82 | 14 | 007 3.90 operates in mountain municipalities (L.R. 44/2006); 009 3.90 began activity, first two periods «nei limiti ed alle condizioni previste dalla legge regionale»; 006 3.90 rural pharmacies; 008 3.90 social cooperatives |
| Molise (14) | 4.97 | 22 | 002 4.05 value produced in the Campobasso municipalities damaged by the 2002 earthquake; 003 4.05 youth company (art. 5 c. 1 D.Lgs. 185/2000, or sole owner aged 35 or less); 004 4.05 women's company (art. 2 lett. a L. 215/1992); 005 4.05 amateur sports company; 006 4.05 value produced in mountain municipalities above 750 m |
| Campania (15) | 4.97 | 10 | 009 4.40 social cooperatives only |
| Puglia (16) | 4.82 | 12 | none: the rows are D.L. 21 activity, concessionaires, banks, insurers, holdings, public bodies, ASP and RUNTS entities |
| Basilicata (17) | 3.90 | 14 | 012 4.82, 013 6.82, 014 5.90 energy and oil ATECO lists; 006 2.98 third-sector entities |
| Calabria (18) | 4.82 | 9 | none (same as Puglia) |
| Sicilia (19) | 3.90 | 25 | 014 3.65 small or medium company in crafts, trade, tourism or services, and cooperatives; 012 0.00 tourism, hotel, craft, cultural-heritage, IT, agri-food or industrial company with turnover up to 10 million that began activity from 2004, five periods; 010 2.98 owner who filed a detailed complaint of extortion, three years; 024 6.82 oil refining |
| Sardegna (20) | 2.93 | 12 | 006 0.00 new productive initiative in the region (art. 3 c. 1 L.R. 5/2015); D.L. 21 activity 009 4.93 |

Regional **deductions and credits** (`COD_DEDUZIONE`, `COD_DETRAZIONE`, `COD_CREDITO`): 8 lines. Bolzano DEDU1 (40,000 euro per bed for accredited care homes) and DEDU2 (50% of the cost of professional athletes); Trento DEDU1 (cost of the «Progettone» staff of L.P. 32/1990); FVG DEDU1 (20,000 euro and other sums per permanent hire of a worker who lost a job in a company crisis in the previous 48 months) and DETR1 (80% of gifts to trusts under L. 112/2016, up to 10,000); Lazio CRED1, Lombardia CRED1 and Toscana CRED1 (tax credits for donations or for non-profit healthcare foundations). All need a fact the CRM can ask; none is modelled.

### The facts the CRM would have to ask

| Fact | Rows that use it | Field it would be |
|---|---|---|
| Legal kind (bank, financial company, SIM, fund manager, holding, insurer, public body, ASP/IPAB, third-sector, cooperative type, amateur sports company) | every region | `legal_entities.entity_kind` (new enum); the pack's srl profile is the default |
| Prevailing ATECO code of the activity (2025, with the 2007 correspondence the rows still use) | D.L. 21/2026 table 1 in every region; Toscana, Umbria, Piemonte, Liguria, Emilia-Romagna, Veneto, Basilicata, Marche, Lombardia lists | `legal_entities.ateco_code` (new; `fiscal_profile.ateco_group` is flat-rate annex 4 only) |
| Concession holder (art. 5 «imprese concessionarie», other than motorway and tunnel construction and management) | every region | `legal_entities.is_concessionaire` |
| Date of constitution, date the activity began, date a seat was transferred into the region | Piemonte 015, Lombardia 002/012, FVG 022/027/031, Trento 012, Veneto 002/003, VdA 021, Lazio 012, Abruzzo 009, Sardegna 006, Sicilia 012 | `legal_entities.incorporated_on`, `activity_started_on`, `seat_transferred_in_on` |
| Founders' age and sex, or the L. 185/2000 / L. 215/1992 qualification | Veneto 002/003, Molise 003/004 | `legal_entities.youth_enterprise`, `women_enterprise` |
| Municipality of each site (mountain, marginal, small, historic centre, earthquake-damaged) and the value produced there | Piemonte 003, Lombardia 002/012, FVG 006, Toscana 003/019, Lazio 010, Abruzzo 007, Molise 002/006 | the site's ISTAT municipality on the establishment record (the CRM holds addresses, not establishments) |
| Headcount (1 to 5) and, for Trento, a person continuously present six or twelve months | FVG 010/049/050, Trento 012 | derived from `staff_members` |
| Collective agreements applied, extra pay, results bonuses, welfare package | Bolzano 009/012, Trento 002/010/016/017, FVG 035/036/065/066 | `legal_entities.collective_agreement_status` |
| Three previous years' value of production and staff cost | FVG 023/024/059/060, VdA 002/005/007 | needs the ledger values of previous years (the CRM keeps no ledger, A6) |
| Increase of R&D staff cost | FVG 015/016/053/054 | no source in the CRM |
| Gaming machines on the premises, or removed | Piemonte 004, Veneto 012, Trento 003, FVG 019/020, Toscana 008/009/017 | `establishments.gaming_machines` |
| EU SME status, turnover, share of turnover exported, INAIL premium rate unchanged | Sicilia 012/014, Marche 010/011, Lombardia 011 | derived or asked |
| *De minimis* aid received in the last three years | the 155 rows flagged `AIUTO_DI_STATO_AUTOMATICO = si` | `legal_entities.de_minimis_received` |

**Code implication.**
- `law_irap.py`: rows `01` (Piemonte), `07` (Liguria), `08` (Emilia-Romagna), `09` (Toscana), `10` (Umbria), `11` (Marche), `17` (Basilicata) carry no `note` about the rows above, and the `05` note (Veneto) does not mention codes 015 and 017. Add them: for Toscana the code 007 text, for Umbria the code 002 text, for Piemonte codes 027 to 030, for Liguria 006, for Emilia-Romagna 002 and 004, for Basilicata 012 to 014, for Marche 010 and 011 (they lower the rate), for Veneto 015 and 017. Row `06` (FVG) already says the 2.98 row exists; it should say that 1 to 5 employees selects it. Rows `06`, `19`, `BZ` and `TN` already carry a note that names their reduced rows and stay as they are.
- `company_estimate._irap` (`assumptions[3]`, «Si applica l'aliquota ordinaria della regione della sede, con una sola sede.»): the sentence must not claim the rate is the one the company pays. Without an ATECO code the right sentence for Toscana, Umbria, Piemonte, Liguria, Emilia-Romagna, Veneto, Basilicata is «l'aliquota ordinaria della regione; per alcune attività (codici ATECO elencati dalla Regione) è più alta»; for FVG «ridotta a 2,98% con 1-5 dipendenti».
- A5 row «IRAP rates of a subject other than an ordinary company...» (state «not modelled»): change to «taxpayer fact» with the table above; A5 row «IRAP ordinary rate of a company in 2026 by region» (state «verified 2026-11-11»): keep the figures (all 21 re-checked 2026-11-12) but add that the row is the default for a company with none of the selecting facts.
- Pack structure (when built): `IrapRegionRate` gains `alternatives: tuple[IrapAlternative, ...]`, each `code`, `rate`, `facts` (keys of the table above) and the dataset text; `IrapLaw.rate_for(year, region, facts)` returns the alternative only when every fact is held, else the ordinary rate and the assumption sentence.

### 1b. Veneto: is 4.08 the general rate an ordinary srl pays?

**Question.** The Veneto row used is labelled «Maggiorazione di aliquota generalizzata»; is it the rate of an ordinary srl?

**Verdict: settled.**

**Answer.** Yes. Code 001 is the Veneto rate for every taxpayer not listed under codes 02 to 26, and there is no row called «Aliquota ordinaria» because 001 plays that part. The file's row: ID_IRAP 2421, DATA_PUBBLICAZIONE 09-GIU-26, `VALORE_ALI` 4.08, `NORMA_ALIQUOTE` «Art. 2, co. 1 lett. b), L.R. n. 32/2024». An ordinary srl leaves the 4.08 only through codes 02/03 (young or women new companies of 2024-2026: 2.90), 12 (gaming machines: 4.82), 15 (ATECOFIN 19, 35, 53, 61, 64, 65, 66: 4.82), 17 (ATECO list: 4.55) and 19 to 24 (D.L. 21 activities, with or without a concession: 6.08 to 7.12); the other codes (04 to 11, 13, 14, 16, 18, 25, 26) are social cooperatives, banks, insurers, concessionaires, public bodies, IPAB and holdings.

**Source.** The same CSV, region Veneto, `CODICE_ALI` 001: «Maggiorazione di aliquota generalizzata a decorrere dall'anno d'imposta successivo a quello in corso al 31/12/2024, ad esclusione dei soggetti passivi di cui alle aliquote 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26.» (read 2026-11-12).

**Code implication.** `law_irap.py` row `05` (`4.08`, `mef_id` 2421, `published` 2026-06-09, norm «art. 2 c. 1 lett. b L.R. 32/2024») and its `note` («code 001 is the general increase to 4.08, which applies to every subject not listed in its other codes») are correct and need no change. The A5 row's remark is also correct. Only add to the note the list above, as for the other regions.

---

## 2. Periods other than twelve months

**Question.** How are the base and the deductions of art. 11 c. 4-bis (small base) and c. 4-bis.1 (per employee) ragguagliate when the tax period is not twelve months?

**Verdict: settled** for the rule; the day-count arithmetic is not written anywhere (see `[INFERENCE]`).

**Answer.** For a period shorter or longer than twelve months, and for a business that starts or stops during the year, the law says that three things are brought to the *anno solare*: the amounts of the c. 4-bis table and the base they are tested on, and the positive components tested against 400,000 in c. 4-bis.1. The 1,850 euro per employee is «su base annua» and is further prorated to the days of the employment relationship in the period, and reduced in proportion for part-time work. `[INFERENCE]` In practice: annual base = base × 365 / days of the period (this is the figure that enters the 180,759.91 to 180,999.91 table); deduction of the table = table amount × days / 365, never more than the base; positive components for the 400,000 test = components × 365 / days; 1,850 × days of employment in the period / 365, times the part-time fraction. The sources give no worked formula, so the 365 against 366 choice for a period that contains 29 February is open; the effect is under 0.3% of a figure of at most 8,000 euro.

**Source.**
- D.Lgs. 446/1997 art. 11 c. 4-bis.2, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1997-12-15;446~art11, text updated at 11/08/2026): «In caso di periodo d'imposta di durata inferiore o superiore a dodici mesi e in caso di inizio e cessazione dell'attività in corso d'anno, gli importi delle deduzioni e della base imponibile di cui al comma 4-bis e dei componenti positivi di cui al comma 4-bis.1 sono ragguagliati all'anno solare. Le deduzioni di cui ai commi 1, lettera a), numero 2), e 4-bis.1 sono ragguagliate ai giorni di durata del rapporto di lavoro nel corso del periodo d'imposta nel caso di contratti di lavoro a tempo indeterminato e parziale, nei diversi tipi e modalità di cui all'articolo 1 del decreto legislativo 25 febbraio 2000, n. 61, [...] sono ridotte in misura proporzionale». Read 2026-11-12.
- Art. 11 c. 4-bis.1: «spetta una deduzione dalla base imponibile pari a euro 1.850, su base annua, per ogni lavoratore dipendente diverso da quelli a tempo indeterminato impiegato nel periodo d'imposta fino a un massimo di cinque».
- Agenzia delle Entrate, «Istruzioni IRAP 2026» (https://www.agenziaentrate.gov.it/portale/documents/20143/9765340/IRAP_2026_istruzioni.pdf/6cd3bc0e-e7bd-f449-c86b-03cc142c1b74?t=1772192055414, read 2026-11-12), quadro IC, rigo IC75: «Ai sensi del comma 4-bis.2 dell'articolo 11, in caso di periodo d'imposta di durata inferiore o superiore a dodici mesi e in caso di inizio e cessazione dell'attività in corso d'anno, gli importi delle deduzioni e della base imponibile sono ragguagliati all'anno solare.» Quadro IS, sezione I, rigo IS5: «gli importi dei componenti positivi di cui al comma 4-bis.1 sono ragguagliati all'anno solare. Ai sensi del medesimo comma 4-bis.2 la deduzione è ragguagliata ai giorni di durata del rapporto di lavoro nel corso del periodo d'imposta e, nel caso di contratti di lavoro a tempo parziale, nei diversi tipi e modalità di cui al d.lgs. 15 giugno 2015, n. 81, ivi compreso il lavoro a tempo parziale di tipo verticale e di tipo misto, è ridotta in misura proporzionale.»

**Code implication.**
- `law_irap.IrapLaw.small_base_deduction(base)`: its docstring says «Art. 11 c. 4-bis.2's pro-rating of a period that is not twelve months is not applied.» Change the signature to take the period's days: `annual = base * 365 / period_days`; row chosen on `annual`; returned deduction `min(row.amount * period_days / 365, base)`. For a twelve-month calendar year nothing changes.
- `company_estimate._staff_deductions`: `period_days = (basis.period_to - basis.period_from).days + 1` and `amount = round_money(rule.amount * row.days_in_service / period_days)`. Today a person in service for the whole of a 100-day first period gets the full 1,850; the law gives 1,850 × 100 / 365 = 506.85. Replace `period_days` by 365 in this fraction. The test `basis.revenue > rule.components_limit` (400,000) must use `basis.revenue * 365 / period_days`.
- Part-time: the law reduces the 1,850 «in misura proporzionale». `StaffMember.part_time_percent` (`staff/models.py` line 207) holds the share, but `basis_schemas.BasisEmployee` (staff_member_id, name, contract_type, cost, days_in_service) does not carry it. Add `part_time_percent` to `BasisEmployee` and multiply the 1,850 fraction by it; the rest of `_staff_deductions` stays.
- A5 row «IRAP deductions...» says «Not modelled: pro-rating of a period that is not twelve months and of part-time work»: both are settled by the sources above; change the state of that sentence to «to build» and cite c. 4-bis.2.
- `PerEmployeeDeduction` docstring already says «Pro rata to the days of the employment (c. 4-bis.2)»; no change.

---

## 3. The seasonal 70% deduction: what the records must hold

**Question.** Art. 11 c. 4-octies: what exactly must the records hold for the 70% deduction of a seasonal worker?

**Verdict: taxpayer fact.** The law and the Agenzia's circular fix the conditions completely; applying them needs the contract history of each worker with the company, which the CRM does not hold. The only part no source defines is which workers are «stagionali» (see the last paragraph of the answer).

**Answer.** For each seasonal worker the record must give, per contract with the same employer: the start and end date of the contract, and the days of *effective work* in each tax period (not the days of the contract). The deduction is due for a contract if (1) it is the second or a later contract with the same employer, signed within two years of the end of the previous one; (2) the worker was employed at least 120 days in two tax periods taken together (the periods need not be consecutive, and the days of the first contract count); (3) the worker is a seasonal worker, that is, hired on a fixed-term contract for recurring seasonal activity. The deduction is 70% of the cost incurred for the days of effective work under the qualifying contract, the INAIL contributions (deductible in full under c. 1 lett. a n. 1) being left out of the 70%. The first contract earns nothing, it only contributes its days. A third contract qualifies if signed within two years of the end of the second and the days of the second and third reach 120. The sources do not define «stagionale»; the circular speaks of «attività stagionali ricorrenti», the labour-law notion is the list of seasonal activities of D.P.R. 1525/1963 and the collective agreements (art. 21 c. 2 D.Lgs. 81/2015), and `[INFERENCE]` the CRM should take the worker as seasonal only on the employer's statement that the activity is on that list or in the agreement.

**Source.**
- D.Lgs. 446/1997 art. 11 c. 4-octies, Normattiva (read 2026-11-12): «La deduzione di cui al primo periodo è altresì ammessa, nei limiti del 70 per cento del costo complessivamente sostenuto, per ogni lavoratore stagionale impiegato per almeno centoventi giorni per due periodi d'imposta, a decorrere dal secondo contratto stipulato con lo stesso datore di lavoro nell'arco temporale di due anni a partire dalla cessazione del precedente contratto.»
- Agenzia delle Entrate, Circolare 18 maggio 2016 n. 20/E, § 2 «Lavoratori stagionali (comma 73)» (https://www.agenziaentrate.gov.it/portale/documents/20143/298218/Circolare+20E+del+18+maggio+2016_Circolare+N.+20_E+DEL+18+maggio+2016.pdf/f31e2fb6-9099-8955-c8de-3ec0034f2093, read 2026-11-12): «estendendo la deducibilità delle spese per il personale impiegato a tempo indeterminato [...] anche ai lavoratori stagionali assunti con contratto a tempo determinato per lo svolgimento di attività stagionali ricorrenti»; «impiegati per almeno 120 giorni nell'arco di due periodi d'imposta successivi, anche non consecutivi, a partire dal secondo contratto stipulato col medesimo datore di lavoro entro il secondo anno successivo alla data di cessazione del primo contratto utile ai fini del computo dei giorni lavorativi richiesti»; «Il calcolo deve intendersi riferito alle giornate di effettivo impiego (e non di formale assunzione), computando anche la prestazione relativa al primo contratto»; the example: «In relazione al secondo rapporto di lavoro è riconosciuta una deduzione pari al 70% delle spese sostenute per il dipendente in relazione ai giorni di effettivo impiego (61 giorni), al netto di eventuali deduzioni spettanti. Il lavoratore risulta infatti complessivamente impiegato per 152 giorni nell'arco dei due periodi d'imposta considerati»; «L'agevolazione andrebbe, altresì, riconosciuta in relazione ad un terzo contratto stipulato entro due anni dalla cessazione del secondo, a condizione che i giorni di impiego risultino almeno pari a 120 giorni, considerando anche quelli afferenti al secondo contratto.» The circular dates from the 2016 wording («70 per cento della differenza ivi prevista»); D.L. 73/2022 art. 10 changed it to «costo complessivamente sostenuto».
- «Istruzioni IRAP 2026», quadro IS, rigo IS7: «ed è altresì ammessa, nei limiti del 70 per cento del costo complessivamente sostenuto (ad eccezione dei contributi assicurativi comunque integralmente deducibili), per ogni lavoratore stagionale impiegato per almeno centoventi giorni per due periodi d'imposta, a decorrere dal secondo contratto stipulato con lo stesso datore di lavoro nell'arco temporale di due anni a partire dalla cessazione del precedente contratto» and «Per i predetti lavoratori stagionali la deduzione integrale dei contributi assicurativi va, invece, indicata nel rigo IS1.» (read 2026-11-12).
- Circolare 9 giugno 2015 n. 22/E, Q&A 5 «Contratti a termine» (before the seasonal rule): fixed-term work «in ragione del tipo di attività svolta [lavorazioni stagionali]» does not give the full deduction of permanent staff, «Stante la ratio della norma, finalizzata ad incentivare gli impieghi a tempo indeterminato» (https://www.agenziaentrate.gov.it/portale/documents/20143/298563/Circolare%2B22E%2Bdel%2B09%2Bgiugno%2B2015_CIR22E%2Bdel%2B09062015.pdf/5f2d7c82-9569-b622-1c6d-48c7c9cb65be?t=1433859871369, read 2026-11-12).
- D.Lgs. 81/2015 art. 21 c. 2, Normattiva: «Le disposizioni di cui al presente comma non trovano applicazione nei confronti dei lavoratori impiegati nelle attività stagionali individuate con decreto del Ministero del lavoro e delle politiche sociali nonché nelle ipotesi individuate dai contratti collettivi. Fino all'adozione del decreto di cui al secondo periodo continuano a trovare applicazione le disposizioni del decreto del Presidente della Repubblica 7 ottobre 1963, n. 1525.» (supporting only: it is the labour definition, the IRAP rule does not cite it).

**Code implication.**
- `law_irap.PermanentStaffDeduction` (`seasonal_percent` 70, `seasonal_min_days` 120) is right and stays. Add `seasonal_window_years = 2` (the text's «due anni»).
- `company_estimate._staff_deductions` (the `seasonal` branch): today it says the deduction «non è applicata: i dati non dicono se il contratto segue un altro con lo stesso datore». The records needed: a new table `staff_contract_periods(staff_member_id, start_date, end_date, worked_days)` (one row per contract, `worked_days` the days of effective work in that contract per tax period, so it must be per period or computed from `start_date`/`end_date` and an absence record) and the flag `seasonal` on the contract, not on the person (`staff/models.py` `CONTRACT_TYPES` holds `permanent`, `fixed_term`, `seasonal` per `StaffMember`; `basis_schemas.BasisEmployee` carries one `contract_type` and `days_in_service` per person per year). With that, the rule is: for each seasonal contract C of tax year Y, deductible if an earlier seasonal contract with the same employer ended less than two years before C started and `worked_days(Y) + worked_days(Y') >= 120` for a previous period Y'; deduction = 70% × (cost of C for the days worked − INAIL contributions).
- `[INFERENCE]` not settled by a source: whether the 70% and the 1,850 of c. 4-bis.1 can both be taken on the same seasonal worker. The statute does not exclude it, c. 4-septies caps only c. 1 and c. 4-bis.1, and the instructions add them in IS8 without a rule. Until the accountant says, the estimate should apply the 70% and leave the 1,850 for a qualifying seasonal worker unapplied (the safer figure).
- A5 row «IRAP deductions»: the seasonal part moves from «not modelled» to «taxpayer fact: contract history».

---

## 4. Headcount exclusions in c. 4-bis.1 (apprentices and disabled workers)

**Question.** The exact words of c. 4-bis.1 on apprentices and disabled workers.

**Verdict: settled.**

**Answer.** The five-employee ceiling and the deduction of 1,850 euro apply to employees *other than* apprentices, disabled workers and staff hired on a *contratto di formazione lavoro*, who are not counted at all. Their cost is not lost: art. 11 c. 1 lett. a n. 5 deducts it in full for a company (art. 5 base), over and above c. 4-bis.1. The Agenzia adds that the contracts of insertion of L. 30/2003 are treated like formation contracts, that the five workers are chosen freely when there are more, and that an apprentice turned permanent mid-year is deducted as an apprentice for the apprentice period and as permanent staff afterwards. «Disabled» means as defined by art. 1 L. 12 marzo 1999 n. 68 or art. 5 L. 2 aprile 1968 n. 482.

**Source.**
- D.Lgs. 446/1997 art. 11 c. 4-bis.1, Normattiva (read 2026-11-12): «Ai fini del computo del numero di lavoratori dipendenti per i quali spetta la deduzione di cui al presente comma non si tiene conto degli apprendisti, dei disabili e del personale assunto con contratti di formazione lavoro.»
- Art. 11 c. 1 lett. a n. 5: «sono ammessi in deduzione: [...] 5) per i soggetti che determinano il valore della produzione ai sensi degli articoli da 5 a 9, in relazione al personale dipendente diverso da quello a tempo indeterminato, [...] le spese relative agli apprendisti, ai disabili e le spese per il personale assunto con contratti di formazione e lavoro».
- «Istruzioni IRAP 2026», rigo IS5: «Ai fini del computo del numero di lavoratori dipendenti per i quali spetta la deduzione non si tiene conto degli apprendisti, dei disabili e del personale assunto con contratti di formazione lavoro (e contratti di inserimento previsti dalla legge n. 30 del 14 febbraio 2003 e dal D.Lgs. n. 276 del 10 settembre 2003 di attuazione della stessa). Si precisa che il limite di cinque lavoratori va calcolato al massimo su cinque lavoratori impiegati nel corso dell'anno; pertanto se il contribuente impiega più di 5 lavoratori dipendenti con contratti di diverso tipo è libero di scegliere i lavoratori su cui calcolare la deduzione.» Rigo IS4, colonna 2: «le spese erogate relative agli apprendisti e ai disabili impiegati nel periodo d'imposta, così come definiti dall'articolo 1 della legge 12 marzo 1999 n. 68 ovvero dall'articolo 5 della legge 2 aprile 1968, n. 482 [...], deducibili ai sensi dell'articolo 11, comma 1, lett. a), n. 5». Sezione I: «nell'ipotesi in cui il datore di lavoro in corso d'anno decida, in relazione ai dipendenti inizialmente assunti come apprendisti, di trasformare il rapporto di lavoro a tempo indeterminato, potrà beneficiare della deduzione per apprendisti per tutto il periodo in cui i lavoratori sono assunti con tale qualifica e, successivamente, della deduzione del costo per personale a tempo indeterminato».

**Code implication.**
- `staff/models.py` `CONTRACT_TYPES = ("permanent", "fixed_term", "seasonal")`, `staff/schemas.py` `ContractType`, `fiscal/basis_schemas.BasisEmployee.contract_type`: add `apprentice`, `training_contract` and a boolean `disabled_l68` on the staff member. `company_estimate._staff_deductions`: those persons get the full cost deducted (line «Deduzione delle spese per apprendisti, disabili e contratti di formazione lavoro (art. 11 c. 1 lett. a n. 5)») and are filtered out of the `other` list that feeds the 1,850 and the `max_employees = 5` slice. Today an untyped person adds a «base is overstated» sentence and a `fixed_term` apprentice would get 1,850 instead of the whole cost.
- The estimate's current assumption text «apprendisti e lavoratori disabili, che la legge non conta, non sono distinti» becomes unnecessary once the types exist.
- `law_irap.PerEmployeeDeduction` docstring («apprentices, disabled workers and workers on training contracts are not counted in the headcount») is correct and stays.
- `[Not asked, found]` art. 11 c. 1 lett. a n. 1 deducts the INAIL contributions on staff other than permanent staff in full («in relazione a soggetti diversi dai lavoratori dipendenti a tempo indeterminato, i contributi per le assicurazioni obbligatorie contro gli infortuni sul lavoro»); the estimate takes no such deduction, so the base is overstated by that amount.

---

## 5. A single-payment threshold for IRAP advances

**Question.** Is there a limit up to which the IRAP advance is paid in one payment (DPR 435/2001, art. 30 D.Lgs. 446/1997, the Redditi and IRAP instructions)?

**Verdict: settled.**

**Answer.** Yes, and it is the same as for IRES and IRPEF: the advance is paid in two instalments *unless the payment due at the first instalment does not exceed 103 euro*; then it is not split. DPR 435/2001 art. 17 c. 3 names IRAP in the same sentence as IRPEF and IRES, and art. 30 c. 3 D.Lgs. 446/1997 sends IRAP advances to the income-tax rules. The test is on the **first instalment's amount** (40% of the advance, or 50% under ISA), not on the whole advance: with 40% the whole advance is split only above 257.50; with 50% only above 206. When the first instalment is 103 or less, the IRAP instructions say it is «non è dovuto»; read with the DPR («in due rate salvo che»), the advance is paid once, in full, at the second term. `[INFERENCE]` that the IRAP page does not spell out «in full»; the Agenzia's IRPEF page does («unico versamento, entro il 30 novembre»). There is no separate IRAP floor for the advance: no advance is due when the previous year's tax is 20.66 euro or less for a company (51.65 for a partnership).

**Source.**
- D.P.R. 7 dicembre 2001 n. 435 art. 17 c. 3, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:presidente.repubblica:decreto:2001-12-07;435~art17, read 2026-11-12): «I versamenti di acconto dell'imposta sul reddito delle persone fisiche e dell'imposta sul reddito delle persone giuridiche dovuti ai sensi della legge 23 marzo 1977, n. 97, e successive modificazioni, nonché quelli relativi all'imposta regionale sulle attività produttive, sono effettuati in due rate salvo che il versamento da effettuare alla scadenza della prima rata non superi euro 103. Il quaranta per cento dell'acconto dovuto è versato alla scadenza della prima rata e il residuo importo alla scadenza della seconda.»
- D.Lgs. 446/1997 art. 30 c. 3 (Normattiva): «sono dovuti acconti dell'imposta ad esso relativa secondo le disposizioni previste per le imposte sui redditi. Gli acconti sono versati con le modalità e nei termini per queste stabiliti.»
- «Istruzioni IRAP 2026», § 5.2 «Acconti»: «Ai sensi dell'articolo 30, comma 3, il versamento in acconto dell'IRAP deve essere effettuato secondo le stesse regole stabilite per le imposte sui redditi.»; «la prima, pari al 40 per cento, entro lo stesso termine per il versamento dell'imposta a saldo [...]. Si fa presente che il versamento della prima rata non è dovuto se d'importo non superiore a euro 103.»; «nella misura pari al 100 per cento dell'importo indicato nel rigo IR21 [...], sempreché tale importo sia superiore a euro 20,66» (51,65 for the partnerships of art. 5 TUIR); for ISA subjects «i versamenti di acconto dell'IRAP sono effettuati, ai sensi dell'art. 17 del decreto del Presidente della Repubblica n. 435 del 2001, in due rate ciascuna nella misura del 50 per cento».
- Redditi SC 2026, «Parte generale» § 4.2 (https://www.agenziaentrate.gov.it/portale/documents/20143/9771542/Parte_Generale_istr_2026.pdf/2ad66f68-6f28-a982-8570-c130cdcdddfa, read 2026-11-12): «I versamenti di acconto dell'IRES (legge 23 marzo 1977, n. 97) sono effettuati in due rate salvo che il versamento della prima rata non superi euro 103 (art. 17, comma 3, del d.P.R. 7 dicembre 2001, n. 435).» Agenzia, «Imposte sui redditi (Irpef, Ires)», società di capitali (https://www.agenziaentrate.gov.it/portale/schede/pagamenti/imposte-sui-redditi/cosa-imposte-sui-redditi-imprese, read 2026-11-12): «L'acconto è pagato in due rate, salvo che il versamento da eseguire alla scadenza della prima non superi i 103 euro.» and, for IRPEF, «unico versamento, entro il 30 novembre, se l'acconto è inferiore a 257,52 euro».
- Balance, not advance: art. 30 c. 4 D.Lgs. 446/1997 and the IRAP 2026 instructions § 5.1: the tax «non è dovuta o, se il saldo è negativo, non è rimborsabile, se i relativi importi spettanti a ciascuna regione non superano 10,33 euro».

**Code implication.**
- `pack.py` `IT_COMPANY_PACK.company_advances`: `ires_single_payment_at_or_below=Decimal("103")`, `irap_single_payment_at_or_below=None`. New: one field `single_payment_first_instalment_at_or_below = Decimal("103")` for both taxes (remove the two old fields), read in `law.CompanyAdvanceLaw` (fields at `law.py` lines 195-196) and its docstring («IRES: the advance is split in two only above ires_single_payment_at_or_below... no source read states a single-payment limit for IRAP»).
- `advances.advance_rule` / `AdvanceRule.is_single(total)` / `advance_amounts`: today `is_single` compares the **total** with `single_limit`. Compute `first = round_money(total * first_percent / 100)` and decide `single = first <= 103`. Worked: IRES or IRAP advance 200 non-ISA: first 80, not above 103, so one payment of 200 on 30 November (today 200 > 103, split 80 / 120, wrong); advance 300: first 120, split. ISA: advance 206 gives first 103, one payment; 207 gives 103.50, split. This is also how `IncomeTaxAdvanceLaw.single_november_payment_at_or_below_isa = 206` already reads for IRPEF.
- A5 rows: «IRES: the advance is split in two instalments only above €103» (state «verified», text must say «above €103 at the first instalment»); «IRAP: the advance is paid in a single payment up to some amount...» (state «to verify») becomes «verified», citing art. 17 c. 3 DPR 435/2001, IRAP 2026 § 5.2.
- Not in the pack: the balance floor (10.33 per region for IRAP, 12 euro per tax for IRES, art. 1 c. 137 L. 266/2005 as quoted by Redditi SC § 4.1) is carried nowhere (`grep` of `fiscal/` finds none).

---

## 6. The IRAP return of a natural person

**Question.** What is the IRAP return date for a person in the ordinary regime in 2026, and does such a person owe IRAP at all?

**Verdict: settled.** There is no return and no tax: a natural person doing business or practising a profession does not owe IRAP, from the tax year 2022 on.

**Answer.** L. 234/2021 art. 1 c. 8 removed IRAP for natural persons under art. 3 c. 1 lett. b (business) and lett. c (arts and professions) of D.Lgs. 446/1997, from the tax period in progress on 1 January 2022. Persons in the flat-rate regime were already outside it. The Agenzia's IRAP 2026 model has no section for natural persons (only partnerships, IRES subjects, entities and public administrations: quadri IP, IC, IE, IK), and its «Come e quando si presenta» page lists no deadline for persons. The exemption is for natural persons: società semplici and associated professional practices (art. 3 c. 1 lett. c) still owe IRAP.

**Source.**
- L. 30 dicembre 2021 n. 234 art. 1 c. 8, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:2021-12-30;234, read 2026-11-12): «A decorrere dal periodo d'imposta in corso alla data di entrata in vigore della presente legge, l'imposta regionale sulle attività produttive (IRAP), di cui al decreto legislativo 15 dicembre 1997, n. 446, non è dovuta dalle persone fisiche esercenti attività commerciali ed esercenti arti e professioni di cui alle lettere b) e c) del comma 1 dell'articolo 3 del medesimo decreto legislativo n. 446 del 1997.» The same text is the annotation (70) to D.Lgs. 446/1997 art. 3 on Normattiva. Entry into force of L. 234/2021, Normattiva note: «Entrata in vigore del provvedimento: 01/01/2022».
- «Istruzioni IRAP 2026», indice: «2.1 – Istruzioni comuni ai quadri IP, IC, IE e IK», «2.2 – Quadro IP Società di persone», «2.3 – Quadro IC Società di capitali», «2.4 – Quadro IE Enti non commerciali», «2.5 – Quadro IK Amministrazioni ed Enti Pubblici» (no quadro for natural persons). Agenzia, «Come e quando si presenta l'Irap 2026» (https://www.agenziaentrate.gov.it/portale/it/dichiarazione-irap-2026/come-e-quando-si-presenta-enti, read 2026-11-12): the model is filed «per le società semplici, le società in nome collettivo e in accomandita semplice, nonché per le società e associazioni a esse equiparate (articolo 5 del Tuir)», «per i soggetti Ires e per le Amministrazioni pubbliche».

**Code implication.**
- A5 row «The filing date of the IRAP return of a natural person» (state «to verify»): close it as «not applicable: art. 1 c. 8 L. 234/2021». The code needs no change: `IT_STANDARD_PACK.laws[0].taxes = ("irpef", "inps")` has no IRAP, and `payment_data._IRAP_FILING` covers only «the IRAP return of a company». Do not add IRAP lines to the person estimate.
- Add one test assertion if wanted: `IT_STANDARD_PACK` has no `irap` tax, so an ordinary-regime person's money picture never shows IRAP.

---

## 7. IRES and IRAP advances and instalments for a company

**Question (2026-11-10 comment «Company pack»).** IRES and IRAP advances and instalments for a company.

**Verdict: settled.** The rows A5 holds as «verified» (REB-1067, REB-1074) are confirmed by the primary text; one of them is mis-stated (see §5).

**Answer.** Advance: 100% of the tax declared for the previous year, net of deductions, credits and withholdings, no advance when that tax is 20.66 euro or less; 40% by the term of the balance (last day of the sixth month after the period closes, 30 June for a calendar-year company, or within 30 days with 0.40%), 60% by the last day of the eleventh month of the period (30 November 2026); 50% and 50% for ISA subjects within the revenue limit; one payment when the first instalment is 103 or less (§5). The balance and the first advance can be paid in monthly instalments (16th of each month, 20 August when it falls on 1 to 20 August) up to 16 December of the year the return is filed; the second or only advance cannot; interest 4% a year from the day after the first instalment's due date to the second's (the Agenzia's table for a first payment on 30 June: 0.00, 0.18, 0.51, 0.84, 1.17, 1.50, 1.83) and 0.33% flat for each later instalment. The forecast method (advance on the expected tax) is allowed with the sanction rules of art. 4 c. 2 D.L. 69/1989. Special 2026 provisions concern other subjects: for energy-sector companies D.L. 21/2026 art. 3 c. 2 says the 2026 IRAP advance assumes the previous tax recomputed with the +2 points; for banks and insurers art. 1 c. 19 L. 207/2024 and art. 1 c. 74 L. 199/2025 change the reference tax. None applies to an ordinary srl.

**Source.**
- Redditi SC 2026 «Parte generale» § 4.2 (read 2026-11-12): «L'acconto non è dovuto se l'imposta relativa al periodo d'imposta precedente al netto delle detrazioni, dei crediti d'imposta e delle ritenute d'acconto è di ammontare non superiore a 20,66 euro. La percentuale dell'acconto dell'IRES è fissata nella misura del 100 per cento. Il quaranta per cento dell'acconto dovuto è versato alla scadenza della prima rata e il residuo importo alla scadenza della seconda.» and «b) per la seconda o unica rata, entro l'ultimo giorno dell'undicesimo mese successivo a quello di chiusura del periodo d'imposta».
- «Istruzioni IRAP 2026» § 5.2: «la seconda, pari al residuo 60 per cento, entro il 30 novembre 2026 ovvero per i soggetti diversi dalle società o associazioni di cui all'articolo 5 del Tuir entro l'ultimo giorno dell'undicesimo mese del periodo di imposta.»; § 5.1: saldo «entro l'ultimo giorno del mese successivo a quello di approvazione del bilancio» for late approval; § 5.4: «Sono esclusi dalla rateazione solo gli importi a titolo di seconda o unica rata di acconto.»; «Sulle somme rateizzate sono dovuti gli interessi nella misura del 4 per cento annuo, da calcolarsi secondo il metodo commerciale [...]. Sugli importi da versare con le rate mensili successive, si applicano gli interessi dello 0,33 per cento in misura forfetaria»; «In ogni caso il pagamento rateale deve essere completato entro il 16 dicembre dello stesso anno di presentazione della dichiarazione (art. 20, comma 1, del d.lgs. 9 luglio 1997, n. 241)».
- D.P.R. 435/2001 art. 17 c. 3 lett. b: «per la seconda rata, nel mese di novembre, ad eccezione di quella dovuta dai soggetti all'imposta sul reddito delle persone giuridiche e all'imposta regionale sulle attività produttive il cui periodo d'imposta non coincide con l'anno solare, che effettuano il versamento di tale rata entro l'ultimo giorno dell'undicesimo mese dello stesso periodo d'imposta». Art. 17 c. 2: payments «possono essere effettuati entro il trentesimo giorno successivo ai termini ivi previsti, maggiorando le somme da versare dello 0,40 per cento a titolo di interesse corrispettivo».
- D.L. 21/2026 art. 3 c. 2 (Normattiva): «Nella determinazione dell'acconto dell'imposta regionale sulle attività produttive dovuto per il periodo d'imposta successivo a quello in corso al 31 dicembre 2025 si assume, quale imposta del periodo precedente, quella che si sarebbe determinata applicando le disposizioni di cui al comma 1.»

**Code implication.**
- `pack.py` `CompanyAdvanceLaw`: `advance_percent_of_base=100`, `40/60`, `50/50` ISA, `no_advance_at_or_below=20.66`, `balance_months_after_year_end=6`, `second_advance_month_of_year=11` all agree with the sources and stay. The single-payment fields change as in §5.
- `payment_data` instalment rule (`last_instalment 16 December`, `annual_interest_percent 4.00`, `later_instalment_interest_percent 0.33`, `DEFERRAL 0.40/30`) agrees; no change.
- The ISA 50/50 split applies to «soggetti che esercitano attività economiche per le quali sono stati approvati gli indici sintetici di affidabilità fiscale [...] e che dichiarano ricavi o compensi di ammontare non superiore al limite stabilito» (IRAP 2026 § 5.2): the profile's `isa` flag must mean «ISA applies and revenue is within the ISA limit», not only «ISA applies». `[INFERENCE]` that today's flag has the narrower meaning; check where `isa` is set.
- A5: the REB-1005 item «IRES and IRAP advances and instalments for a company» can be removed from the accountant list; rows 197-200, 215-218 stay «verified» except the two amended in §5.

---

## 8. Cash or accrual basis for an srl (art. 109 TUIR)

**Question.** Is an srl's income on the cash or the accrual basis?

**Verdict: settled.** Accrual («competenza»), for IRES and for IRAP; cash is not available to an srl.

**Answer.** An srl is bound to full accounting (art. 13 lett. a and art. 14 DPR 600/1973); the simplified, cash-basis regime of art. 18 DPR 600/1973 and art. 66 TUIR reaches only the subjects of art. 13 lett. c and d (snc, sas and similar, individual businesses). The srl's taxable income starts from the profit or loss of the income statement and its items enter in the year of competence (art. 83 c. 1 and art. 109 c. 1 TUIR); the IRAP base is the difference between the values and costs of production of art. 2425 c.c. «così come risultanti dal conto economico dell'esercizio» (art. 5 c. 1 D.Lgs. 446/1997). The estimate's `COMPANY_ACCRUAL` is right.

**Source.**
- D.P.R. 22 dicembre 1986 n. 917 (TUIR) art. 109 c. 1, Normattiva, text in force on 2026-11-12 (https://www.normattiva.it/uri-res/N2Ls?urn:nir:presidente.repubblica:decreto:1986-12-22;917!vig=2026-11-12~art109): «I ricavi, le spese e gli altri componenti positivi e negativi, per i quali le precedenti norme della presente Sezione non dispongono diversamente, concorrono a formare il reddito nell'esercizio di competenza; tuttavia i ricavi, le spese e gli altri componenti di cui nell'esercizio di competenza non sia ancora certa l'esistenza o determinabile in modo obiettivo l'ammontare concorrono a formarlo nell'esercizio in cui si verificano tali condizioni.» Art. 83 c. 1: «Il reddito complessivo è determinato apportando all'utile o alla perdita risultante dal conto economico, relativo all'esercizio chiuso nel periodo d'imposta, le variazioni in aumento o in diminuzione conseguenti all'applicazione dei criteri stabiliti nelle successive disposizioni della presente sezione.» Art. 66 c. 1: «Il reddito d'impresa dei soggetti che, secondo le disposizioni del decreto del Presidente della Repubblica 29 settembre 1973, n. 600, applicano il regime di contabilità semplificata, è costituito dalla differenza tra l'ammontare dei ricavi di cui all'articolo 85 e degli altri proventi di cui all'articolo 89 percepiti nel periodo d'imposta e quello delle spese sostenute nel periodo stesso».
- D.P.R. 29 settembre 1973 n. 600 art. 13 (Normattiva, `!vig=2026-11-12`): «a) le società soggette all'imposta sul reddito delle persone giuridiche; [...] c) le società in nome collettivo, le società in accomandita semplice e le società ad esse equiparate [...]; d) le persone fisiche che esercitano imprese commerciali»; art. 14: «Le società, gli enti e gli imprenditori commerciali di cui al primo comma dell'art. 13 devono in ogni caso tenere: a) il libro giornale e il libro degli inventari». Art. 18 c. 1: «i soggetti indicati alle lettere c) e d) del primo comma dell'articolo 13, qualora i ricavi [...] non abbiano superato l'ammontare di 500.000 euro per le imprese aventi per oggetto prestazioni di servizi, ovvero di 800.000 euro per le imprese aventi per oggetto altre attività, sono esonerati per l'anno successivo dalla tenuta delle scritture contabili».
- D.Lgs. 446/1997 art. 5 c. 1 (Normattiva): «la base imponibile è determinata dalla differenza tra il valore e i costi della produzione di cui alle lettere A) e B) dell'articolo 2425 del codice civile, con esclusione delle voci di cui ai numeri 9), 10), lettere c) e d), 12) e 13), nonché dei componenti positivi e negativi di natura straordinaria derivanti da trasferimenti di azienda o di rami di azienda, così come risultanti dal conto economico dell'esercizio.»

**Code implication.**
- `pack.py` `IT_COMPANY_PACK.laws[0].income_basis = COMPANY_ACCRUAL` and the comment «A company's income accrues (art. 83 and 109 TUIR; IRAP, art. 5 D.Lgs. 446/1997)» are correct; no value changes.
- **Citation numbering for 2027.** D.Lgs. 19 giugno 2026 n. 117 («Testo unico delle disposizioni legislative in materia di imposte sui redditi», GU n. 152 of 3-7-2026, S.O. 26, https://www.gazzettaufficiale.it/eli/gu/2026/07/03/152/so/26/sg/pdf , read 2026-11-12) is in force since 4-7-2026, but its art. 377 reads «Le disposizioni del presente testo unico si applicano a decorrere dal 1° gennaio 2027»; Normattiva's default view of old art. 109 reads «Testo in vigore dal: 1-1-2027» and «PROVVEDIMENTO ABROGATO DAL D.LGS. 19 GIUGNO 2026, N. 117». For the 2026 estimate the TUIR numbers stay; from the 2027 year the same rules are art. 118 (old 109; the text is word for word, «(articolo 109 decreto del Presidente della Repubblica 22 dicembre 1986, n. 917; ...)»), art. 92 (old 83), art. 75 (old 66, «Imprese minori»), art. 56 (old 54), art. 82 (old 73), art. 86 (old 77). A5 rows that cite «TUIR art. N ... Normattiva, in force from 4-7-2026 / 12-8-2026» (REB-1066) should say «in force for 2026; from 1-1-2027 art. M of D.Lgs. 117/2026». Likewise D.P.R. 600/1973 art. 14 and 18 are annotated «ARTICOLO ABROGATO DAL D.LGS. 5 AGOSTO 2026, N. 141» by the default Normattiva view (effective date not read here; `[INFERENCE]` also 2027).

---

## What no source settles, and what I did not read

For the accountant (two points, both small):
1. **Seasonal 70% with the 1,850 on the same worker** (§3): may a seasonal worker who qualifies under c. 4-octies also take the 1,850 of c. 4-bis.1 for the same days? Read: art. 11 c. 4-octies and c. 4-bis.1, c. 4-septies, the IRAP 2026 instructions IS5 to IS10, Circolare 20/E 2016 § 2 («al netto di eventuali deduzioni spettanti», the 2016 wording). The statute does not exclude it and no text says it is allowed; c. 4-septies caps only c. 1 and c. 4-bis.1.
2. **Trento code 010 for a company with no employees** (§1): the row speaks of not respecting collective agreements «nei confronti della generalita' o di intere categorie di dipendenti». Read: the dataset text only; the provincial law was not read. `[INFERENCE]` a company without staff pays the 2.68 of code 001.

Read-yet-not-enough (not for the accountant, for the next person who builds the pack):
- The ATECO lists of Emilia-Romagna 002 (annex to L.R. 31/3/2025 n. 1), Veneto 017 (the file's text is cut), Marche 011 (full conditions), and the text of Toscana L.R. 79/2012 art. 1-bis (row 006) are in the regional laws, which were not read. Do not list those codes in the pack from the dataset text alone.
- The dataset's «Tabella 1» of D.L. 21/2026 (ATECO codes) is quoted only as the dataset names it in the Veneto rows; the annex itself was not read.
- The arithmetic of §2 for a period containing 29 February (365 or 366 as denominator).

---

## Appendix A. Every rate row of the 2026 dataset, by region

Source: the CSV of §1, read 2026-11-12, 449 rate rows (the 21 `CODICE_ALI` 001 rows are the pack's). «Facts that select the row» is a mechanical tagging of the Italian text by keywords: the quote governs. Rows tagged *activity in D.L. 21/2026 table 1 (yes/no)* mention the table either as the condition (the company is in it) or as the exclusion (the company is not in it); read the quote.


### Piemonte (`01`; Finanze ID_IRAP 2313; DATA_PUBBLICAZIONE 01-APR-26; 45 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 2.98 | ATECO / activity-code list | Aliquota agevolata per l'editoria e l'informazione locale. |
| 003 | 2.98 | site in a listed municipality/area | Aliquota agevolata per le attivita' economiche esercitate in comuni ad alta marginalita'. |
| 004 | 4.82 | gaming machines in the premises | Aliquota maggiorata per esercizi nei quali sono presenti uno o piu' apparecchi da gioco individuate dall'art. 4 comma 7 L.R. 8/2013 di cui all'art. 110, comma 6 e 7, del … |
| 005 | 4.20 | concessionaire | Aliquota per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori. |
| 006 | 3.28 | concessionaire; site in a listed municipality/area | Aliquota agevolata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori che operano nei c… |
| 007 | 5.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota maggiorata per le banche ed altri enti e societa' finanziarie di cui all'art. 6 commi 2,3,4 e 9 del d.lgs. 446/1997. |
| 008 | 4.65 | bank/financial company (art. 6 c. 1, 7); site in a listed municipality/area | Aliquota per le banche ed altri enti e societa' finanziarie di cui all'art. 6 commi 2,3,4 e 9 del d.lgs. 446/1997 che operano nei comuni ad alta marginalita'. |
| 009 | 8.82 | insurer (art. 7) | Aliquota maggiorata per le imprese di assicurazioni di cui all' art. 7 del d.lgs.446/1997 |
| 010 | 7.90 | insurer (art. 7); site in a listed municipality/area | Aliquota per le imprese di assicurazioni di cui all'art. 7 del d.lgs. 446/1997 che operano nei comuni ad alta marginalita'. |
| 011 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici. |
| 012 | 0.00 | ASP/IPAB | Aliquota azzerata per le istituzioni pubbliche di assistenza e beneficienza (IPAB) |
| 013 | 3.00 | ATECO / activity-code list | Aliquota agevolata per le attivita' ricreative,artistiche e di intrattenimento - CODICE ATECO 90 |
| 014 | 3.00 | ATECO / activity-code list | Aliquota agevolata per le attivita' di biblioteche, archivi e musei e altre attivita' culturali. CODICE ATECO 91. |
| 015 | 2.98 | new business / transfer into the region | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, che applicano al valore della produzione net… |
| 016 | 3.28 | concessionaire; new business / transfer into the region | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, che esercitano attivita' di imprese concessi… |
| 017 | 4.65 | new business / transfer into the region | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, di cui all'art. 6 del D.Lgs. 446/97, commi 2… |
| 018 | 7.90 | insurer (art. 7); new business / transfer into the region | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, di cui all'art. 7 del D.Lgs. 446/97 (imprese… |
| 019 | 3.73 | new business / transfer into the region; site in a listed municipality/area | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, di cui all'art. 6, commi 2,3,4 e 9 del D.Lgs… |
| 020 | 6.98 | insurer (art. 7); new business / transfer into the region; site in a listed municipality/area | Aliquota ridotta per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nel territorio regionale, di cui all'art. 7 del D.Lgs. 446/97 (imprese… |
| 021 | 0.00 | ASP/IPAB | Aliquota azzerata per le aziende pubbliche di servizi alla persona (APSP), anche succedute alle IPAB |
| 022 | 0.00 | other (read the quote) | Enti che svolgono le attivita' indicate all' articolo 5, comma 1, lettere a), b) e c), del decreto legislativo 117/2017. |
| 023 | 1.90 | third-sector / cooperative / sports-club status | Cooperative sociali iscritte nell'apposita sezione di cui all'articolo 46 del medesimo decreto legislativo. |
| 024 | 3.00 | third-sector / cooperative / sports-club status | Centri di servizio del volontariato di cui all' articolo 61 del decreto legislativo 117/2017 , iscritte nell'apposita sezione di cui all'articolo 46 del medesimo decreto … |
| 025 | 0.00 | third-sector / cooperative / sports-club status | Organizzazioni di volontariato iscritte al RUNTS nella sezione di cui all'art. 46 del d.lgs. 117/2017. |
| 026 | 6.82 | ATECO / activity-code list | Attivita' di produzione, trasmissione e distribuzione di energia elettrica, di cui al codice ATECO 35.1 |
| 027 | 4.82 | ATECO / activity-code list | Attivita' di commercio al dettaglio in esercizi non specializzati con prevalenza di prodotti alimentari e bevande, di cui al codice ATECO 47.1 |
| 028 | 4.82 | ATECO / activity-code list | Attivita' di servizi finanziari, escluse le assicurazioni e i fondi pensione, di cui al codice ATECO 64 |
| 029 | 4.82 | ATECO / activity-code list | Attivita' ausiliarie dei servizi finanziari, escluse le assicurazioni e i fondi pensione, di cui al codice ATECO 66.1 |
| 030 | 4.82 | ATECO / activity-code list | Attivita' ausiliarie delle assicurazioni e dei fondi pensione, di cui al codice ATECO 66.2 |
| 031 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 032 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); site in a listed municipality/area | aliquota agevolata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 033 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | aliq. agevolata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n… |
| 034 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 035 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); site in a listed municipality/area | aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 036 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); site in a listed municipality/area | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 037 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 038 | 6.65 | bank/financial company (art. 6 c. 1, 7); site in a listed municipality/area | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 operanti nei comuni ad alta marginalita' |
| 039 | 6.65 | bank/financial company (art. 6 c. 1, 7); new business / transfer into the region | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 di nuova costituzione o che trasferiscono l'… |
| 040 | 5.73 | bank/financial company (art. 6 c. 1, 7); new business / transfer into the region; site in a listed municipality/area | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 di nuova costituzione o che trasferiscono l'… |
| 041 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 042 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); site in a listed municipality/area | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 043 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 044 | 5.73 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region; site in a listed municipality/area | aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 045 | 2.98 | ATECO / activity-code list | Aliquota agevolata per i soggetti che svolgono l'attivita' di commercio al dettaglio di giornali ed altre pubblicazioni periodiche con codice ATECO 47.62.10 |

### Valle d'Aosta (`02`; Finanze ID_IRAP 2401; DATA_PUBBLICAZIONE 05-GIU-26; 25 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 2.98 | value and staff-cost growth vs 3-year average | Aliquota agevolata per i soggetti passivi che, alla chiusura del singolo periodo d'imposta, realizzino, su base nazionale, entrambi i seguenti presupposti: a) un incremen… |
| 003 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota ordinaria per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 004 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota ordinaria per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle eserc… |
| 005 | 3.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Agevolazione per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, … |
| 006 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota ordinaria per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 2… |
| 007 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Agevolazione per per le imprese concessionarie che esercitano, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L… |
| 008 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota ordinaria per banche ed altri enti e societa' finanziari di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 009 | 5.73 | bank/financial company (art. 6 c. 1, 7); value and staff-cost growth vs 3-year average | Agevolazione per banche ed altri enti e societa' finanziari di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997, che alla chiusura del singolo periodo d'imposta,… |
| 010 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota ordinaria per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento … |
| 011 | 3.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Agevolazione per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capi… |
| 012 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota ordinaria per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, l… |
| 013 | 5.73 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Agevolazione per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le atti… |
| 014 | 7.90 | insurer (art. 7) | Aliquota ordinaria per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 |
| 015 | 6.98 | insurer (art. 7); value and staff-cost growth vs 3-year average | Agevolazione per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 che alla chiusura del singolo periodo d'imposta, realizzino, su base nazionale, entram… |
| 016 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli enti del Terzo settore di cui all' articolo 82 del d.lgs. 117/2017, comprese le cooperative sociali ed escluse le imprese sociali costituite in forma di… |
| 017 | 0.00 | ASP/IPAB | Esenzione per le Aziende pubbliche di servizi alla persona (ASP) |
| 018 | 0.00 | other (read the quote) | Esenzione per i centri polifunzionali di servizio individuati dall'art. 12 della L.R. n. 12/99. |
| 019 | 0.00 | ATECO / activity-code list | Esenzione per i soggetti che esercitano in via esclusiva l'attivita' di rifugio alpino ai sensi del capo IV della legge regionale 29/05/1996, n. 11 (Disciplina delle stru… |
| 020 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli Enti cooperativi a mutualita' prevalente, iscritti nel registro regionale degli Enti cooperativi di cui all'art. 3, della L.R. 27/98, soggetti all'aliqu… |
| 021 | 0.00 | new business / transfer into the region | Esenzione per i soggetti passivi che intraprendono stabilmente nuove iniziative economiche nel territorio regionale nel periodo d'imposta in corso al 1 gennaio 2022. L'es… |
| 022 | 0.00 | other (read the quote) | Esenzione per imprese industriali e centri di ricerca che hanno insediato una loro attivita' produttiva nelle aree individuate con deliberazione della Giunta regionale di… |
| 023 | 0.00 | other (read the quote) | Esenzione dal 1 gennaio 2026 sulle attivita' non commerciali delle universita' non statali gestite da enti pubblici e aventi sede legale e operativa nel territorio region… |
| 024 | 8.50 | public body | Aliquota amministrazioni ed enti pubblici. |
| 025 | 0.00 | third-sector / cooperative / sports-club status | Esenzione dal 1 gennaio 2026 per gli enti di cui all'articolo 101, comma 8, secondo periodo, del d.lgs. 117/2017, qualificati come ONLUS fino al 2025 che, a causa della d… |

### Lombardia (`03`; Finanze ID_IRAP 2312; DATA_PUBBLICAZIONE 30-MAR-26; 19 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 2.98 | new business / transfer into the region; site in a listed municipality/area | Aliquota agevolata per le nuove imprese costituite nei piccoli comuni individuati nei provvedimenti attuativi, nei limiti e alle condizioni previste dalla L.R. 10/2003 e … |
| 003 | 2.98 | other (read the quote) | Aliquota agevolata per le scuole dell'infanzia autonome di cui all'art. 7 ter, della L.R. 6/08/2007 n. 19 |
| 004 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 005 | 3.28 | concessionaire | Aliquota agevolata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che fruiscono d… |
| 006 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota maggiorata per banche ed altri enti e societa' finanziari di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446/97 |
| 007 | 8.82 | insurer (art. 7) | Aliquota maggiorata per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 |
| 008 | 8.50 | public body | Aliquota Amministrazioni ed Enti pubblici |
| 009 | 0.00 | other (read the quote) | Esenzione applicata alle Aziende lombarde per l'edilizia residenziale (ALER) ai sensi dell'art. 5, c. 1, del D.Lgs. 68/2011. Decisione della Commissione 2012/21/UE del 20… |
| 010 | 0.00 | ASP/IPAB | Esenzione per le Aziende pubbliche di servizi alla persona (ASP) succedute alle Istituzioni pubbliche di assistenza e beneficienza (IPAB) |
| 011 | 2.90 | ATECO / activity-code list; SME status / turnover / exports / INAIL premium | Agevolazione per le micro, piccole e medie imprese che esercitano attivita' economiche di proiezione cinematografica di cui al codice ATECO 591400. Reg. (UE) n. 2831/2023 |
| 012 | 0.00 | new business / transfer into the region; site in a listed municipality/area | Esenzione per le nuove imprese e per le sedi o per le unita' locali di nuova iscrizione al registro delle imprese di cui all'articolo 77, comma 6 octies, della L.R 14 lug… |
| 013 | 4.65 | ATECO / activity-code list | Aliquota ordinaria riguardante attivita' economiche ATECO 2007 relative a tutti i codici a sei cifre diversi dai settori 64, 65 e 66, qualora l'attivita' sia svolta da so… |
| 014 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le ex ONLUS che abbiano perso tale qualifica a seguito dell'iscrizione nel RUNTS purche' iscritte nell'Anagrafe delle Onlus alla data 22 novembre 2021 o in … |
| 015 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per organizzazioni di volontariato iscritte nella sezione a) del RUNTS di cui all'articolo 45 del d.lgs. 117/2017 e cooperative sociali di cui alla legge 381/19… |
| 016 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 017 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 018 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 019 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |

### Bolzano (`BZ`; Finanze ID_IRAP 2501; DATA_PUBBLICAZIONE 12-OTT-26; 15 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in v… |
| 003 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota per banche ed altri enti e societa' finanziari di cui all'art. 6 commi 1 e 7 del D.Lgs. n. 446/97. |
| 004 | 7.90 | insurer (art. 7) | Aliquota per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 |
| 005 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici |
| 006 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le organizzazioni di volontariato e per le associazioni di promozione sociale di cui alle lettere a) e b) del comma 1 dell'articolo 46 del decreto legislati… |
| 007 | 0.00 | ASP/IPAB | Esenzione per le aziende pubbliche di servizi alla persona (APSP) |
| 008 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli enti del Terzo settore di cui all'articolo 82, comma 1, del decreto legislativo 3 luglio 2017, n. 117 |
| 009 | 2.68 | collective agreement / extra pay for staff | Aliquota ordinaria ridotta per i soggetti passivi che erogano elementi economici aggiuntivi ai propri dipendenti in applicazione di contratti collettivi territoriali inte… |
| 010 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 011 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 012 | 4.68 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota per i soggetti che erogano elementi economici aggiuntivi ai propri dipendenti in applicazione di contratti collettivi territoriali integrativi, accordi aziendali… |
| 013 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 014 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 015 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per i soggetti passivi iscritti nell'elenco provinciale degli enti che svolgono attivita' di interesse generale, di cui all'art. 2 della legge provinciale 8 lug… |

### Trento (`TN`; Finanze ID_IRAP 2302; DATA_PUBBLICAZIONE 20-MAR-26; 19 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 2.68 | activity in D.L. 21/2026 table 1 (yes/no); collective-agreement compliance (the row excludes code 010/011) | Aliquota ridotta per i soggetti di cui all'art.16, co 1, D.Lgs. n. 446/97 che non rientrano nel codice 010 e che non esercitano, in via prevalente, le attivita' economich… |
| 002 | 2.00 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff; collective-agreement compliance (the row excludes code 010/011) | Aliquota ulteriormente ridotta per i soggetti di cui all'articolo 16, co 1, del decreto legislativo n. 446 del 1997, che non rientrano nel codice 010, che non esercitano,… |
| 003 | 4.82 | gaming machines in the premises | Aliquota maggiorata per gli esercizi ove sono collocati apparecchi da gioco di cui all'articolo 110, comma 6, del regio decreto 18 giugno 1931, n. 773 |
| 004 | 2.98 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); collective-agreement compliance (the row excludes code 010/011) | Aliquota ridotta per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, che non rientrano … |
| 005 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota maggiorata per le banche, le societa' finanziarie e gli altri enti di cui ai co 1 e 7 dell'art. 6 del D.Lgs. n. 446/97 |
| 006 | 7.90 | insurer (art. 7) | Aliquota maggiorata per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 |
| 007 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per le societa' di partecipazione non finanziaria e assimilati di cui al co 9 dell'art. 6 del D.Lgs. n. 446/97, esercenti, in via prevalente, le attiv… |
| 008 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici |
| 009 | 0.00 | ASP/IPAB | Esenzione per le Aziende pubbliche di servizi alla persona (ASP) succedute alle IPAB |
| 010 | 4.82 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota maggiorata per i soggetti di cui all'art.16, co 1, D.Lgs. n. 446/97, che non esercitano, in via prevalente, le attivita' economiche individuate dai codici ATECO … |
| 011 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, che non esercit… |
| 012 | 0.00 | new business / transfer into the region | Azzeramento d'aliquota per i primi cinque anni d'imposta per le nuove iniziative produttive intraprese nel territorio provinciale a decorrere dal periodo d'imposta succes… |
| 013 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento a capitale variabile di cui ai co 2, 3,… |
| 014 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli enti del Terzo settore di cui all'art. 82, comma 1, del D.Lgs. n. 117 del 2017, a decorrere dal periodo d'imposta in corso alla data del 6 agosto 2024 |
| 015 | 4.68 | activity in D.L. 21/2026 table 1 (yes/no); collective-agreement compliance (the row excludes code 010/011) | Aliquota per i soggetti di cui all'art.16, co 1, D.Lgs. n. 446/97 che non rientrano nel codice 010 e che esercitano, in via prevalente, le attivita' economiche individuat… |
| 016 | 4.00 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff; collective-agreement compliance (the row excludes code 010/011) | Aliquota per i soggetti di cui all'art.16, co1, D.Lgs. n. 446/97 che non rientrano nel codice 010, che esercitano, in via prevalente, le attivita' economiche individuate … |
| 017 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota maggiorata per i soggetti di cui all'art.16, co 1, D.Lgs. n. 446/97, che esercitano, in via prevalente, le attivita' economiche individuate dai codici ATECO di c… |
| 018 | 4.98 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); collective-agreement compliance (the row excludes code 010/011) | Aliquota per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, che non rientrano nel codi… |
| 019 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, che esercitano,… |

### Veneto (`05`; Finanze ID_IRAP 2421; DATA_PUBBLICAZIONE 09-GIU-26; 26 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.08 | general | Maggiorazione di aliquota generalizzata a decorrere dall'anno d'imposta successivo a quello in corso al 31/12/2024, ad esclusione dei soggetti passivi di cui alle aliquot… |
| 002 | 2.90 | new business / transfer into the region; founders young/women | Aliquota agevolata per le nuove imprese giovanili che si sono costituite nel territorio regionale negli anni 2024-2026 in possesso dei requisiti di cui all'art. 2 della L… |
| 003 | 2.90 | new business / transfer into the region; founders young/women | Aliquota agevolata per le nuove imprese femminili che si sono costituite nel territorio regionale negli anni 2024-2026 in possesso dei requisiti di cui all'art. 2 della L… |
| 004 | 3.35 | third-sector / cooperative / sports-club status | Aliquota agevolata per le cooperative sociali di cui all'art. 2, co. 1, lett. a) della L.R. n. 23/2006 iscritte nella sez. A dell'albo regionale delle cooperative sociali… |
| 005 | 2.35 | third-sector / cooperative / sports-club status | Aliquota agevolata per le cooperative sociali di cui all'art. 2, co. 1, lett. a) della L.R. n. 23/2006 iscritte nella sez. A dell'albo regionale delle cooperative sociali… |
| 006 | 2.90 | third-sector / cooperative / sports-club status; new business / transfer into the region | Aliquota agevolata per le nuove cooperative sociali di cui all'art. 2, co. 1, lett. a) delle L.R. n. 24/1994 e successive modificazioni e loro nuovi consorzi che si sono … |
| 007 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le cooperative sociali di cui all'art. 2, co. 1, lett. b) della L.R. n. 23/2006 iscritte nella sez. B dell'albo regionale delle cooperative sociali di cui a… |
| 008 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le cooperative sociali di cui all'art. 2, co. 1, lett. b) della L.R. n. 23/2006 iscritte nella sez. B dell'albo regionale delle cooperative sociali di cui a… |
| 009 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota maggiorata per banche ed altri enti e societa' finanziari di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446/97. |
| 010 | 8.82 | insurer (art. 7) | Aliquota maggiorata per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97. |
| 011 | 4.38 | concessionaire | Aliquota maggiorata a decorrere dall'anno d'imposta successivo a quello in corso al 31/12/2024, per i soggetti che esercitano attivita' di imprese concessionarie diverse … |
| 012 | 4.82 | gaming machines in the premises | Aliquota maggiorata per gli esercizi nei quali risultano installati apparecchi da gioco di cui all'articolo 110, commi 6 e 7, del regio decreto 18 giugno 1931, n. 773 |
| 013 | 8.50 | public body | Amministrazioni ed Enti pubblici - attivita' istituzionale. |
| 014 | 3.90 | other (read the quote) | Aliquota agevolata per le Istituzioni Pubbliche di Assistenza e Beneficenza - Attivita' istituzionale. |
| 015 | 4.82 | bank/financial company (art. 6 c. 1, 7); insurer (art. 7); ATECO / activity-code list | Aliquota maggiorata, a decorrere dall'anno d'imposta successivo a quello in corso al 31/12/2022, per le attivita' economiche rientranti nei settori contraddistinti dai co… |
| 016 | 5.12 | concessionaire; ATECO / activity-code list | Aliquota maggiorata, a decorrere dall'anno d'imposta successivo a quello in corso al 31/12/2022, per i soggetti che esercitano attivita' di imprese concessionarie diverse… |
| 017 | 4.55 | ATECO / activity-code list | Aliquota maggiorata, a decorrere dall'anno d'imposta successivo a quello in corso al 31.12.2024, per le attivita' economiche rientranti nei settori contraddistinti dai co… |
| 018 | 4.85 | concessionaire; ATECO / activity-code list | Aliquota maggiorata, a decorrere dall'anno d'imposta successivo a quello in corso al 31.12.2024, per i soggetti che esercitano attivita' di imprese concessionarie diverse… |
| 019 | 6.08 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 06 e 49.50.1 di cui all'art. 3, comma 1, del D.L. 20 febbraio 2… |
| 020 | 6.55 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 09.1 di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. … |
| 021 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 19.2, 35.1, 35.2, 35.4 di cui all'art. 3, comma 1, del D.L. 20 … |
| 022 | 6.38 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 06 e 49.50.1 di cui all'art. 3, comma 1, del D.L… |
| 023 | 6.85 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 09.1 di cui all'art. 3, comma 1, del D.L. 20 feb… |
| 024 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO 19.2, 35.1, 35.2, 35.4 di cui all'art. 3, comma … |
| 025 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investim… |
| 026 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |

### Friuli-Venezia Giulia (`06`; Finanze ID_IRAP 2383; DATA_PUBBLICAZIONE 21-MAG-26; 67 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | ALIQUOTA ORDINARIA |
| 002 | 4.20 | concessionaire | ALIQUOTA ORDINARIA per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori |
| 003 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | ALIQUOTA ORDINARIA per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento … |
| 004 | 7.90 | insurer (art. 7) | ALIQUOTA ORDINARIA per le imprese di assicurazione di cui all'art. 7 del D.Lgs. 446/97 come modificata dall'articolo 1, comma 74 della L.199/2025 (Legge di bilancio 2026) |
| 005 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici |
| 006 | 0.00 | site in a listed municipality/area | Aliquota ridotta per le imprese insediate nelle zone di svantaggio socio-economico del territorio montano (Reg. UE 2831/2023-1408/2013) |
| 007 | 0.00 | concessionaire; site in a listed municipality/area | Aliquota ridotta per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori insediati nelle zon… |
| 008 | 0.00 | bank/financial company (art. 6 c. 1, 7); site in a listed municipality/area | Aliquota ridotta per le banche ed altri enti e societa' finanziari di cui all'art. 6 del D.Lgs. n. 446/97 insediate nelle zone di svantaggio socio-economico del territori… |
| 009 | 0.00 | insurer (art. 7); site in a listed municipality/area | Aliquota ridotta per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 insediate nelle zone di svantaggio socio-economico del territorio montano (Reg. UE… |
| 010 | 2.98 | headcount 1-5 employees | Aliquota ridotta per imprese aventi almeno 1 e non piu' di 5 dipendenti (Reg. UE 2831/2023) |
| 011 | 2.98 | headcount 1-5 employees | Aliquota ridotta per esercenti arti e professioni con almeno 1 dipendente e volume d'affari non superiore a 120 mila euro (Reg. UE 2831/2023) |
| 012 | 3.28 | concessionaire; headcount 1-5 employees | Aliquota ridotta per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori aventi almeno 1 e n… |
| 013 | 3.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); headcount 1-5 employees | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 014 | 6.98 | insurer (art. 7); headcount 1-5 employees | Aliquota ridotta, in ragione delle modifiche introdotte dall'articolo 1, comma 74 della L.199/2025 (Legge di bilancio 2026), per le imprese di assicurazione di cui all'ar… |
| 015 | 3.50 | R&D staff cost increase | Aliquota ridotta a favore dei soggetti passivi Irap che incrementano il costo per il personale addetto alla ricerca e sviluppo (Reg. UE 2831/2023-1408/2013-717/2014) |
| 016 | 3.80 | concessionaire; R&D staff cost increase | Aliquota ridotta per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori che incrementano il… |
| 017 | 4.25 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); R&D staff cost increase | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 018 | 7.50 | insurer (art. 7); R&D staff cost increase | Aliquota ridotta, in ragione delle modifiche introdotte dall'art.1, comma 74 della L.199/2025 (Legge di bilancio 2026), per le imprese di assicurazione di cui all'art. 7 … |
| 019 | 2.98 | gaming machines in the premises | Aliquota ridotta per gli esercizi pubblici, commerciali e i circoli privati che provvedono volontariamente alla disinstallazione dai propri locali di tutti gli apparecchi… |
| 020 | 4.82 | gaming machines in the premises | Aliquota maggiorata per gli esercizi pubblici, commerciali e i circoli privati nei cui locali sono installati apparecchi per il gioco lecito di cui all'articolo 110, comm… |
| 021 | 0.00 | ASP/IPAB | Esenzione per le Aziende Pubbliche di Servizi alla persona (ASP) - a decorrere dal periodo di imposta in corso all'1 gennaio 2023 concessa ai sensi della normativa europe… |
| 022 | 3.17 | new business / transfer into the region | Aliquota ridotta per un massimo di 5 periodi d'imposta alle nuove imprese artigiane aventi i requisiti previsti dalla legge regionale (Reg. UE 2831/2023) |
| 023 | 2.98 | value and staff-cost growth vs 3-year average | Aliquota ridotta per i soggetti passivi previsti dalla legge regionale che, alla chiusura del singolo periodo d'imposta presentino: a) un incremento del valore della prod… |
| 024 | 3.28 | concessionaire; value and staff-cost growth vs 3-year average | Aliquota ridotta per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori che, alla chiusura … |
| 025 | 3.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 026 | 6.98 | insurer (art. 7); value and staff-cost growth vs 3-year average | Aliquota ridotta, in ragione delle modifiche introdotte dall'art.1, comma 74, della L.199/2025 (Legge di bilancio 2026), per le imprese di assicurazione di cui all'art. 7… |
| 027 | 0.00 | new business / transfer into the region | Riduzione dell'aliquota Irap per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nella regione Friuli Venezia Giulia per cinque periodi di … |
| 028 | 0.00 | concessionaire; new business / transfer into the region | Riduzione dell'aliquota Irap per i soggetti che esercitano attivita' di imprese concessionarie, diverse da quelle di costruzione e gestione di autostrade e trafori, di nu… |
| 029 | 0.00 | bank/financial company (art. 6 c. 1, 7); new business / transfer into the region | Riduzione dell'aliquota Irap per le banche ed altri enti e societa' finanziari, di cui all'art. 6 del D.Lgs. n.446/97, di nuova costituzione o che trasferiscono l'insedia… |
| 030 | 0.00 | insurer (art. 7); new business / transfer into the region | Riduzione, in ragione delle modifiche introdotte dall'art.1, comma 74, della L. 199/2025 ( Legge di bilancio 2026), dell'aliquota Irap per le imprese di assicurazione, di… |
| 031 | 1.00 | new business / transfer into the region | Riduzione dell'aliquota Irap per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nella regione Friuli Venezia Giulia per cinque periodi di … |
| 032 | 1.30 | concessionaire; new business / transfer into the region | Riduzione dell'aliquota Irap per i soggetti che esercitano attivita' di imprese concessionarie, diverse da quelle di costruzione e gestione di autostrade e trafori, di nu… |
| 033 | 1.75 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 034 | 5.00 | insurer (art. 7); new business / transfer into the region | Riduzione, in ragione delle modifiche introdotte dall'art.1, comma 74, della L.199/2025 (Legge di bilancio 2026), dell'aliquota Irap per le imprese di assicurazione, di c… |
| 035 | 2.90 | collective agreement / extra pay for staff | Riduzione dell'aliquota Irap per i soggetti passivi Irap che, alla chiusura del periodo d'imposta considerato: - applichino o sottoscrivano contratti collettivi nazionali… |
| 036 | 3.20 | concessionaire; collective agreement / extra pay for staff | Riduzione dell'aliquota Irap per soggetti che esercitano attivita' di imprese concessionarie, diverse da quelle di costruzione e gestione di autostrade e trafori, che, al… |
| 037 | 3.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 038 | 6.90 | insurer (art. 7); collective agreement / extra pay for staff | Riduzione, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), dell'aliquota Irap per le imprese di assicurazione, di cu… |
| 039 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli enti del Terzo settore di cui al decreto legislativo 3 luglio 2017, n. 117 (Codice del terzo settore), comprese le cooperative sociali ed escluse le imp… |
| 040 | 6.65 | bank/financial company (art. 6 c. 1, 7) | ALIQUOTA ORDINARIA, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), per le banche, le societa' finanziarie e gli alt… |
| 041 | 5.73 | bank/financial company (art. 6 c. 1, 7); headcount 1-5 employees | Aliquota ridotta, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), per le banche, le societa' finanziarie e gli altri… |
| 042 | 6.25 | bank/financial company (art. 6 c. 1, 7); R&D staff cost increase | Aliquota ridotta, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), per le banche, le societa' finanziarie e gli altri… |
| 043 | 5.73 | bank/financial company (art. 6 c. 1, 7); value and staff-cost growth vs 3-year average | Aliquota ridotta, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), per le banche, le societa' finanziarie e gli altri… |
| 044 | 3.75 | bank/financial company (art. 6 c. 1, 7); new business / transfer into the region | Riduzione, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), dell'aliquota Irap per le banche, le societa' finanziarie… |
| 045 | 5.65 | bank/financial company (art. 6 c. 1, 7); collective agreement / extra pay for staff | Riduzione, in ragione delle modifiche introdotte dall'art.1, comma 74, della L199/2025 (Legge di bilancio 2026), dell'aliquota Irap per le banche, le societa' finanziarie… |
| 046 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | ALIQUOTA ORDINARIA per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 047 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | ALIQUOTA ORDINARIA per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 2… |
| 048 | 6.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | ALIQUOTA ORDINARIA per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento … |
| 049 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); headcount 1-5 employees | Aliquota ridotta per le imprese aventi almeno 1 e non piu' di 5 dipendenti (Reg. UE 2831/2023) per i soggetti esercenti, in via prevalente, le attivita' economiche indivi… |
| 050 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); headcount 1-5 employees | Aliquota ridotta per le imprese aventi almeno 1 e non piu' di 5 dipendenti (Reg. UE 2831/2023) per i soggetti esercenti, in via prevalente, le attivita' economiche indivi… |
| 051 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); headcount 1-5 employees | Aliquota ridotta per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 … |
| 052 | 5.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); headcount 1-5 employees | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 053 | 5.50 | activity in D.L. 21/2026 table 1 (yes/no); R&D staff cost increase | Aliquota ridotta a favore dei soggetti passivi Irap esercenti in via prevalente le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. … |
| 054 | 5.80 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); R&D staff cost increase | Aliquota ridotta le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febb… |
| 055 | 6.25 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); R&D staff cost increase | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 056 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); gaming machines in the premises | Aliquota ridotta, per gli esercizi pubblici, commerciali e i circoli privati che provvedono volontariamente alla disinstallazione dai propri locali di tutti gli apparecch… |
| 057 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no); gaming machines in the premises | Aliquota maggiorata per gli esercizi pubblici, commerciali e i circoli privati nei cui locali sono installati apparecchi per il gioco lecito di cui all'articolo 110, comm… |
| 058 | 5.17 | activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Aliquota ridotta per un massimo di 5 periodi d'imposta alle nuove imprese artigiane aventi i requisiti previsti dalla legge regionale (Reg. UE 2831/2023) esercenti, in vi… |
| 059 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Aliquota ridotta per i soggetti passivi esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbrai… |
| 060 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Aliquota ridotta per le imprese concessionarie esercenti in via prevalente le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 fe… |
| 061 | 5.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); value and staff-cost growth vs 3-year average | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |
| 062 | 3.00 | activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Riduzione dell'aliquota Irap per i soggetti di nuova costituzione o che trasferiscono l'insediamento produttivo nella regione Friuli Venezia Giulia per cinque periodi di … |
| 063 | 3.30 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Riduzione dell'aliquota Irap per le imprese concessionarie esercenti in via prevalente le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, de… |
| 064 | 3.75 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); new business / transfer into the region | Riduzione di aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimen… |
| 065 | 4.90 | activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Riduzione dell'aliquota Irap per i soggetti passivi Irap esercenti in via prevalente le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del … |
| 066 | 5.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Riduzione dell'aliquota Irap per imprese concessionarie, esercenti in via prevalente le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del … |
| 067 | 5.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); collective agreement / extra pay for staff | Aliquota ridotta per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a … |

### Liguria (`07`; Finanze ID_IRAP 2303; DATA_PUBBLICAZIONE 20-MAR-26; 15 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | aliquota ordinaria |
| 002 | 8.50 | public body | aliquota Amministrazioni ed Enti pubblici |
| 003 | 7.57 | bank/financial company (art. 6 c. 1, 7); non-financial holding (art. 6 c. 9); ATECO / activity-code list | aliquota maggiorata per le banche e per le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. 446 del 1997 e per le societa' di partecipaz… |
| 004 | 8.82 | insurer (art. 7) | aliquota maggiorata per le imprese di assicurazioni |
| 005 | 4.20 | concessionaire | aliquota per imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori |
| 006 | 4.82 | ATECO / activity-code list | Aliquota maggiorata per i soggetti passivi esercenti,in via prevalente, le attivita' economiche comprese nelle categorie e sottocategorie di attivita' economiche individu… |
| 007 | 5.12 | concessionaire; ATECO / activity-code list | aliquota maggiorata per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono attivita' economiche rientranti nei s… |
| 008 | 3.00 | third-sector / cooperative / sports-club status | aliquota agevolata per i seguenti soggetti, limitatamente al valore della produzione netta prodotta nel territorio della Regione: organismi di volontariato di cui alla le… |
| 009 | 2.98 | third-sector / cooperative / sports-club status | aliquota agevolata per associazioni di promozione sociale di cui alla legge 383/2000 gia' iscritte al registro nazionale previsto dall'art.7, comma 3 della legge medesima… |
| 010 | 7.50 | ASP/IPAB | aliquota agevolata per le aziende pubbliche di servizi alla persona (Asp) succedute alle istituzioni pubbliche di assistenza e beneficienza (Ipab) |
| 011 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); ATECO / activity-code list | aliquota maggiorata per le societa' di intermediazione mobiliare e gli intermediari,le societa' di gestione dei fondi comuni di investimento. le societa' di investimento … |
| 012 | 5.90 | ATECO / activity-code list | Aliquota maggiorata per i soggetti passivi esercenti,in via prevalente, le attivita' economiche comprese nelle categorie e sottocategorie di attivita' economiche individu… |
| 013 | 6.20 | concessionaire; ATECO / activity-code list | aliquota maggiorata per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono attivita' economiche rientranti nei s… |
| 014 | 6.82 | ATECO / activity-code list | Aliquota maggiorata per i soggetti passivi esercenti,in via prevalente, le attivita' economiche comprese nelle categorie e sottocategorie di attivita' economiche individu… |
| 015 | 7.12 | concessionaire; ATECO / activity-code list | aliquota maggiorata per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono attivita' economiche rientranti nei s… |

### Emilia-Romagna (`08`; Finanze ID_IRAP 2320; DATA_PUBBLICAZIONE 02-APR-26; 18 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria (per i soggetti che non rientrano in nessuno dei casi sottoelencati) |
| 002 | 4.20 | ATECO / activity-code list | Aliquota prevista per alcune attivita' economiche identificate dai soli codici ATECO 2007 (agg 2022) riportati nell' allegato indicato all'art.1 comma 1 della L.R. 31/3/2… |
| 003 | 6.20 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota prevista per alcune attivita' economiche identificate dai soli codici ATECO 2007 (agg 2022) riportati nell'allegato indicato all'art. 1 comma 1 della L.R. n. 1/2… |
| 004 | 4.82 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti passivi esercenti le attivita' economiche rientranti nei settori contraddistinti dai codici 23, 40, 64 e 67 della classificazione ATECOFIN 2004 e … |
| 005 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti passivi esercenti le attivita' economiche rientranti nei settori contraddistinti dai codici 23, 40, 64 e 67 della classificazione ATECOFIN 2004 ri… |
| 006 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti di cui all'art. 5 del decreto legislativo n. 446/97 che svolgono attivita' di imprese concessionarie diverse da quelle di costruzione e gestione d… |
| 007 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti, di cui all'art. 5 del d.lgs. 446/97, che svolgono attivita' di imprese concessionarie, diverse da quelle di costruzione e gestione di autostrade … |
| 008 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti di cui all'art. 5 del D.Lgs. n. 446/97 che svolgono attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade … |
| 009 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti di cui all'art. 5 del D.Lgs. n. 446/97 che svolgono attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade … |
| 010 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 011 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 012 | 4.65 | bank/financial company (art. 6 c. 1, 7); ATECO / activity-code list | Aliquota per attivita' economiche di cui all'art.6 del D.lgs. n.446/97 qualora l'attivita' sia svolta da banche ed altri enti e societa' finanziarie non rientranti nei se… |
| 013 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 014 | 8.82 | insurer (art. 7); ATECO / activity-code list | Aliquota per le attivita' economiche rientranti nei settori contraddistinti dai codici 66 e 67 della classificazione ATECOFIN 2004 qualora l'attivita' sia svolta da impre… |
| 015 | 3.21 | third-sector / cooperative / sports-club status | Aliquota per gli Enti del Terzo Settore di cui al d.lgs.117/2017 (Codice del Terzo Settore) iscritti nelle sezioni del Registro unico nazionale del Terzo settore (RUNTS) … |
| 016 | 3.21 | third-sector / cooperative / sports-club status | Aliquota per le cooperative sociali di cui alla Legge n. 381/91 |
| 017 | 2.98 | third-sector / cooperative / sports-club status | Aliquota per le organizzazioni non governative riconosciute ai sensi della Legge 26 febbraio 1987, n. 49 |
| 018 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici |

### Toscana (`09`; Finanze ID_IRAP 2318; DATA_PUBBLICAZIONE 13-APR-26; 25 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 2.98 | third-sector / cooperative / sports-club status | Aliquota agevolata per i soggetti di cui all'articolo 3, comma 1, lettere a), b), ed e), del D.lgs. 446/1997 con qualifica di impresa sociale ai sensi del decreto legisla… |
| 003 | 2.94 | site in a listed municipality/area | Aliquota agevolata per i soggetti di cui all'articolo 3, comma 1, lettere a), e b), del D.lgs. 446/1997, limitatamente al valore della produzione netta prodotta nei terri… |
| 004 | 2.98 | ASP/IPAB | Aliquota ridotta per le aziende pubbliche di servizi alla persona (ASP) |
| 005 | 2.98 | ATECO / activity-code list | Aliquota agevolata per i settori di attivita' economica 77.11 e 77.12 della classificazione ATECO 2007 |
| 006 | 2.98 | other (read the quote) | Aliquota agevolata per i soggetti di cui all'articolo 1bis della L.R. 79/2012 |
| 007 | 4.82 | ATECO / activity-code list | Aliquota maggiorata per le attivita' economiche individuate dai codici ISTAT (Ateco 2007): 52.21.2, 61, 68, 05, 06, 07,08, 09, 21, 35,36,37,38, 39, 45, 46, 47, 49.10, 49.… |
| 008 | 4.20 | gaming machines in the premises | Aliquota maggiorata per gli esercizi pubblici e commerciali e i circoli privati in cui vi sia offerta di apparecchi di cui all'art. 110, comma 6, del regio decreto n. 773… |
| 009 | 4.32 | ATECO / activity-code list; gaming machines in the premises | Aliquota agevolata per gli esercizi pubblici e commerciali e i circoli privati che rimuovono dai locali tutti gli apparecchi di cui all'art. 110, comma 6, del regio decre… |
| 010 | 7.57 | bank/financial company (art. 6 c. 1, 7); ATECO / activity-code list | Aliquota maggiorata per le attivita' individuate dal codice 64 e 66 (ATECO 2007) qualora l'attivita' sia svolta per le banche, le societa' finanziarie e gli altri enti di… |
| 011 | 6.65 | bank/financial company (art. 6 c. 1, 7); ATECO / activity-code list | Aliquota per banche ed altri enti e societa' finanziari di cui per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446… |
| 012 | 8.82 | insurer (art. 7); ATECO / activity-code list | Aliquota maggiorata per le attivita' individuate dal codice ISTAT 65 e dal codice 66 (ATECO 2007) qualora l'attivita' sia svolta da imprese di assicurazione di cui all'ar… |
| 013 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 014 | 3.24 | concessionaire | Aliquota agevolata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che fruiscono d… |
| 015 | 5.12 | concessionaire; ATECO / activity-code list | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono a… |
| 016 | 3.28 | concessionaire | Aliquota agevolata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che fruiscono d… |
| 017 | 3.40 | gaming machines in the premises | Aliquota agevolata per gli esercizi pubblici e commerciali e i circoli privati che rimuovono dai locali tutti gli apparecchi di cui all'art. 110, comma 6, del regio decre… |
| 018 | 8.50 | public body | Aliquota Amministrazioni ed Enti pubblici |
| 019 | 0.00 | other (read the quote) | Esenzione per gli esercenti l'attivita' commerciale di cui all'articolo 4 del D.lgs. n.114 del 31/3/98, n. 114 nei territori classificati montani |
| 020 | 2.98 | third-sector / cooperative / sports-club status | Enti iscritti al Registro unico nazionale del Terzo settore (RUNTS) nelle sezioni di cui all'articolo 46, comma 1, limitatamente alle attivita' non commerciali e alle att… |
| 021 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Maggiorazione codice ATECO 2007 :19.2 Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all… |
| 022 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 023 | 6.82 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Maggiorazione codice ATECO 2007 :19.2 Aliquota per le imprese concessionarie non esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui… |
| 024 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 025 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per le attivita' individuate dal codice 64 e 66 (ATECO 2007) qualora si tratti di societa' di intermediazione mobiliare e gli intermediari, di societa… |

### Umbria (`10`; Finanze ID_IRAP 2481; DATA_PUBBLICAZIONE 30-LUG-26; 22 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.30 | general | Aliquota ordinaria |
| 002 | 4.82 | bank/financial company (art. 6 c. 1, 7); insurer (art. 7); ATECO / activity-code list | Aliquota maggiorata per i settori di attivita' economiche contraddistinti dai codici ATECO 2007: 53.10.00. 53.20.00. 61.10.00. 61.20.00. 61.30.00. 61.90.10. 61.90.20. 61.… |
| 003 | 1.50 | third-sector / cooperative / sports-club status | Aliquota agevolata per le cooperative sociali di tipo A di cui alla Legge n. 381 del 1991, limitatamente alle attivita' istituzionali esercitate |
| 004 | 2.98 | third-sector / cooperative / sports-club status | Aliquota agevolata per le societa' cooperative di lavoro di cui al D.P.R. 30/04/1970 n. 602 limitatamente all'attivita' istituzionale esercitata |
| 005 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le cooperative sociali di tipo B di cui alla Legge n. 381 del 1991, limitatamente alle attivita' istituzionali esercitate |
| 006 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in v… |
| 007 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono a… |
| 008 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento… |
| 009 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 010 | 8.82 | insurer (art. 7); ATECO / activity-code list | Aliquota maggiorata per i settori di attivita' economiche contraddistinti dai codici ATECO 2007: 65.11.00. 65.12.00. 65.20.00. 65.30.10. 65.30.20. 65.30.30 e dal codici A… |
| 011 | 8.50 | public body | Aliquota amministrazioni ed enti pubblici |
| 012 | 4.25 | ASP/IPAB | Aliquota ridotta per le aziende pubbliche di servizi alla persona (ASP) di cui alla legge regionale 28 novembre 2014, n. 25, limitatamente alle attivita' istituzionali es… |
| 013 | 6.30 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici Ateco di cui all'art. 3, comma 1 del D.L. 20 febbraio 2026, n. 21 e c… |
| 014 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici Ateco di cui all'art. 3, comma 1 del D.L. 20 febbraio 2026… |
| 015 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono le attivita'… |
| 016 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono l… |
| 017 | 6.65 | bank/financial company (art. 6 c. 1, 7); ATECO / activity-code list | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 che non svolgono attivita' economiche di cui… |
| 018 | 7.57 | bank/financial company (art. 6 c. 1, 7); ATECO / activity-code list | Aliquota maggiorata per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 che svolgono attivita' economiche… |
| 019 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 020 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no); ATECO / activity-code list | Aliquota maggiorata per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, … |
| 021 | 1.50 | third-sector / cooperative / sports-club status; ATECO / activity-code list | Aliquota agevolata per enti del Terzo settore, a prescindere dalla sezione del RUNTS in cui sono iscritti, che esercitano in via esclusiva e principale una o piu' attivit… |
| 022 | 2.98 | third-sector / cooperative / sports-club status; ATECO / activity-code list | Aliquota agevolata per enti del Terzo settore, a prescindere dalla sezione del RUNTS in cui sono iscritti, che esercitano in via esclusiva e principale una o piu' attivit… |

### Marche (`11`; Finanze ID_IRAP 2307; DATA_PUBBLICAZIONE 20-MAR-26; 22 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.73 | general | Aliquota ordinaria. |
| 002 | 3.90 | SME status / turnover / exports / INAIL premium | Aliquota agevolata per i soggetti che realizzano almeno il 50 per cento del proprio fatturato annuo, dichiarato ai fini IVA, per lavorazioni in conto terzi nei settori de… |
| 003 | 3.90 | third-sector / cooperative / sports-club status | Aliquota agevolata per gli enti del terzo settore di cui al decreto legislativo 3 luglio 2017, n. 117 (Codice del terzo settore a norma dell'articolo 1, comma 2, lettera … |
| 004 | 3.90 | other (read the quote) | Aliquota agevolata per i soggetti di cui all'art. 3, comma 1, lettera e-bis),del D.Lgs. 446/1997, relativamente al valore prodotto nell'esercizio dell'attivita' commercia… |
| 005 | 3.90 | ATECO / activity-code list | Aliquota agevolata per il settore ricerca e sviluppo di cui ai codici ISTAT, attivita' economiche per le classi: 73.10 e 73.20 (confluiscono nei codici Ateco 2007: 72.11.… |
| 006 | 3.90 | ATECO / activity-code list | Aliquota agevolata per il settore 'altre attivita' dello spettacolo, di intrattenimento e divertimento' di cui al codice ISTAT, della classificazione delle attivita' econ… |
| 007 | 2.50 | third-sector / cooperative / sports-club status | Aliquota agevolata per le cooperative sociali di cui all'art.1, comma 1, lett. A), legge 381/1991. |
| 008 | 1.50 | third-sector / cooperative / sports-club status | Aliquota agevolata per le cooperative sociali di cui all'art.1, comma 1, lett. B), legge 381/1991. |
| 009 | 4.13 | ATECO / activity-code list | Aliquota agevolata per il settore fabbricazione delle calzature di cui ai codici ISTAT attivita' economiche: 19.30.1, 19.30.2, 19.30.3 e per il settore fabbricazione arti… |
| 010 | 4.13 | ATECO / activity-code list; SME status / turnover / exports / INAIL premium | Aliquota agevolata per le imprese, con sede legale nel territorio regionale, operanti nei settori dell'industria, dell'artigianato e del commercio di cui ai codici alfabe… |
| 011 | 4.13 | ATECO / activity-code list; SME status / turnover / exports / INAIL premium | Aliquota agevolata per le imprese, con sede legale nel territorio regionale, operanti nei settori dell'industria, dell'artigianato e del commercio di cui ai codici alfabe… |
| 012 | 4.13 | ATECO / activity-code list | Aliquota agevolata per le attivita' di preparazione e concia del cuoio di cui al codice Istat attivita' economiche: 19.10.0 (confluisce nel codice Ateco 2007: 15.11.00 - … |
| 013 | 3.90 | ASP/IPAB | Aliquota agevolata per le aziende pubbliche di servizi alla persona (ASP) di cui alla legge regionale 26 febbraio 2008, n. 5, relativamente alle attivita' istituzionali e… |
| 014 | 5.03 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per i soggetti di cui all'articolo 5 del d.lgs. 446/1997 che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestion… |
| 015 | 6.13 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 016 | 7.48 | bank/financial company (art. 6 c. 1, 7) | Aliquota maggiorata per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997. |
| 017 | 8.73 | insurer (art. 7) | Aliquota maggiorata per le imprese di assicurazione di cui all'articolo 7 del d.lgs. 446/1997. |
| 018 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici. |
| 019 | 5.48 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento… |
| 020 | 6.73 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 202… |
| 021 | 7.03 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per i soggetti di cui all'articolo 5 del d.lgs. 446/1997 che svolgono attivita' di imprese concessionarie diverse da quelle di costruzione e gestione … |
| 022 | 7.48 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota maggiorata per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, … |

### Lazio (`12`; Finanze ID_IRAP 2361; DATA_PUBBLICAZIONE 14-APR-26; 13 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.82 | general | Aliquota ordinaria |
| 002 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 003 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 005 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 006 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 007 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 008 | 8.82 | insurer (art. 7) | Aliquota per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446 del 1997 |
| 009 | 8.50 | public body | Aliquota per le amministrazioni e gli enti pubblici |
| 010 | 3.90 | site in a listed municipality/area | Aliquota per i soggetti passivi IRAP, di cui all'articolo 3, comma 1, lettere a), b), c) ed e) del d.lgs. 446/1997, operanti esclusivamente nei comuni montani indicati ne… |
| 011 | 3.90 | third-sector / cooperative / sports-club status | Aliquota per gli enti del Terzo settore, iscritti nel Registro unico nazionale del Terzo settore (RUNTS), previsto all'articolo 45 del decreto legislativo 3 luglio 2017, … |
| 012 | 3.90 | new business / transfer into the region | Aliquota per i soggetti passivi IRAP, di cui all'articolo 3, comma 1, lettere a), b), c) ed e) del d.lgs. 446/1997, gia' attivi alla data del 01/01/2026 (data di entrata … |
| 013 | 4.36 | third-sector / cooperative / sports-club status | Aliquota per per le cooperative sociali iscritte nell'albo regionale di cui all'articolo 4, comma 1, lettera b) della legge regionale 28 novembre 2024, n. 18 (Nuove dispo… |

### Abruzzo (`13`; Finanze ID_IRAP 2309; DATA_PUBBLICAZIONE 26-MAR-26; 14 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.82 | general | Aliquota Ordinaria |
| 002 | 8.82 | insurer (art. 7) | Aliquota per imprese di assicurazioni di cui all'art. 7 del D. Lgs. 446/97 |
| 003 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per banche, societa' finanziarie e altri enti di cui ai commi 1 e 7 all'art. 6 del D.Lgs. n. 446/1997 |
| 004 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economiche … |
| 005 | 8.50 | public body | Aliquota per Amministrazioni ed Enti Pubblici |
| 006 | 3.90 | ATECO / activity-code list | Aliquota agevolata per le Farmacie Rurali sussidiate |
| 007 | 3.90 | site in a listed municipality/area | Aliquota agevolata per i soggetti di cui all'art. 3 c. 1 lettere a) e b) del D. Lgs. 446/1997 operanti in Comuni Montani, nei limiti e alle condizioni previste dalla L.R.… |
| 008 | 3.90 | third-sector / cooperative / sports-club status | Aliquota agevolata per Cooperative Sociali |
| 009 | 3.90 | new business / transfer into the region | Aliquota agevolata per Soggetti passivi che iniziano l'attivita' a decorrere dal 1/1/2004, per i primi due periodi d'imposta, nei limiti ed alle condizioni previste dalla… |
| 010 | 0.00 | third-sector / cooperative / sports-club status | Aliquota per le Organizzazioni di volontariato e le Associazioni di promozione sociale iscritte nei RUNTS e le ex ONLUS che hanno perso tale qualifica a seguito di iscriz… |
| 011 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 012 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per societa' di intermediazione mobiliare e gli intermediari, societa' di gestione dei fondi comuni di investimento, societa' di investimento a capitale variabil… |
| 013 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.LGS. n. 446 del 1997 esercenti, in via prevalente, le attivita' e… |
| 014 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 20… |

### Molise (`14`; Finanze ID_IRAP 2441; DATA_PUBBLICAZIONE 20-LUG-26; 22 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.97 | general | Aliquota ordinaria comprensiva della maggiorazione dello 0.92% |
| 002 | 4.05 | site in a listed municipality/area | Aliquota agevolata per i soggetti di cui all'art. 3 del D.Lgs. n. 446/97 limitatamente al valore della produzione netta riferibile al territorio dei comuni della provinci… |
| 003 | 4.05 | founders young/women | Aliquota agevolata per le imprese giovanili di cui all'art. 5, co.1 del decreto legislativo n. 185 del 21 aprile 2000, e per le imprese individuali il cui titolare ha eta… |
| 004 | 4.05 | founders young/women | Aliquota agevolata per le imprese femminili in possesso dei requisiti di cui all'art. 2, lettera a) della legge 25 febbraio 1992, n. 215. |
| 005 | 4.05 | third-sector / cooperative / sports-club status | Aliquota agevolata per le societa' sportive dilettantistiche operanti senza fine di lucro. |
| 006 | 4.05 | site in a listed municipality/area | Aliquota agevolata per le imprese, sotto qualsiasi forma costituite, limitatamente al valore della produzione netta riferibile al territorio dei Comuni montani con riliev… |
| 007 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le ex ONLUS di cui all'art. 10 del D.Lgs. n. 460/97, che hanno perfezionato l'iscrizione al RUNTS entro il 31 marzo 2026. |
| 008 | 6.35 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 009 | 4.35 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 010 | 5.72 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 011 | 4.80 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 012 | 8.97 | insurer (art. 7) | Aliquota per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97. |
| 013 | 8.20 | insurer (art. 7) | Aliquota agevolata per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 che fruiscono di una delle agevolazioni di cui ai codici 2, 3, 4, 6. |
| 014 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici. |
| 015 | 7.72 | bank/financial company (art. 6 c. 1, 7) | Aliquota per banche ed altri enti e societa' finanziare di cui all'art. 6 del D.Lgs. n. 446/97. commi 1 e 7. |
| 016 | 5.27 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 017 | 7.27 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 018 | 6.05 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 019 | 6.97 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 . |
| 020 | 6.80 | bank/financial company (art. 6 c. 1, 7) | Aliquota agevolata per banche ed altri enti e societa' finanziare di cui all'art. 6 del D.Lgs. n. 446/97. commi 1 e 7, che usufruiscono delle agevolazioni di cui ai codic… |
| 021 | 7.72 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 022 | 6.80 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, l… |

### Campania (`15`; Finanze ID_IRAP 2325; DATA_PUBBLICAZIONE 01-APR-26; 10 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.97 | general | Aliquota ordinaria (tranne le eccezioni previste dal D.Lgs. 446/97 e dalla Legge regionale) |
| 002 | 6.97 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 003 | 5.27 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 7.27 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 005 | 7.72 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 006 | 5.72 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 007 | 7.72 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 008 | 8.97 | insurer (art. 7) | Aliquota per le imprese di assicurazione di cui all'art. 7 del D.Lgs. n. 446/97 |
| 009 | 4.40 | third-sector / cooperative / sports-club status | Aliquota per le cooperative sociali di cui alla Legge 8 novembre 1991, n. 381 |
| 010 | 8.50 | public body | Aliquota Amministrazioni ed enti pubblici |

### Puglia (`16`; Finanze ID_IRAP 2461; DATA_PUBBLICAZIONE 23-LUG-26; 12 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.82 | general | Aliquota ordinaria |
| 002 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 003 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 005 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 006 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 007 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 008 | 8.82 | insurer (art. 7) | Aliquota per le imprese di assicurazione |
| 009 | 8.50 | public body | Aliquota per le amministrazioni e gli enti pubblici |
| 010 | 0.00 | ASP/IPAB | Esenzione per le Aziende Pubbliche di servizi alla persona (ASP) succedute alle Istituzioni Pubbliche di Assistenza e Beneficienza (IPAB) |
| 011 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per gli enti iscritti nel Registro unico nazionale del Terzo settore (Runts), istituito con decreto legislativo 3 luglio 2017, n. 117, nelle sezioni di cui all'… |
| 012 | 0.00 | third-sector / cooperative / sports-club status | Esenzione, alle medesime condizioni previste dall'art. 48, comma 1-bis, della legge regionale n. 7 del 21 maggio 2002 e limitatamente alle attivita' non commerciali, agli… |

### Basilicata (`17`; Finanze ID_IRAP 2317; DATA_PUBBLICAZIONE 13-APR-26; 14 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria. |
| 002 | 7.12 | concessionaire; ATECO / activity-code list | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono a… |
| 003 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997. |
| 004 | 7.90 | insurer (art. 7) | Aliquota per le imprese di assicurazione di cui all'art. 7 del D.lgs. n. 446/97. |
| 005 | 8.50 | public body | Aliquota per le Amministrazioni ed enti pubblici. |
| 006 | 2.98 | third-sector / cooperative / sports-club status | Aliquota agevolata per le ex ONLUS e per gli Enti del Terzo Settore comprese le cooperative sociali ed escluse le imprese sociali costituite in forma di societa' cosi' co… |
| 007 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 008 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 009 | 6.20 | concessionaire; ATECO / activity-code list | Aliquota maggiorata per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori, per i soggetti che esercitano attivita' di produzio… |
| 010 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 011 | 5.12 | concessionaire; ATECO / activity-code list | Aliquota maggiorata per i soggetti che esercitano attivita' di imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e che svolgono a… |
| 012 | 4.82 | ATECO / activity-code list | Aliquota maggiorata per i seguenti settori di attivita': a) produzione, trasmissione e distribuzione di energia elettrica e servizi connessi di cui ai codici ATECO 2007: … |
| 013 | 6.82 | ATECO / activity-code list | Aliquota maggiorata per i seguenti settori di attivita': a) produzione, trasmissione e distribuzione di energia elettrica e servizi connessi di cui ai codici ATECO 2025: … |
| 014 | 5.90 | ATECO / activity-code list | Aliquota maggiorata per i seguenti settori di attivita': a) produzione, trasmissione e distribuzione di energia elettrica e servizi connessi di cui ai codici ATECO 2025:3… |

### Calabria (`18`; Finanze ID_IRAP 2306; DATA_PUBBLICAZIONE 20-MAR-26; 9 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 4.82 | general | Aliquota ordinaria |
| 002 | 6.82 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 003 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 005 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 006 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 007 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 008 | 8.82 | insurer (art. 7) | Aliquota per le imprese di assicurazione |
| 009 | 8.50 | public body | Aliquota per le amministrazioni e gli enti pubblici |

### Sicilia (`19`; Finanze ID_IRAP 2305; DATA_PUBBLICAZIONE 30-MAR-26; 25 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 3.90 | general | Aliquota ordinaria |
| 002 | 5.90 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 003 | 4.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 6.20 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 005 | 6.65 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 |
| 006 | 4.65 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 007 | 6.65 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivita… |
| 008 | 7.90 | insurer (art. 7) | Aliquota per le imprese di assicurazione |
| 009 | 8.50 | public body | Aliquota per le amministrazioni e gli enti pubblici |
| 010 | 2.98 | other (read the quote) | Aliquota agevolata per le aziende i cui titolari abbiano sporto denuncia circostanziata nei confronti di atti estorsivi compiuti ai loro danni, come definiti dalla vigent… |
| 011 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le cooperative sociali di cui alla legge n. 381/91(reg.UE 2023/2831 e reg.UE 2019/316). |
| 012 | 0.00 | new business / transfer into the region; SME status / turnover / exports / INAIL premium | Esenzione per le imprese turistiche, alberghiere e artigianali, le imprese operanti nel settore dei beni, culturali e dell'information- technology, le industrie agro-alim… |
| 013 | 0.00 | ASP/IPAB | Esenzione per le istituzioni pubbliche di assistenza e beneficenza (IPAB). (reg.UE 2023/2831 e reg.UE 2019/316). |
| 014 | 3.65 | third-sector / cooperative / sports-club status; SME status / turnover / exports / INAIL premium | Aliquota agevolata per le piccole e medie imprese operanti in Sicilia nei settori dell'artigianato, del commercio, del turismo e dei servizi, nonche' per le cooperative, … |
| 015 | 0.00 | third-sector / cooperative / sports-club status; new business / transfer into the region | Esenzione IRAP agli enti iscritti al RUNTS, comprese le cooperative sociali ed escluse le imprese sociali costituite in forma di societa', anche nelle seguenti ipotesi: a… |
| 016 | 0.00 | third-sector / cooperative / sports-club status | Esenzione IRAP enti terzo settore di cui al D. Lgs 3 luglio 2017, n. 117 (Reg. UE 2023/2831 e Reg. UE 2019/316) |
| 017 | 4.98 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026… |
| 018 | 3.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita… |
| 019 | 5.28 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 2… |
| 020 | 5.73 | bank/financial company (art. 6 c. 1, 7) | Aliquota agevolata per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D.Lgs. n. 446 del 1997 e che fruiscono dell'agevolazione … |
| 021 | 3.73 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento … |
| 022 | 5.73 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota agevolata per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D.Lgs. n. 446 del 1997 esercenti, in via prevalente, l… |
| 023 | 6.98 | insurer (art. 7) | Aliquota agevolata per le imprese di assicurazione che fruiscono dell'agevolazione di cui al codice 10. Tre anni dalla denunzia - (reg.UE 2023/2831 e reg.UE 2019/316). |
| 024 | 6.82 | ATECO / activity-code list | Aliquota per le imprese di raffinazione di prodotti petroliferi in Sicilia |
| 025 | 5.90 | ATECO / activity-code list | Aliquota agevolata per le imprese di raffinazione di prodotti petroliferi in Sicilia e che fruiscono dell'agevolazione di cui al codice 10. Tre anni dalla denunzia - (reg… |

### Sardegna (`20`; Finanze ID_IRAP 2381; DATA_PUBBLICAZIONE 13-MAG-26; 12 rate rows)

| Code | Rate % | Facts that select the row (mechanical tags; the quote governs) | Dataset text, Italian, first 170 characters |
|---|---|---|---|
| 001 | 2.93 | general | Aliquota ordinaria |
| 002 | 8.50 | public body | Aliquota amministrazioni ed enti pubblici |
| 003 | 5.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie diverse da quelle di costruzione e gestione di autostrade e trafori e da quelle esercenti, in via prevalente, le attivita' economic… |
| 004 | 7.57 | bank/financial company (art. 6 c. 1, 7) | Aliquota per le banche, le societa' finanziarie e gli altri enti di cui ai commi 1 e 7 dell'art. 6 del D. Lgs. n. 446 del 1997 |
| 005 | 8.82 | insurer (art. 7) | Aliquota per le imprese di assicurazione |
| 006 | 0.00 | new business / transfer into the region | Esenzione di cui all'art.16 comma 1 del D.lgs n.446/97 per le nuove iniziative produttive intraprese nel territorio regionale |
| 007 | 0.00 | third-sector / cooperative / sports-club status | Esenzione per le imprese iscritte nel registro ONLUS tenuto sino al 31.12.2025 e ora iscritte nel Registro unico nazionale del terzo settore di cui al D. Lgs. n. 117 del … |
| 008 | 0.00 | ASP/IPAB | Esenzione per le aziende pubbliche di servizi alla persona succedute alle IPAB |
| 009 | 4.93 | activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per i soggetti esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21 |
| 010 | 7.12 | concessionaire; activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le imprese concessionarie esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui all'art. 3, comma 1, del D.L. 20 febbraio… |
| 011 | 5.57 | SIM/asset manager (art. 6 c. 2-4); non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di intermediazione mobiliare e gli intermediari, le societa' di gestione dei fondi comuni di investimento, le societa' di investimento a capitale… |
| 012 | 7.57 | non-financial holding (art. 6 c. 9); activity in D.L. 21/2026 table 1 (yes/no) | Aliquota per le societa' di partecipazione non finanziaria e assimilati di cui al comma 9 dell'art. 6 del D. Lgs. n. 446 del 1997 esercenti, in via prevalente, le attivit… |

---

## Summary table

| # | Question | Verdict |
|---|---|---|
| 1 | Special-subject rates and regional reductions that depend on the company, per region (Finanze 2026 `irp2026.csv`) | taxpayer fact |
| 1b | Veneto 4.08 is the general rate an ordinary srl pays (code 001, «Maggiorazione di aliquota generalizzata») | settled |
| 1c | Trento code 010 (4.82) for a company with no employees | accountant |
| 2 | Periods other than twelve months: how the base and the c. 4-bis and c. 4-bis.1 deductions are ragguagliate | settled |
| 3 | Seasonal 70% deduction (c. 4-octies): what the records must hold | taxpayer fact |
| 3b | Seasonal 70% together with the 1,850 of c. 4-bis.1 on the same worker | accountant |
| 4 | Headcount exclusions in c. 4-bis.1 (apprentices, disabled workers, formazione lavoro): exact words | settled |
| 5 | Single-payment threshold for IRAP advances | settled (103 euro, on the first instalment) |
| 6 | IRAP return date for a natural person | settled (no IRAP due: art. 1 c. 8 L. 234/2021) |
| 7 | IRES and IRAP advances and instalments for a company | settled |
| 8 | Cash or accrual basis for an srl (art. 109 TUIR) | settled (accrual) |
