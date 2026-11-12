# REB-1086: flat-rate, chamber fee and F24 questions of REB-975, read against primary sources

Read date of every source below: **2026-11-12**. Normattiva texts were read with `!vig=2026-11-12` (the text in force that day). Repo read only; nothing edited.
Code paths are under `projects/pigrocrm/packages/core/src/pigrocrm/core/fiscal/`. The A5 register is `projects/pigrocrm/docs/superpowers/specs/2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (rows are named by their first words).

Quotes are Italian, exact. URLs are the ones fetched.

---

## 1. Can the flat-rate substitute tax be paid in instalments (saldo and first advance; codes 1790, 1791, 1792, 1793)?

**Question.** Does the rateazione of art. 20 D.Lgs. 241/1997 apply to the substitute tax of the flat-rate regime, and which of 1790/1791/1792/1793 can carry an instalment?

**Verdict: settled.**

**Answer.** Yes for the balance (1792) and the first advance (1790); no for the second or only advance (1791). The instalment is written in the `rateazione` column as `NNRR`. 1793 is not a flat-rate code: it is the first advance of the closed «regime fiscale di vantaggio» (D.L. 98/2011), so the flat-rate pack must never use it. The 0.40% deferral, the 4% / 0.33% instalment interest and the 16 December limit are the ones of personal income tax.

**Sources.**

1. Agenzia delle Entrate, Risoluzione n. 59/E of 11 June 2015, https://www.agenziaentrate.gov.it/portale/documents/20143/303445/Risoluzione+n.+59+del+11+giugno+2015_RISOLUZIONE+59_E+DEL+11+GIUGNO+2015.pdf/cc2855cf-a72f-243a-4f90-389d75b598e4?t=1434034788629 (read 2026-11-12):
   - «Per i codici tributo "1790" e "1792", in caso di versamento rateale, nel campo "rateazione/regione/prov./mese rif." è riportato il numero della rata nel formato "NNRR" dove "NN" rappresenta il numero della rata in pagamento e "RR" indica il numero complessivo delle rate. Si precisa che in caso di pagamento in un'unica soluzione il suddetto campo è valorizzato con "0101".»
   - «Il codice tributo "1792" è utilizzabile anche in corrispondenza degli "importi a credito compensati".»
   - 1791 is «Acconto seconda rata o in unica soluzione» and is not in the sentence about instalments.
2. Agenzia, «Ricerca codici tributo», the page of each code (read 2026-11-12):
   - https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1790 : «L'importo a debito puo' essere versato in forma rateale»
   - https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1791 : «L'importo a debito deve essere versato in unica soluzione»
   - https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1792 : «L'importo a debito puo' essere versato in forma rateale» and «Il codice e' utilizzabile anche per indicare un importo a credito da compensare»
   - https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1793 : «IMPOSTA SOSTITUTIVA PER REGIME FISCALE DI VANTAGGIO PER L'IMPRENDITORIA GIOVANILE E LAVORATORI IN MOBILIT - ACCONTO PRIMA RATA - ART. 27, DL 06/06/2011, N. 98 [...]»; with 1794 (second advance) and 1795 (balance). Risoluzione n. 52/E of 25 May 2012 (https://www.agenziaentrate.gov.it/portale/documents/20143/304596/Risoluzione+52+del+25+05+2012_ris52e+del+25.05.12.pdf/5644d4c3-0e7a-3f52-821f-d53410b931eb?t=1337939949153) institutes them for «l'imposta sostitutiva sul regime fiscale di vantaggio per l'imprenditoria giovanile e lavoratori in mobilità».
3. L. 23 dicembre 2014 n. 190, art. 1 c. 64, last sentence, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:2014-12-23;190~art1!vig=2026-11-12): «Si applicano le disposizioni in materia di versamento dell'imposta sui redditi delle persone fisiche.»
4. D.Lgs. 9 luglio 1997 n. 241, art. 20 c. 1, Normattiva text in force on 2026-11-12 (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1997-07-09;241~art20!vig=2026-11-12): «Le somme dovute a titolo di saldo e di acconto delle imposte e dei contributi [...] possono essere versate [...] in rate mensili di uguale importo, con la maggiorazione degli interessi di cui al comma 2, decorrenti dal mese di scadenza, in ogni caso, il pagamento deve essere completato entro il 16 dicembre dello stesso anno di presentazione della dichiarazione o della denuncia.» (c. 4: «I versamenti rateali sono effettuati entro il giorno 16 di ciascun mese.») The article is repealed from 2027-01-01 by D.Lgs. 33/2025 art. 10 (the same words), as the pack already records.
5. Agenzia, Redditi PF 2026, Fascicolo 1, Parte I «Rateazione» (https://infoprecompilata.agenziaentrate.gov.it/portale/documents/d/guest/pf1_istruzioni_2026.pdf, p. 10-11): «Tutti i contribuenti possono avvalersi della facoltà di rateizzare i versamenti delle somme dovute a titolo di saldo e di primo acconto»; «Sugli importi rateizzati sono dovuti gli interessi nella misura del 4 per cento annuo, [...] Sugli importi da versare con le rate mensili successive, si applicano gli interessi dello 0,33 per cento in misura forfetaria»; «gli interessi da rateazione non vanno cumulati all'imposta, ma versati separatamente mediante l'apposito codice tributo»; the same page lists «1668: Interessi pagamento dilazionato. Importi rateizzabili Sez. Erario» beside the codes «1792 [...] Saldo», «1790 [...] Acconto prima rata», «1791 [...] Acconto seconda rata o unica soluzione».
6. Same booklet, rigo RN38, col. 4, confirms the 1793/1794 vs 1790/1791 split by regime: «(di cui fuoriusciti dal regime di vantaggio o dal regime forfetario) [...] (rispettivamente codici tributo "1793" e "1794", o codici tributo "1790" e "1791", anno di riferimento "2025")».

**Code implication.**
- A5 row «Instalment plan (rateazione) of the flat-rate regime's June balance and first advance»: `to verify` becomes `verified 2026-11-12`, with sources 1 to 5 above.
- `payment_data.py`: no value changes. `IT_FLAT_RATE_PAYMENT_2026` already uses `INSTALMENTS` (`rateable=("balance", "first_advance")`), the codes 1792/1790/1791 and the interest code 1668 (`_INTEREST_ERARIO_CODE`). Add the quotes of sources 1 and 2 to the `source` of `_SUBSTITUTE_TAX_CODES` (today it says only «risoluzione 59/E ... Principali codici tributo»).
- `law_flat_rate.py`, docstring of `FlatRateAdvanceLaw`: «The instalment plan of the June balance and first advance is not here: the sources read do not state it for the substitute tax (A5, to verify, REB-975)» is wrong now. Replace with the sources above and point to `payment_data.INSTALMENTS`.
- Do not add 1793-1795 to the flat-rate pack (they belong to a regime closed to new entrants; a person leaving it is only an input of RN38 col. 4).
- A test worth adding: `second_advance` is not rateable for the flat-rate pack (1791 «in unica soluzione»).

---

## 2. Do the 4% INPS surcharge (rivalsa) lines belong to the flat-rate revenue?

**Question.** Is the rivalsa INPS 4% a component of the «compensi» the coefficient is applied to? (REB-975 cites Circolare 10/E 2016 and the risposte.)

**Verdict: settled** for what the product can issue (the gestione separata professional's 4%): it is part of the revenue. Strength: Agenzia's own guidance for the quadro LM plus INPS's message and the Agenzia's ruling on a recharge to the client; no statute and no circular states it for the flat-rate regime in so many words.

**Answer.** The 4% of art. 1 c. 212 L. 662/1996 is charged «in via definitiva» on top of the fee, is part of the fee for withholding and VAT, and the Agenzia's quadro LM guidance counts it among the positive components of income for a gestione separata professional: it is collected revenue, so it is in the base the coefficient applies to, and in the 85,000 euro limit. What is excluded is a different thing: the «contributo integrativo» of a professional fund (Cassa), which «non fa parte del compenso». A person with business income (artigiano, commerciante) has no 4% rivalsa at all: c. 212 is addressed to «redditi di lavoro autonomo di cui all'articolo 49, comma 1» of the TUIR.

The two circulars named in REB-975 do not mention it. I read the full text of Circolare 10/E of 4 April 2016 (https://www.agenziaentrate.gov.it/portale/documents/20143/298247/Circolare+10E+del+4+aprile+2016_Circolare+n.+10_E+del+4+aprile+2016.pdf/028b9b7e-66f0-0edf-128c-0613fd0563fd) and of Circolare 9/E of 10 April 2019 (https://www.agenziaentrate.gov.it/portale/documents/20143/297183/Circolare%2Bn%2B9%2Bdel%2B10%2Baprile%2B2019_Circolare%2B9%2Bdel%2B10%2Baprile%2B2019.pdf/4d0ea018-7b88-fd53-b03e-56b370db6f91): neither has a word on the INPS surcharge, on a «contributo integrativo» or on «4%» of a fee («rivalsa» there is the VAT rivalsa and the rivalsa on family-business collaborators' contributions). So the citation in `pack.py` («la circolare dell'Agenzia delle Entrate n. 10/E del 4 aprile 2016, pur non costituendo reddito imponibile», copied from `mastro/src/lib/server/fiscal/packs/it-flat-rate.ts:262` and `:474`) cannot be checked against the circular and says the opposite of what the Agenzia's guidance says.

**Sources.**

1. L. 23 dicembre 1996 n. 662, art. 1 c. 212, Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1996-12-23;662~art1!vig=2026-11-12): «i soggetti titolari di redditi di lavoro autonomo di cui all'articolo 49, comma 1, del testo unico delle imposte sui redditi [...] hanno titolo ad addebitare ai committenti, con effetto dal 26 settembre 1996, in via definitiva, una percentuale nella misura del 4 per cento dei compensi lordi.»
2. INPS, Messaggio n. 7751 of 7 May 2012, «Gestione separata - Titolo di rivalsa - Precisazioni» (https://www.inps.it/content/dam/inps-site/it/scorporati/circolari-e-messaggi/2012/05/Circolare_10983/Allegati/7928_Messaggio-numero-7751-del-07-05-2012.pdf): «La rivalsa è calcolata sui compensi lordi»; «Ai fini fiscali si ricorda che la maggiorazione addebitata in fattura (rivalsa) ed acquisita a titolo definitivo deve essere assoggettata al prelievo alla fonte di cui all'art. 25 del DPR 600/73 e concorre a formare la base imponibile dell'imposta sul valore aggiunto ai sensi dell'art. 13 del DPR 26 ottobre 1972 n. 633 così come precisato nella Risoluzione del 11/07/1996 n. 109 del Ministero delle Finanze».
3. Agenzia, «Dichiarazione precompilata», FAQ «Nel precompilare i dati relativi ai ricavi e/o ai compensi percepiti come vengono considerati gli importi esposti in fattura a titolo di contributi alla Cassa professionale?» (https://infoprecompilata.agenziaentrate.gov.it/portale/w/faq-nel-precompilare-i-dati-relativi-ai-ricavi-e-o-ai-compensi-percepiti-come-vengono-considerati-gli-importi-esposti-in-fattura-a-titolo-di-contribut) and the page «Quadro LM» (https://infoprecompilata.agenziaentrate.gov.it/portale/quadro-lm): «Se sei un professionista per il quale è previsto l'obbligo di iscrizione alla Cassa professionale di previdenza, i contributi addebitati al committente in fattura sono esclusi dall'importo dei componenti positivi di reddito precompilati. Diversamente, se sei un professionista che esercita un'attività per la quale non è prevista un'apposita Cassa professionale di previdenza e, pertanto, sei tenuto all'iscrizione alla Gestione Separata dell'INPS, i contributi addebitati al committente in fattura sono inclusi nell'importo dei componenti positivi di reddito precompilati.»
4. Agenzia, Certificazione Unica 2025, istruzioni, lavoro autonomo punto 4 (https://www.agenziaentrate.gov.it/portale/documents/20143/8486798/CU_istr_2025.pdf/1bbe2806-cb64-53e3-7720-622235b9293b?t=1737021527750&version=1.0): «Nel punto 4 va indicato l'ammontare lordo del compenso corrisposto al netto dell'IVA eventualmente dovuta. Si precisa che il contributo integrativo (ad esempio 2 per cento o 4 per cento) destinato alle Casse professionali non fa parte del compenso e, quindi, non deve essere indicato.»
5. Agenzia, Risposta n. 428 of 12 August 2022 (https://www.agenziaentrate.gov.it/portale/documents/20143/4629422/Risposta+all'interpello+n.+428+del+12+agosto+2022.pdf/e15564db-6ef3-132f-f6e0-a425e55cb576), by analogy for a flat-rate taxpayer's recharge to the client: «il riaddebito al cliente dell'imposta di bollo, essendo il professionista il soggetto passivo, fa parte integrante del suo compenso, con la conseguenza che risulta assimilato ai ricavi di cui al citato comma 64 e concorre al calcolo volto alla determinazione forfetaria del reddito».
6. L. 190/2014 art. 1 c. 64 (URL in question 1): the base is «l'ammontare dei ricavi o dei compensi percepiti».
7. TUIR art. 54 c. 2 lett. a), Normattiva (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.del.presidente.della.repubblica:1986-12-22;917~art54!vig=2026-11-12), the text after D.Lgs. 192/2024: «Non concorrono a formare il reddito le somme percepite a titolo di: a) contributi previdenziali e assistenziali stabiliti dalla legge a carico del soggetto che li corrisponde». The Cassa's integrativo is the case it covers; the 4% rivalsa of source 1 is a surcharge the professional «ha titolo» to add, not a contribution the law puts on the client.

**Code implication.**
- A5 row «Flat-rate basis: whether the INPS surcharge lines (the 4% «rivalsa») are in or out of the collected revenue»: `to verify` becomes `verified 2026-11-12`: in. Cite sources 1-3 and say that Circolare 10/E 2016 and 9/E 2019 are silent.
- `pack.py` line 285: `SOCIAL_SECURITY_SURCHARGE_INPS.count_by_the_coefficient=False` becomes `True`. The charge then counts in the ceiling (already `count_by_the_limit=True`) and in the coefficient base, so the two booleans no longer differ for any charge and the field can go (clean cutover): remove `StatutoryCharge.count_by_the_coefficient` (`pack.py` ~l. 146 and its docstring l. 132-141), `basis.py::_untaxed_markers` (l. 405-409) and `_surcharge_line` (l. 412-420) and their two call sites (l. 97-100 and 238-255), the `left_out` code `inps_surcharge` (`basis_schemas.py` l. 18, `apps/web/src/lib/api-types.ts` l. 8307, `AGENTS.md` l. 489 and 497), `ceiling.py::excluded_from_coefficient_base`, and the tests that pin the old behaviour (`test_fiscal_basis.py`, `test_fiscal_ceiling.py`, `test_fiscal_estimate.py`, `test_fiscal_pack.py`).
- Replace the comment above the charge («pur non costituendo reddito imponibile») with sources 1-3.
- Worked effect: a 100.00 fee with a 4.00 surcharge collected gives revenue 104.00 and income 104.00 x coefficient, not 100.00 x coefficient.
- Outside the repo: `mastro/src/lib/server/fiscal/packs/it-flat-rate.ts:262` and `:474` carry the same unverifiable citation.
- Not checked in code: that the surcharge line is offered only to a gestione separata professional (c. 212 gives it to «lavoro autonomo» only). A Cassa-covered professional's integrativo is a different line, excluded from revenue by source 4.

---

## 3. Cash or accrual for a person with business income (impresa) in the flat-rate regime

**Question.** art. 1 c. 64 L. 190/2014 «criterio di cassa»: does it apply to a flat-rate person whose income is business income, or only to a professional's?

**Verdict: settled.** Cash, for both.

**Answer.** The flat-rate revenue or fee counts when received, whether the activity is a profession or a business. The words «criterio di cassa» are not in c. 64 (it says «ricavi o [...] compensi percepiti»); they are in the Agenzia's circular and instructions.

**Sources.**

1. L. 190/2014 art. 1 c. 64 (URL in question 1): «determinano il reddito imponibile applicando all'ammontare dei ricavi o dei compensi percepiti il coefficiente di redditività».
2. Agenzia, Circolare n. 10/E of 4 April 2016 (URL in question 2), § 4.3: «Di seguito i coefficienti di redditività, distinti per codice ATECO, da applicare ai ricavi o ai compensi percepiti nel periodo di imposta, calcolati in base al criterio di cassa.»; § 4.3.5: «Il regime forfetario, infatti, determina il reddito, al pari di quello professionale, secondo il criterio di cassa, a prescindere dal tipo di attività esercitata (professionale o di impresa).»
3. Agenzia, Redditi PF 2026, Fascicolo 3, quadro LM, rigo LM22 (https://www.agenziaentrate.gov.it/portale/documents/d/guest/pf3_istruzioni_2026_agg-13-05-2026, updated 13-05-2026): «Nel regime in esame, i ricavi ed i compensi vengono imputati al periodo d'imposta sulla base del cd. "principio di cassa", e cioè in considerazione del momento di effettiva percezione del ricavo o compenso: tale criterio, pertanto, si applica tanto in caso di reddito derivante dall'esercizio di arti o professione, quanto in caso di reddito d'impresa.»
4. Same instructions, on the way in from another regime (art. 1 c. 72, Circolare 10/E § 4.3.6): «i ricavi incassati nel periodo di adozione del regime forfetario non concorrono alla determinazione del reddito forfetario qualora gli stessi abbiano già concorso a formare il reddito in base alle regole di competenza proprie del precedente regime».

**Code implication.**
- A5 row «Flat-rate basis: revenue and fees collected, whether the income is a profession's or a business's (cash principle)» is already `verified`; add source 2 § 4.3.5 (the explicit statement for the business case) next to c. 64 and the LM instructions. No code change: `law_income_basis.py` / `basis.py::year_basis` read collected revenue for the flat-rate regime whatever the profile.
- The 2026-11-09 comment, question 6, first half («is the basis cash or issued?»): answered, cash. The issued-invoices path of `GET /api/analytics/fiscal` must not feed a flat-rate estimate (A5 already says the estimate reads `year_basis`).
- Not modelled and not a question for the accountant: the year of entry from the ordinary or simplified regime (source 4: income collected in the first flat-rate year that already accrued before is not counted again). That needs the previous regime's accrual history, which the records do not hold.

---

## 4. Chamber fee: which chamber covers BT (Barletta-Andria-Trani) and SU (Sud Sardegna) for the 2026 increases?

**Verdict: settled.** BT: Camera di commercio di Bari (20%). SU: Camera di commercio di Cagliari-Oristano (20%). One fact changed on the way: «SU» no longer names Sud Sardegna.

**Answer.** The national fee portal of the chambers lists the Bari chamber with Bari and Barletta-Andria-Trani, and Cagliari-Oristano with Cagliari, Oristano and Sud Sardegna, both at the 20% increase (Napoli at 0). The Cagliari-Oristano chamber's own page says its four territories are Cagliari, Medio Campidano, Sulcis Iglesiente and Oristano, which is the whole former Sud Sardegna. The Bari chamber runs a branch office at Barletta. D.P.R. 4 August 2026 n. 183 (GU n. 238 of 13 October 2026, in force 28 October 2026) suppresses the sigla «Sud Sardegna SU» and gives «SU» to Sulcis Iglesiente, and adds «VS» (Medio Campidano), «OG» (Ogliastra) and «OT» (Gallura Nord-Est Sardegna): a company's province field can now hold VS, OG or OT too.

**Sources.**

1. Camere di commercio, national portal «Diritto Annuale», https://dirittoannuale.camcom.it/ , script `main-AOTDNTI4.js` (the chamber table of the fee calculator): `text:"Camera di commercio di BARI  (Bari, Barletta-Andria-Trani)",provList:[10,18],magg:20` and `text:"Camera di commercio di CAGLIARI - ORISTANO (Cagliari, Oristano, Sud Sardegna)",provList:[20,61,89],magg:20`; in the same script the province list is `{value:10,text:"BA"}`, `{value:18,text:"BT"}`, `{value:20,text:"CA"}`, `{value:61,text:"OR"}`, `{value:89,text:"SU"}`, and `Camera di commercio di NAPOLI` has `magg:0`, `NUORO` `provList:[60]` and `SASSARI` `provList:[88]`, both `magg:20`. (Read as the page's published code, 2026-11-12.)
2. Camera di Commercio Cagliari-Oristano, «Chi siamo» (https://www.caor.camcom.it/la-camera-di-commercio/chi-siamo): «L'ambito di riferimento della nuova Camera è determinato da quattro province: Città metropolitana di Cagliari, Provincia del Medio Campidano, Provincia del Sulcis Iglesiente e Provincia di Oristano.» And «Quanto si paga» (https://www.caor.camcom.it/registro-imprese/diritto-annuale/quanto-si-paga): «Per il triennio 2026-2028, come autorizzato dal Ministero delle Imprese e del Made in Italy con Decreto ministeriale 17 marzo 2026, il diritto annuale è maggiorato del 20%.»
3. Camera di Commercio di Bari, «Uffici e sedi» (https://www.ba.camcom.it/bari/uffici-e-sedi): «SEDI DECENTRATE Barletta: Via Municipio n. 22 - 70051 Barletta (BA)». Camere di commercio, Unioncamere «Elenco delle Camere di commercio italiane» (https://unioncamere.gov.it/sistema-camerale/elenco-delle-camere-di-commercio-italiane) lists «Camera di commercio di BARI Corso Cavour, 2 - 70121 BARI» and no chamber of Barletta-Andria-Trani.
4. D.P.R. 4 agosto 2026 n. 183, GU Serie Generale n. 238 of 13-10-2026 (https://www.gazzettaufficiale.it/eli/id/2026/10/13/26G00198/sg, in force 28/10/2026; text read at http://www.gazzettaufficiale.biz/atti/2026/20260238/26G00198.htm): «Considerato che, a seguito dell'individuazione della circoscrizione territoriale delle nuove province del Sulcis Iglesiente, del Medio Campidano, dell'Ogliastra e della Gallura Nord-Est Sardegna e della soppressione della provincia del Sud Sardegna [...]»; «d) le parole: «Sud Sardegna SU» sono sostituite dalle seguenti: «Sulcis Iglesiente SU»»; «Medio Campidano VS», «Ogliastra OG», «Gallura Nord-Est Sardegna OT» are inserted.
5. Agenzia, table T6 «Sigla delle province italiane per diritti camerali» (https://www.agenziaentrate.gov.it/portale/documents/20143/448330/Tabella+sigle+province+italiane+per+diritti+camerali_T6_Prov_diritti_camerali_20_11_2017.pdf/e937024c-6d67-a595-d65f-79b5fce523ce?t=1511255918167): the province codes of the F24 «codice ente» for code 3850 have BA, CA, OR, NU, SS and no BT and no SU: the company pays under its chamber's province.
6. Annex A of D.M. 17 March 2026 (the 61 chambers at 20%) was read by the REB-1068 run as an image and the pack's table already holds both «Bari» and «Cagliari-Oristano» from it; I did not read it again. The two chambers' 20% is confirmed by sources 1 and 2.

**Code implication.**
- A5 rows «Which chamber covers the province of Barletta-Andria-Trani (BT)» and «Which chamber covers the province of Sud Sardegna (SU)»: `to verify` becomes `verified 2026-11-12`, with sources 1-3. Add a row for the SU sigla change (source 4).
- `law_chamber_fee.py`: `_CHAMBERS` entry `_c("Bari", "BA")` becomes `_c("Bari", "BA", "BT")`; `_c("Cagliari-Oristano", "CA", "OR")` becomes `_c("Cagliari-Oristano", "CA", "OR", "SU", "VS")` (VS: source 2 names Medio Campidano, and the portal's «Sud Sardegna» covered its territory). `_UNMAPPED = ("BT", "SU")` loses BT and SU (leave only what is still open, see below, or delete the field and its docstring paragraph «Which chamber a province belongs to»); the module docstring («no primary source read says which chamber covers them») changes accordingly. The fee for BT is then Bari's: 120 euro for a company up to 100,000 euro of turnover (100 x 1.20), same as BA; for SU and VS it is Cagliari-Oristano's.
- Still open and not covered by any source I read: **OG** (Ogliastra) and **OT** (Gallura Nord-Est Sardegna). Nuoro and Sassari (whose site now reads «Camera di Commercio Nord Sardegna») are the likely chambers, but no page I read says so. Until one does, `chamber_for("OG")` and `("OT")` must stay `None` (the fee is then «to verify»).
- The F24 «codice ente» for a BT company is not stated by a source (table T6 only shows that it is a chamber's province code); the pack writes none today.

---

## 5. Chamber fee in a company's first year (new registration)

**Question.** What does an srl pay in the year it registers, and when?

**Verdict: settled** for the amount and the date; **taxpayer fact** for which year is the first: the date the company applied for registration in the registro imprese (the product stores no incorporation or registration date).

**Answer.** An srl registering in 2026 pays the first-tier fixed amount, with no turnover: 200 euro before the 50% cut, so 100 euro, plus the chamber's increase: 120 euro in a 20% chamber, 170 in the six Sicilian ones, 100 in Napoli. It is not pro-rated by the months left in the year. It is due with the application or within 30 days of it, by F24 (code 3850) or at the counter, not on 30 June.

**Sources.**

1. D.I. 21 April 2011, art. 4 c. 2 (GU n. 127 of 3-6-2011, https://www.bo.camcom.it/sites/default/files/diritto-annuale/normativa/D.M.%2021%20aprile%202011%20Misura%20diritto%20annuale%202011.pdf): «Le nuove imprese diverse da quelle di cui al comma 1 iscritte nel registro delle imprese nel corso del 2011 sono tenute a versare l'importo relativo alla prima fascia di fatturato pari a € 200,00, entro trenta giorni dalla presentazione della domanda dell'iscrizione, tramite modello F24 o direttamente allo sportello camerale». (The 2011 text. The MIMIT note below says the D.I. of 8 January 2015 applies «le disposizioni degli articoli da 2 a 6» of this decree for every later year; I did not read the D.I. of 2015 itself.)
2. MIMIT, nota n. 9347 of 16 January 2026 (https://www.mimit.gov.it/images/stories/normativa/allegati/Nota_n._9347_del_16_gennaio_2026_-Misure_del_diritto_annuale_2026.pdf): «è stato adottato il decreto interministeriale 8 gennaio 2015, con il quale sono state determinate le misure del diritto annuale dovuto a decorrere dal 2015, applicando le disposizioni degli articoli da 2 a 6 del decreto interministeriale 21 aprile 2011, con l'aggiornamento dinamico di tutti i riferimenti e termini temporali indicati»; «Si rammenta che anche la misura fissa prevista per la prima fascia di fatturato [...] è soggetta, a conclusione del calcolo, alla riduzione complessiva del 50%, con la conseguenza che per le imprese con fatturato fino a 100.000,00 euro, l'importo del diritto annuale da versare è pari ad € 100,00.»
3. Camera di commercio del Molise, «Nuove iscrizioni» 2026 (https://www.molise.camcom.gov.it/print/pdf/node/2886): «Nella tabella di seguito sono riportati gli importi dovuti dalle imprese e dagli altri soggetti di nuova iscrizione che contengono la maggiorazione del 20%.» and the row «Per tutti gli altri soggetti che si iscrivono nel Registro Imprese: società di persone, società di capitali, cooperative, consorzi, GEIE, [...] € 120,00 € 24,00»; «Per nuove imprese, unità locali e soggetti R.E.A. iscritti in corso d'anno il versamento del diritto annuale può avvenire contestualmente alla presentazione della domanda di iscrizione [...] oppure entro i 30 giorni successivi con modello F24.»; «L'importo del diritto non è frazionabile in rapporto alla durata di iscrizione nell'anno (D.M. 359/2001, art. 3, comma 2).» Same 120 euro for «Società di capitali» at Catanzaro-Crotone-Vibo (https://czkrvv.camcom.it/diritto-annuale/importi-diritto-annuale) and, before the decree of 17 March 2026, 100 euro at Torino (https://www.to.camcom.it/sites/default/files/diritto-annuale/2026%20diritto%20annuale%20NUOVE%20IMPRESE%20nota%20mimit%20-%20genn.pdf).

**Code implication.**
- No new constant. `ChamberFeeLaw.fee(2026, Decimal(0), province)` already returns 120 (100 x 1.20), 170 for a Sicilian chamber and 100 for Napoli, which is the first-year amount (`national_amount(0)` is the 200 fixed tier).
- `tax_items.py::_chamber_fee_payment` (docstring: «A company's first year pays a fixed amount by another rule and the entity stores no incorporation date: that case is not modelled»): add the registration-year branch: if the entity's registration date is in `year`, the amount is `fee(year, Decimal(0), province)` with state `due` (not `forecast`: it needs no estimate of turnover) and the due date is the application date + 30 days instead of the first income-tax advance (`TaxDateRule` of kind `annual_fee`, 30 June, in `pack.py` ~l. 476). That needs a new field on `LegalEntity` (the date of the registration application). The second year's turnover rule («turnover of the previous year») was not read for a company that registered part-way through the previous year: do not extend this branch to it.
- `law_chamber_fee.py` docstring, «Not modelled: [...] the fee of a company in the year it registers»: remove it from the list once the branch exists, and cite sources 1-3.

---

## 6. F24: does an F24 show a withholding credit (ritenute subite)? Can an ordinary person compensate them on F24?

**Verdict: settled.** No.

**Answer.** A withholding suffered is not a line of an F24, and not a credit a person can use in compensation. It is deducted inside the return (rigo RN33, col. 4 → RN34): the F24 of the balance then carries only the net IRPEF (code 4001), and the next year's advance is also computed on that net figure. A credit exists for F24 only when the return itself shows an excess (then art. 17 of D.Lgs. 241/1997 allows compensation of credits «risultanti dalle dichiarazioni»).

**Sources.**

1. D.Lgs. 241/1997 art. 17 c. 1 (https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legislativo:1997-07-09;241~art17!vig=2026-11-12): «I contribuenti eseguono versamenti unitari delle imposte [...] con eventuale compensazione dei crediti, dello stesso periodo, nei confronti dei medesimi soggetti, risultanti dalle dichiarazioni e dalle denunce periodiche presentate successivamente alla data di entrata in vigore del presente decreto.» And, for over 5,000 euro a year of income-tax credits, compensation from «il decimo giorno successivo a quello di presentazione della dichiarazione [...] da cui il credito emerge».
2. TUIR art. 22 (Normattiva, text in force to 31-12-2026, https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.del.presidente.della.repubblica:1986-12-22;917~art22!vig=2026-11-12), c. 1: «Dall'imposta determinata a norma dei precedenti articoli si scomputano nell'ordine: [...] c) le ritenute alla fonte a titolo di acconto operate sui redditi che concorrono a formare il reddito complessivo»; c. 2: «Se l'ammontare complessivo dei crediti di imposta, dei versamenti e delle ritenute, è superiore a quello dell'imposta netta sul reddito complessivo, il contribuente ha diritto, a sua scelta, di computare l'eccedenza in diminuzione dell'imposta del periodo d'imposta successiva o di chiederne il rimborso in sede di dichiarazione dei redditi.»
3. Agenzia, Redditi PF 2026, Fascicolo 1 (https://infoprecompilata.agenziaentrate.gov.it/portale/documents/d/guest/pf1_istruzioni_2026.pdf):
   - Parte I, «La compensazione»: «Il contribuente ha la facoltà di compensare nei confronti dei diversi enti impositori [...] i crediti e i debiti risultanti dalla dichiarazione e dalle denunce periodiche contributive»; «Possono avvalersi della compensazione tutti i contribuenti, a favore dei quali risulti un credito d'imposta dalla dichiarazione e dalle denunce periodiche contributive.»
   - Rigo RN33 col. 4: «indicare il totale delle ritenute subite e già indicate nei diversi quadri»; rigo RN34 «Differenza»: «RN26 – RN27 – RN28 – [...] + RN33 col. 3 – RN33 col. 4».
   - Parte I: «Al fine di calcolare l'eventuale imposta a debito o a credito è necessario considerare anche gli acconti già versati e le ritenute d'acconto.»
   - Rigo RN62: «l'acconto è dovuto in misura pari all'importo indicato nel campo "Differenza" dei citati righi RN34, ovvero RN61.» So the advance base is the tax net of the withholdings.
   - Quadro RX: «Nella presente colonna va riportato l'intero importo del debito risultante dalla dichiarazione e non quello della prima rata»; the credit «possono essere richiesti a rimborso, utilizzati in compensazione ai sensi dell'art. 17 del D. Lgs. n. 241 del 1997 o in diminuzione delle imposte dovute per i periodi successivi»; no refund or compensation of an IRPEF credit of 12 euro or less.
4. Agenzia, code page 1040 (question 8): «(5) importi a credito compensati: NON COMPILARE». The pages I read carry no code for a «ritenute subite» credit; I did not scan the whole table.

**Code implication.**
- A5 row «Whether a withholding suffered on sales is a line of the F24 at all, or only reduces the balance computed in the return»: `to verify` becomes `verified 2026-11-12`: only reduces the balance (sources 1-3).
- `tax_credits.py::refresh_withholding_credits` writes the `withholding_suffered` credit as a negative due item (`source_kind=tax_credit`), and `f24.py` lets it be grouped as a credit line of an F24 («Due items that fall on the same date, credits included, are grouped into one F24»). Per the sources it must not be an F24 line and must not net against another F24 line. Keep the netting where it already is (`ordinary_estimate.py` line `withholdings`, and the declared figure which «already nets the withholdings»: `tax_items`, `tax_credits.py` docstring) and either stop writing the due item or give it a kind the F24 grouping refuses (information only, not payable and not groupable). The existing rule «once a declared figure is typed the credit item is dropped» is the right behaviour in every case, not just after a declared figure: the IRPEF item's own amount is net.
- Same finding for the IVA credit carried forward (`IVA_ORIGIN = "iva_carry_forward"` in the same file): the credit reduces the next period's liquidation, it is not an F24 line. The monthly codes 6001-6012 say «importi a credito compensati: NON COMPILARE»; a credit appears in an F24 only as a compensation with 6099 («Il codice e' utilizzabile anche per indicare un importo a credito da compensare») or 6036-6038 («solo per indicare un importo a credito da compensare»), and then it is an act of the taxpayer, not a due item the product can schedule.
- A5 row «Year in which a withholding suffered counts as a credit, for a natural person»: unchanged. Add a note to A5 row «Income tax advance 100%...»: the advance base is RN34, i.e. net of withholdings (the code already does it, `advances.py` docstring).
- The same art. 17 c. 1 limit applies to an srl (credits only «risultanti dalle dichiarazioni»); this does not settle the year rule for an srl (A5 row «The same year rule for the withholdings an srl suffers (IRES)» stays `to verify`).

---

## 7. F24: the IVA codes (6001-6012, 6031-6034, 6099)

**Verdict: settled** for 6001-6012, 6013, 6031-6033, 6035 and 6099; **accountant** for one sub-point: which payers use 6034 (the table says only «QUARTO TRIMESTRE»).

**Answer.** The Agenzia's code pages give: one code per month (6001 January ... 6012 December), no instalment, no month field; one per quarter (6031, 6032, 6033), the 1% interest added to the same line (scheda quoted below); 6099 is the payment «sulla base della dichiarazione annuale», the only one of the three groups that can be paid in instalments (field `NNRR`) or carry a credit. For all of them the `anno di riferimento` is the tax year the payment is for. 6013 («VERSAMENTO ACCONTO PER IVA MENSILE») and 6035 («VERSAMENTO IVA ACCONTO») are the advance codes; the pages do not say which payer uses which.

| Code | Agenzia's wording | Can it be paid in instalments? | Credit |
|---|---|---|---|
| 6001 ... 6012 | VERSAMENTO IVA MENSILE GENNAIO ... DICEMBRE | no («in unica soluzione») | no («NON COMPILARE») |
| 6013 | VERSAMENTO ACCONTO PER IVA MENSILE | no | no |
| 6031, 6032, 6033 | VERSAMENTO IVA TRIMESTRALE 1, 2, 3 TRIMESTRE (art. 74) | no | no |
| 6034 | VERSAMENTO IVA QUARTO TRIMESTRE (art. 74) | no | no |
| 6035 | VERSAMENTO IVA ACCONTO | no | no |
| 6099 | VERSAMENTO IVA SULLA BASE DELLA DICHIARAZIONE ANNUALE | yes («forma rateale», `rateazione` = `0107` in the example) | yes («anche per indicare un importo a credito da compensare») |
| 6036, 6037, 6038 | CREDITO IVA ART. 38BIS COMMA 2 DPR 633/1972 1, 2, 3 TRIMESTRE | no | only a credit (or a ravvedimento debit) |
| 6040 | IVA DOVUTA DALLE PP.AA. - SCISSIONE DEI PAGAMENTI - ART. 17-TER DEL DPR N. 633/1972 | no | no |

**Sources.** Agenzia, «Ricerca codici tributo», `https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=<code>` for each code above (read 2026-11-12). Quotes: 6001: «VERSAMENTO IVA MENSILE GENNAIO [...] L'importo a debito deve essere versato in unica soluzione»; «(2) rateazione/regione/prov/mese rif: NON COMPILARE (3) anno di riferimento: Anno d'imposta per cui si effettua il pagamento [...] (5) importi a credito compensati: NON COMPILARE»; 6031: «VERSAMENTO IVA TRIMESTRALE 1 TRIMESTRE», «Art. 74»; 6034: «VERSAMENTO IVA QUARTO TRIMESTRE»; 6099: «L'importo a debito puo' essere versato in forma rateale. Il codice e' utilizzabile anche per indicare un importo a credito da compensare» and «(2) rateazione/regione/prov/mese rif: Indicare due cifre numeriche per il numero di rata da versare e due cifre numeriche per il numero di rate in cui si intende frazionare il debito, nell'esempio '0107'». Agenzia, scheda «F24 IVA Periodica - Come e quando si versa» (https://www.agenziaentrate.gov.it/portale/schede/pagamenti/f24ivap10/come-e-quando-si-versa): «contribuenti trimestrali: [...] entro il 16 del secondo mese successivo a ciascuno dei primi tre trimestri solari (16 maggio, 20 agosto e 16 novembre). Il versamento relativo all'ultimo trimestre va effettuato in sede di conguaglio annuale entro il 16 marzo dell'anno successivo»; «In caso di versamento trimestrale l'eventuale debito d'imposta deve essere maggiorato dell'1% a titolo di interesse»; for the special quarterly payers the fourth quarter goes «entro il 16 febbraio dell'anno successivo [...] al netto dell'acconto eventualmente versato entro il 27 dicembre».

The sub-point for the accountant: the scheda sends the ordinary quarterly payer's fourth quarter to the annual settlement (6099), and the special quarterly payer's to 16 February; the page of 6034 does not say which of the two (or neither) uses 6034.

**Code implication.**
- A5 row «F24 tax codes for the periodic IVA payments (60xx), for the withholding on pay to others (1040), for payroll contributions and INAIL, and for a credit used in an F24»: split it. IVA: `verified 2026-11-12` as in the table, except 6034 (accountant) and the credit use (question 6).
- `payment_data.py` / `law_payment.py`: `F24Line` has no IVA line. Add `iva_monthly` (a code per period month: 6000 + month), `iva_quarterly` (6031 + quarter - 1 for the first three), `iva_annual` (6099) and `iva_advance` (6013 monthly, 6035 quarterly; the page of 6013 says «ACCONTO PER IVA MENSILE», of 6035 «VERSAMENTO IVA ACCONTO», and does not say who uses 6035), each as an `F24Code` of `kind="tax_code"` with the page's own wording and URL. The code depends on the period, not on the year: `F24Code` is one code per line today, so the IVA entry needs a period-parameterised lookup (a dict month to code) in `PaymentRulesLaw`, and `f24_codes.py::line_of` must read the IVA settlement's period (it only knows income-tax slots and the chamber fee).
- Left empty by the pages: the `rateazione/mese` field for 6001-6034 (empty); the `anno di riferimento` is the year of the period (a December 2025 payment on 16 January 2026 is `6012`, year 2025 by the page's «Anno d'imposta per cui si effettua il pagamento»; the page example is a same-year payment, so this last inference is `[INFERENCE]`, to confirm on a real F24 of January).

---

## 8. F24: withholding on employees and on self-employed (1001, 1040)

**Verdict: settled.**

**Answer.** 1001 is the withholding on employees' pay (art. 23 c. 2 D.P.R. 600/1973), 1040 on self-employed fees (art. 25 D.P.R. 600/1973). Both are Erario-section lines, paid in one payment, with the **month** of the payment in the `rateazione/regione/prov./mese rif.` column as two digits and the tax year in `anno di riferimento`; no credit.

**Sources.** Agenzia, «Ricerca codici tributo»:
- https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1001 : «RITENUTE SU RETRIBUZIONI PENSIONI TRASFERTE MENSILITA' AGGIUNTIVE E RELATIVO CONGUAGLIO», «Decreto del Presidente della Repubblica 600 del 29/09/1973 Art. 23 Comma 2», «(2) rateazione/regione/prov/mese rif: indicare il numero del Mese formato da 2 caratteri numerici, nell'esempio '03' - Marzo. (3) anno di riferimento: Anno d'imposta per cui si effettua il pagamento», «L'importo a debito deve essere versato in unica soluzione», «(5) importi a credito compensati: NON COMPILARE».
- https://www1.agenziaentrate.gov.it/servizi/codici/ricerca/SezioneErario.php?CT=1040 : «RITENUTE SU REDDITI DI LAVORO AUTONOMO COMPENSI PER L'ESERCIZIO DI ARTI E PROFESSIONI», «Decreto del Presidente della Repubblica 600 del 29/09/1973 Art. 25», same fields.
- The Agenzia's «Allegato 1 - Modello F24/770 anno 2026, Codici tributo relativi alle ritenute/trattenute operate» lists 1001 under the group of withholdings on «RETRIBUZIONI» (found, not read in full).

**Code implication.** Add `F24Line` values for them (for example `withholding_employees`, `withholding_self_employed`), `_tax(...)` entries with these descriptions and sources, and the month field: `F24Code`'s docstring only knows the year and the `NNRR` column, so the `mese rif.` column needs its own slot on the line (`f24_codes.py::LineCode`). The withholding dates (`_WITHHOLDING_DATES` in `pack.py`, 16th, 20 August) are untouched. A5 row about «the withholding on pay to others (1040)» becomes `verified` for the code (the date was already in the calendar).

---

## 9. F24: INPS payroll lines (DM10 causali)

**Verdict: settled** for the contributions of employers' payroll (DM10, DMRA, RC01, DMDP/DMDD and the instalment/credit rules); **accountant** (not settled by what I read) for INAIL.

**Answer.** An employer's INPS contributions go on the «Sezione INPS» of the F24 with causale **DM10**; a rectification with **DMRA**; a credit used in compensation with **RC01** (credit only); instalments with **DMDP-DMDD**. The INPS columns are: codice sede (the INPS office), causale contributo, matricola INPS (format 1: ten digits), periodo di riferimento «da» (`MM/AAAA`, the month of the payroll) and «a» (empty), importi a debito versati. The same section serves the other INPS lines already in the pack (P10/PXX, AF/CF, AP/CP, DPPI, APR/CPR).

**Sources.**

1. Agenzia, table «Causali INPS» (updated 11-11-2026), https://www.agenziaentrate.gov.it/portale/documents/20143/448438/Causali_INPS_11_11_2026.xls/3b09e2f6-b721-b9d7-a2f7-3fce66c81f0f?t=1794389958494 (reached from https://www.agenziaentrate.gov.it/portale/strumenti/codici-attivita-e-tributo/f24-codici-tributo-per-i-versamenti/tabelle-dei-codici-tributo-e-altri-codici-per-il-modello-f24/tabelle-codici-inps-e-enti-previdenziali-ed-assicurativi). Rows read: `DM10 | DM10 attivi, passivi, insoluti | Formato 1 | MM/AAAA>=01/1989 MM/AAAA<=Data Riscossione | 0 | E`; `DMRA | DMRA rettificativi | Formato 1 | ... | D`; `RC01 | Crediti previdenziali | Formato 1 | ... | D`; `DMDP-DMDD | Rate dilazioni previdenziali | Formato 1 | ... | D`; `DMRP | Rettifiche da mod. DM10/2 | Formato 1 | ... | R`; `AF-CF`, `AP-CP`, `APR-API-CPI-CPR`, `P10-PXX`, `P10R-PXXR`, `DPPI | Gestione 10% professionisti - Rate ed interessi`. Notes of the table: «L'indicazione del termine "D" nella colonna MODALITA' DI UTILIZZO sta a significare che il tributo deve essere utilizzato solo per il versamento di imposte a debito. [...] "R" [...] solo per fruire di importi a credito. [...] "E" [...] sia per il versamento di imposte a debito che per fruire di importi a credito.» So DM10 is the one payroll causale that can carry both a debit and a credit.
2. Agenzia, «Tabella dei valori che può assumere il campo "matricola INPS/codice INPS/filiale azienda" della sezione INPS del modello F24» (https://www.agenziaentrate.gov.it/portale/documents/20143/448438/Tabella+formati+matricole+e+codici_formati_+INPS_14_12_2017.pdf/fcc0dc75-ac16-92bc-0728-e77b7ac158f7?t=1513323457313): «1 Matricola INPS (tipo 1) 10 numerici (+ 7 blank a riempimento)».
3. INPS, Circolare n. 137 of 28 July 2003, Allegato 1 (https://www.inps.it/content/dam/inps-site/it/scorporati/circolari-e-messaggi/2003/07/Circolare_1580/Allegati/705_Circolare-numero-137-del-28-7-2003_Allegato-n-1.pdf): «I datori di lavoro per il versamento dei contributi con il modello F24 devono compilare le righe della sezione INPS secondo le indicazioni che seguono: codice sede: codice della sede presso la quale è aperta la posizione contributiva aziendale; causale contributo: DM10; matricola INPS: matricola aziendale - 10 caratteri che identificano la posizione contributiva aziendale; periodo di riferimento "da": il mese ed anno di competenza del DM10/2 (es: 022003); periodo di riferimento "a": il campo non deve essere compilato; importi a debito versati». And for rectifications: «causale contributo: DMRA».
4. INPS, Circolare n. 6 of 22 January 2025 (https://www.inps.it/content/dam/inps-site/it/scorporati/circolari-e-messaggi/2025/01/Circolare_14782/Allegati/15855_Circolare-numero-6-del-22-01-2025.pdf): the same INPS section is described in current words («la causale [...] nella sezione "INPS", nel campo "causale contributo", in corrispondenza, esclusivamente, del campo "importi a debito versati"»; «nel campo "codice sede", il codice della sede INPS competente; nel campo "matricola INPS/codice INPS/filiale azienda", la matricola INPS del datore di lavoro; nel campo "periodo di riferimento", nella colonna "da mm/aaaa", il mese e l'anno di competenza, nel formato MM/AAAA; la colonna "a mm/aaaa" non deve essere valorizzata»).

The old circular (2003) predates the Uniemens flow; the Agenzia's table of 11 November 2026 is the proof that DM10 is still a live causale.

**INAIL: not settled.** The Agenzia's table of «Altri enti previdenziali e assicurativi» (https://www.agenziaentrate.gov.it/portale/strumenti/codici-attivita-e-tributo/f24-codici-tributo-per-i-versamenti/tabelle-dei-codici-tributo-e-altri-codici-per-il-modello-f24/tabelle-codici-inps-e-enti-previdenziali-ed-assicurativi/altri-enti-previdenziali_assicurativi) has no INAIL table (only the pension funds and a list of INAIL offices); the INAIL section's fields were not found in a source I could read. It stays on the accountant's list: **what the payroll's INAIL premium puts in the F24's INAIL section** (codice sede, codice ditta, number of reference, causale).

**Code implication.**
- A5 row on «payroll contributions and INAIL»: INPS payroll `verified 2026-11-12` (DM10, DMRA, RC01, DMDP-DMDD); INAIL `to verify`.
- `payment_data.py::_INPS_CODES` (the INPS lines of the pack, `kind="inps_causale"`): add `inps_payroll` = `DM10` (source: sources 1-3, format 1 from source 2), and `F24Line` in `law_payment.py` gets the matching value. `_INPS_CODES` is in the `f24_codes` of `IT_FLAT_RATE_PAYMENT_2026` and `IT_STANDARD_PAYMENT_2026`, not in `IT_COMPANY_PAYMENT_2026`; a payroll line belongs to whichever entity has staff, so the company pack needs the payroll line too (it carries no INPS lines at all today), and the flat-rate pack keeps it because art. 1 c. 69 L. 190/2014 makes a flat-rate holder a withholding agent for employees (A5 row «A flat-rate taxpayer is not a withholding agent, except on employees»).
- `f24_codes.py::line_of`: the header says «the payroll codes [...] are not in the pack today»; once added, a payroll due item (`PAYROLL_SOURCE_KIND`) maps to `inps_payroll` and carries the payroll month as «periodo da».

---

## 10. The 2026-11-09 comment, question 6 (revenue basis and the 4% surcharge)

**Verdict: settled** (both halves).

**Answer.** First half: the flat-rate basis is cash (collected in the year), for a profession and a business alike (question 3). Second half: the 4% INPS surcharge is left **in** the base for a gestione separata professional, not out (question 2); a Cassa's contributo integrativo is out. «Does the accountant leave the 4% out of the base?» has no accountant-dependent answer: a source states it.

**Code implication.** The A5 rows «Flat-rate basis: revenue and fees collected...» (verified, add Circolare 10/E § 4.3.5) and «Flat-rate basis: whether the INPS surcharge lines...» (to verify to verified, `count_by_the_coefficient` to `True`: see question 2). The other five questions of that comment (contribution base, which year's contributions, INPS rate, coefficient, substitute rate) are answered elsewhere in A5 (rows 223-229 of the register, `verified 2026-11-11`) and are not part of this report.

---

## Findings outside the six questions

1. **IRPEF advance base.** The Redditi PF 2026 instructions (RN34 and RN62, question 6) say the advance is the «Differenza» of RN34, which is net of the withholdings suffered. The code (`advances.py`, `ordinary_estimate.py`) already does it; A5 row «Income tax advance 100%...» can cite RN62.
2. **Interest causale of artigiani and commercianti instalments** (A5 row «The causale for the interest of an instalment of artigiani and commercianti contributions», `to verify`): the Agenzia's table of 11 November 2026 lists `APR-API-CPI-CPR`, and INPS, Circolare n. 87 of 3 May 2002 (https://www.inps.it/bussola/visualizzadoc.aspx?svirtualurl=/circolari/circolare+numero+87+del+3-5-2002.htm), says: «La predetta maggiorazione dello 0,40 per cento deve essere versata separatamente dai contributi, utilizzando la causale contributo "API" (artigiani) o "CPI" (commercianti)» and «gli interessi vanno esposti separatamente dai contributi; le causali CP, CPR, AP, APR devono quindi riguardare solo contributi». So `API` (artigiani) and `CPI` (commercianti) carry the 0.40% of a deferral and the interest of an instalment; the A5 row can be verified from these two sources (a 2002 circular, confirmed live by the table). Not part of this ticket's scope; no code read for it.
3. **Calendar.** The Molise chamber's page (see question 5) mentions a D.L. 22 maggio 2026 n. 89 that moved the 2026 income-tax payment dates for ISA subjects «inclusi i soggetti che presentano cause di esclusione» (to 20 July, with 0.80% to 20 August). I did not read the decree; the pack's 2026 dates (30 June, 30 July with 0.40%) were verified on 2026-11-10 against the Agenzia's scadenzario. Worth one look by whoever owns the calendar rows.

## Summary

| # | Question | Verdict |
|---|---|---|
| 1 | Substitute tax in instalments (saldo 1792, first advance 1790 yes; 1791 no; 1793 is another regime) | settled |
| 2 | Is the 4% INPS surcharge in the flat-rate revenue | settled: in, for a gestione separata professional (Cassa integrativo out) |
| 3 | Cash or accrual for a person with business income in the flat-rate regime | settled: cash |
| 4 | Chamber for BT and SU | settled: BT to Bari, SU to Cagliari-Oristano (SU now means Sulcis Iglesiente; VS Medio Campidano also Cagliari-Oristano; OG and OT open) |
| 5 | Chamber fee in a company's first year | settled (100 x (1 + increase), within 30 days of the application, not pro-rated); taxpayer fact: registration date (new field) |
| 6 | Does an F24 show a withholding credit | settled: no; the credit is netted in the return |
| 7 | F24 codes of IVA (6001-6012, 6031-6034, 6099) | settled, except which payers use 6034: accountant |
| 8 | F24 codes of withholding (1001, 1040) | settled |
| 9 | F24 INPS payroll lines (DM10) | settled for INPS; INAIL: accountant |
| 10 | 2026-11-09 comment question 6 | settled |
