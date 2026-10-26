import { describe, expect, it } from 'vitest'
import {
  confidenceBand,
  decisionLine,
  describeProposal,
  excerptPreview,
  formatAmount,
  formatConfidence,
  formatQuantity,
  type ProposalDescription,
} from './fields'
import { validateProposalsSearch } from './search'

const CONTRACT_ID = '5b2c70c2-0000-4000-8000-000000000001'
const CUSTOMER_ID = '5b0d7190-0000-4000-8000-000000000002'

const DAY = {
  contract_id: CONTRACT_ID,
  data: '2026-11-05',
  quantita: 1,
  descrizione: 'Sopralluogo al magazzino di Verona',
  approvazione: {
    canale: 'email',
    mittente: 'giulia.bianchi@nordwind.example',
    ricevuto_il: '2026-10-21T09:12:00Z',
  },
}

const CONTRACT = {
  contract: {
    customer_id: CUSTOMER_ID,
    titolo: 'Consulenza fiscale 2027',
    inizio: '2027-01-01',
    fine: '2027-12-31',
    tipo_rinnovo: 'tacito',
    preavviso_rinnovo_giorni: 90,
    preavviso_disdetta_giorni: 60,
    giorni_pagamento: 30,
    pagamento_fine_mese: true,
    cadenza_fatturazione: 'mensile',
    divisa: 'EUR',
    requires_prior_approval: false,
    applies_social_charge: true,
    note: null,
  },
  rate_card: {
    tipo: 'ricorrente_fisso',
    unita: 'mese',
    importo: '1800.00',
    valido_da: '2027-01-01',
    valido_a: null,
    ore_minime: null,
    periodo_erogazione: 'mensile',
  },
}

function rowsOf(description: ProposalDescription, group: string) {
  return Object.fromEntries(
    (description.groups.find((candidate) => candidate.title === group)?.rows ?? []).map((row) => [
      row.label,
      row.value,
    ]),
  )
}

/** A proposal as the queue holds it: nothing decided, nothing accepted yet. */
function waiting(target_type: 'contratto' | 'giornata', campi_proposti: Record<string, unknown>) {
  return { target_type, campi_proposti, campi_accettati: null, stato: 'in_attesa' }
}

describe('describeProposal, a day', () => {
  const description = describeProposal(waiting('giornata', DAY))

  it('titles the proposal with its date and quantity and names the contract it is for', () => {
    expect(description.title).toBe('Giornata del 05/11/2026 · 1 gg')
    expect(description.contractId).toBe(CONTRACT_ID)
    expect(description.customerId).toBeNull()
  })

  it('words the day and the approval it rests on', () => {
    expect(rowsOf(description, 'Giornata')).toEqual({
      Data: '05/11/2026',
      Quantità: '1 giorno',
      Descrizione: 'Sopralluogo al magazzino di Verona',
    })
    expect(rowsOf(description, 'Approvazione del cliente')).toEqual({
      Canale: 'Email',
      Mittente: 'giulia.bianchi@nordwind.example',
      'Ricevuta il': '21/10/2026',
    })
  })

  it('shows the message the approval came with when there is one', () => {
    const withMessage = describeProposal(
      waiting('giornata', { ...DAY, approvazione: { ...DAY.approvazione, message_id: '<abc@mail>' } }),
    )
    expect(rowsOf(withMessage, 'Approvazione del cliente').Messaggio).toBe('<abc@mail>')
  })

  it('reads a quantity sent as a decimal string and one sent as a JSON number the same way', () => {
    const fromString = describeProposal(waiting('giornata', { ...DAY, quantita: '0.50' }))
    const fromNumber = describeProposal(waiting('giornata', { ...DAY, quantita: 0.5 }))
    expect(fromString.title).toBe('Giornata del 05/11/2026 · 0,5 gg')
    expect(fromNumber.title).toBe(fromString.title)
    expect(rowsOf(fromNumber, 'Giornata').Quantità).toBe('0,5 giorni')
  })
})

describe('describeProposal, a contract', () => {
  const description = describeProposal(waiting('contratto', CONTRACT))

  it('titles the proposal with the contract and points at its customer', () => {
    expect(description.title).toBe('Consulenza fiscale 2027')
    expect(description.customerId).toBe(CUSTOMER_ID)
    expect(description.contractId).toBeNull()
  })

  it('words the terms in the vocabulary of the contract page', () => {
    expect(rowsOf(description, 'Contratto')).toMatchObject({
      Periodo: '01/01/2027 – 31/12/2027',
      Rinnovo: 'Tacito rinnovo',
      'Preavviso di rinnovo': '90 giorni',
      'Preavviso di disdetta': '60 giorni',
      Pagamento: '30 giorni data fattura fine mese',
      'Approvazione preventiva': 'No',
      'Rivalsa INPS': 'Sì',
    })
  })

  it('speaks of one day of notice in the singular', () => {
    const one = describeProposal(
      waiting('contratto', { ...CONTRACT, contract: { ...CONTRACT.contract, preavviso_disdetta_giorni: 1 } }),
    )
    expect(rowsOf(one, 'Contratto')['Preavviso di disdetta']).toBe('1 giorno')
  })

  it('words the first rate card with its amount in the contract currency', () => {
    const rate = rowsOf(description, 'Prima scheda tariffaria')
    expect(rate.Tipo).toBe('Canone ricorrente')
    expect(rate.Importo?.replace(/\s/g, ' ')).toBe('1.800,00 € / mese')
    expect(rate.Validità).toBe('dal 01/01/2027')
  })

  it('does not call a flat fee «per forfait»', () => {
    const flat = describeProposal(
      waiting('contratto', { ...CONTRACT, rate_card: { ...CONTRACT.rate_card, tipo: 'una_tantum', unita: 'forfait' } }),
    )
    expect(rowsOf(flat, 'Prima scheda tariffaria').Importo?.replace(/\s/g, ' ')).toBe('1.800,00 € (forfait)')
  })

  it('shows what the accept would write even when no label exists for it', () => {
    const full = describeProposal(
      waiting('contratto', {
        ...CONTRACT,
        contract: {
          ...CONTRACT.contract,
          politica_spese: { kind: 'rimborsabile_con_tetto', tetto: '500.00' },
          custom_fields: { cig: 'Z123' },
        },
        rate_card: { ...CONTRACT.rate_card, frazioni_ammesse: [1, 0.5] },
      }),
    )
    expect(rowsOf(full, 'Contratto')['Politica spese']).toBe('rimborsabile con tetto · tetto: 500.00')
    expect(rowsOf(full, 'Contratto')['Campi personalizzati']).toBe('cig: Z123')
    expect(rowsOf(full, 'Prima scheda tariffaria')['Frazioni ammesse']).toBe('1, 0,5')
  })

  it('says a payment term left to the customer instead of leaving the row out', () => {
    const inherited = describeProposal(
      waiting('contratto', {
        ...CONTRACT,
        contract: { ...CONTRACT.contract, giorni_pagamento: null, pagamento_fine_mese: null },
      }),
    )
    expect(rowsOf(inherited, 'Contratto').Pagamento).toBe('Come da condizioni del cliente')
  })

  it('says an open-ended contract is open-ended instead of dropping the end', () => {
    const open = describeProposal(
      waiting('contratto', { ...CONTRACT, contract: { ...CONTRACT.contract, fine: null } }),
    )
    expect(rowsOf(open, 'Contratto').Periodo).toBe('01/01/2027 – a tempo indeterminato')
  })
})

describe('describeProposal once a proposal is decided', () => {
  const corrected = {
    ...CONTRACT,
    contract: { ...CONTRACT.contract, titolo: 'Consulenza fiscale 2027 (corretto)' },
  }

  it('describes what was accepted, which is what the contract was built from', () => {
    const accepted = describeProposal({
      target_type: 'contratto',
      campi_proposti: CONTRACT,
      campi_accettati: corrected,
      stato: 'accettata',
    })
    expect(accepted.title).toBe('Consulenza fiscale 2027 (corretto)')
  })

  it('keeps describing what was proposed for a rejected one, which built nothing', () => {
    const rejected = describeProposal({
      target_type: 'contratto',
      campi_proposti: CONTRACT,
      campi_accettati: corrected,
      stato: 'rifiutata',
    })
    expect(rejected.title).toBe('Consulenza fiscale 2027')
  })
})

describe('describeProposal on a shape it does not know', () => {
  it('draws no group instead of throwing, and keeps a fallback title', () => {
    expect(describeProposal(waiting('contratto', { altro: 1 }))).toEqual({
      title: 'Nuovo contratto',
      customerId: null,
      contractId: null,
      // The inherited-payment row is a fact about an absent value, so even an empty
      // contract reads one; every other row needs a value to exist.
      groups: [{ title: 'Contratto', rows: [{ label: 'Pagamento', value: 'Come da condizioni del cliente' }] }],
    })
    expect(describeProposal(waiting('giornata', {})).title).toBe('Giornata')
  })
})

describe('decisionLine', () => {
  it('says who decided and when', () => {
    expect(
      decisionLine({ stato: 'accettata', deciso_da: 'Lorenzo Fiore', deciso_il: '2026-10-25T10:00:00Z' }),
    ).toMatch(/^Accettata da Lorenzo Fiore · 25 ott 2026/)
  })

  it('does not print a placeholder for what it does not know', () => {
    expect(decisionLine({ stato: 'rifiutata', deciso_da: null, deciso_il: null })).toBe('Rifiutata')
    expect(decisionLine({ stato: 'rifiutata', deciso_da: 'Ada', deciso_il: null })).toBe('Rifiutata da Ada')
  })
})

describe('formatters', () => {
  it('prints an amount in a currency Intl does not know as the digits and the code', () => {
    expect(formatAmount('12.50', 'XX')).toBe('12,50 XX')
  })

  it('prints a declared confidence as a percentage and bands it by the decimal string', () => {
    expect(formatConfidence('0.93')).toBe('93%')
    expect(confidenceBand('1.00')).toBe('alta')
    expect(confidenceBand('0.80')).toBe('alta')
    expect(confidenceBand('0.79')).toBe('media')
    expect(confidenceBand('0.50')).toBe('media')
    expect(confidenceBand('0.49')).toBe('bassa')
    expect(confidenceBand('0.00')).toBe('bassa')
  })

  it('formats no quantity from a value that is not one', () => {
    expect(formatQuantity(undefined)).toBeNull()
    expect(formatQuantity('')).toBeNull()
    expect(formatQuantity(Number.NaN)).toBeNull()
  })

  it('flattens an excerpt to one line and cuts it with an ellipsis', () => {
    expect(excerptPreview('Ciao\n\n  Lorenzo,   ok')).toBe('Ciao Lorenzo, ok')
    const long = excerptPreview('parola '.repeat(60), 40)
    expect(long).toHaveLength(40)
    expect(long.endsWith('…')).toBe(true)
  })
})

describe('validateProposalsSearch', () => {
  it('defaults to the queue and keeps the two decided tabs', () => {
    expect(validateProposalsSearch({})).toEqual({ stato: 'in_attesa' })
    expect(validateProposalsSearch({ stato: 'accettata' })).toEqual({ stato: 'accettata' })
    expect(validateProposalsSearch({ stato: 'rifiutata' })).toEqual({ stato: 'rifiutata' })
    expect(validateProposalsSearch({ stato: 'boh' })).toEqual({ stato: 'in_attesa' })
    expect(validateProposalsSearch({ stato: 3 })).toEqual({ stato: 'in_attesa' })
  })
})
