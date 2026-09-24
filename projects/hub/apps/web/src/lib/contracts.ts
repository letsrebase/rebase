/**
 * The drafts the matches pages edit (REB-387): an input always holds a string, the API
 * takes typed values, and these helpers are the one place the two meet. Kept out of the
 * page files, which export components only (`react-refresh/only-export-components`).
 */
import {
  LETTERA_TEXT_KEYS,
  type Cliente,
  type ClienteDraft,
  type ContractDocument,
  type Fiscal,
  type FiscalData,
  type Lettera,
  type LetteraDraft,
  type LetteraTextKey,
  type Match,
  type SendReport,
} from './api'

export type FiscalDraft = Record<keyof FiscalData, string>

export const FISCAL_EMPTY: FiscalDraft = { codice_fiscale: '', partita_iva: '', domicilio: '', pec: '' }

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    codice_fiscale: fiscal.codice_fiscale,
    partita_iva: fiscal.partita_iva,
    domicilio: fiscal.domicilio,
    pec: fiscal.pec ?? '',
  }
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address. */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    codice_fiscale: draft.codice_fiscale.trim(),
    partita_iva: draft.partita_iva.trim(),
    domicilio: draft.domicilio.trim(),
    pec: draft.pec.trim() || null,
  }
}

export type ClienteForm = Record<keyof Cliente, string>
export const CLIENTE_EMPTY: ClienteForm = { cliente_ragione_sociale: '', cliente_piva: '', cliente_sede: '' }

export function clienteForm(draft: ClienteDraft): ClienteForm {
  return {
    cliente_ragione_sociale: draft.cliente_ragione_sociale ?? '',
    cliente_piva: draft.cliente_piva ?? '',
    cliente_sede: draft.cliente_sede ?? '',
  }
}

export function toCliente(form: ClienteForm): Cliente {
  return {
    cliente_ragione_sociale: form.cliente_ragione_sociale.trim(),
    cliente_piva: form.cliente_piva.trim(),
    cliente_sede: form.cliente_sede.trim(),
  }
}

export type LetteraForm = Record<LetteraTextKey, string> & {
  data_inizio: string
  data_fine: string
  compenso: string
  giorni_pagamento: string
  fine_mese: boolean
  giorni_preavviso: string
}
export type LetteraFieldKey = keyof LetteraForm

const TEXT_EMPTY = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, ''])) as Record<LetteraTextKey, string>

export const LETTERA_EMPTY: LetteraForm = {
  ...TEXT_EMPTY,
  data_inizio: '',
  data_fine: '',
  compenso: '',
  giorni_pagamento: '',
  fine_mese: false,
  giorni_preavviso: '',
}

export function letteraForm(draft: LetteraDraft): LetteraForm {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, draft[key] ?? ''])) as Record<LetteraTextKey, string>
  return {
    ...text,
    data_inizio: draft.data_inizio ?? '',
    data_fine: draft.data_fine ?? '',
    compenso: draft.compenso ?? '',
    giorni_pagamento: draft.giorni_pagamento === null ? '' : String(draft.giorni_pagamento),
    fine_mese: draft.fine_mese ?? false,
    giorni_preavviso: draft.giorni_preavviso === null ? '' : String(draft.giorni_preavviso),
  }
}

export function toLettera(form: LetteraForm): Lettera {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, form[key].trim() || null])) as Record<
    LetteraTextKey,
    string | null
  >
  return {
    ...text,
    data_inizio: form.data_inizio,
    data_fine: form.data_fine || null,
    compenso: form.compenso.replace(',', '.').trim(),
    giorni_pagamento: Number(form.giorni_pagamento),
    fine_mese: form.fine_mese,
    giorni_preavviso: form.giorni_preavviso.trim() ? Number(form.giorni_preavviso) : null,
  }
}

/** The letter's fields as step 4 names them. The client's budget has no label here,
 *  because it has no field anywhere in this flow (spec § 1h). */
export const LETTERA_LABELS: Record<LetteraFieldKey, string> = {
  ruolo: 'Ruolo',
  attivita: 'Cosa fa il professionista',
  risultati: 'Risultati da consegnare, solo a corpo',
  accettazione: 'Come il cliente accetta i risultati, solo a corpo',
  data_inizio: 'Inizio',
  data_fine: 'Fine prevista',
  impegno: 'Impegno',
  periodo_verifica: 'Periodo iniziale di verifica',
  luogo: 'Luogo',
  coordinamento: 'Coordinamento concordato con il cliente',
  referente_cliente: 'Referente del cliente',
  referente_rebase: 'Referente di rebase',
  modalita: 'Modalità',
  compenso: 'Compenso, IVA esclusa (€)',
  unita: 'Unità del compenso',
  lavoro_extra: 'Lavoro festivo o fuori fascia',
  spese: 'Spese',
  giorni_pagamento: 'Giorni di pagamento',
  fine_mese: 'Contati da fine mese',
  scadenze_fatturazione: 'Fatture, solo a corpo',
  giorni_preavviso: 'Giorni di preavviso',
  dati_personali: 'Tratta dati personali del cliente',
  dati_finalita: 'Natura e finalità del trattamento',
  dati_categorie: 'Categorie di dati',
  dati_interessati: 'Categorie di interessati',
  dati_autorizzazione: 'Autorizzazione scritta del cliente alla nomina',
  esclusiva: 'Esclusiva verso il cliente',
  portfolio: 'Citazione nel portfolio',
  assicurazione: 'Assicurazione di responsabilità civile professionale',
  altre_condizioni: 'Altre condizioni',
  rapporti_precedenti: 'Rapporti precedenti con il cliente',
}

/** Step 4 in the letter's own sections. */
export const LETTERA_GROUPS: readonly { title: string; fields: readonly LetteraFieldKey[] }[] = [
  { title: 'Attività', fields: ['ruolo', 'attivita', 'risultati', 'accettazione'] },
  { title: 'Tempi e impegno', fields: ['data_inizio', 'data_fine', 'impegno', 'periodo_verifica'] },
  { title: 'Modalità di lavoro', fields: ['luogo', 'coordinamento', 'referente_cliente', 'referente_rebase'] },
  {
    title: 'Condizioni economiche',
    fields: ['modalita', 'compenso', 'unita', 'lavoro_extra', 'spese', 'giorni_pagamento', 'fine_mese', 'scadenze_fatturazione'],
  },
  { title: 'Preavviso', fields: ['giorni_preavviso'] },
  {
    title: 'Dati personali',
    fields: ['dati_personali', 'dati_finalita', 'dati_categorie', 'dati_interessati', 'dati_autorizzazione'],
  },
  { title: 'Condizioni particolari', fields: ['esclusiva', 'portfolio', 'assicurazione', 'altre_condizioni'] },
  { title: 'Rapporti precedenti', fields: ['rapporti_precedenti'] },
]

export const LETTERA_REQUIRED: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'ruolo',
  'attivita',
  'data_inizio',
  'compenso',
  'giorni_pagamento',
])
export const LETTERA_MULTILINE: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'attivita',
  'risultati',
  'accettazione',
  'dati_finalita',
  'altre_condizioni',
])

/** How a document is named in a button's label or a confirmation, the one place both
 *  the admin's «Match e contratti» (REB-407) and the member area's «Contratti»
 *  (REB-392) name a document: «del contratto quadro», «della lettera n. 2026-001».
 *  Takes just the two fields a label needs, so a `MemberContract` names a document the
 *  same way a `ContractDocument` does. */
export function whatOf(document: Pick<ContractDocument, 'kind' | 'numero'>): string {
  return document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
}

/** What «Annulla» on a match asks before it acts: the match and its letter's own
 *  number become `annullato` for good, and, when the letter has already left, that
 *  its envelope on the signing site is cancelled too and the freelancer's link stops
 *  working (REB-407). The framework agreement is the freelancer's, not the match's,
 *  and stays untouched either way. */
export function cancelDescription(match: Match): string {
  const base = `Il match con ${match.nome_azienda} e la lettera n. ${match.lettera.numero} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`
  return match.lettera.stato === 'inviato'
    ? `${base} La lettera è già partita: viene annullata anche sul sito di firma, e il link ricevuto dal freelance smette di funzionare.`
    : base
}

/** The sentence the pages show after «Invia per la firma» (REB-390). */
export function sendReportMessage(report: SendReport): string {
  const numero = report.match.lettera.numero
  const sent =
    report.inviato === 'quadro'
      ? `Partito il contratto quadro: la lettera n. ${numero} partirà da sola dopo la sua firma.`
      : report.inviato === 'lettera'
        ? `Partita la lettera di incarico n. ${numero}.`
        : `La lettera n. ${numero} aspetta il contratto quadro già in firma e partirà da sola dopo.`
  return report.mail_inviata === false ? `${sent} La mail però non è partita: usa «Reinvia email».` : sent
}
