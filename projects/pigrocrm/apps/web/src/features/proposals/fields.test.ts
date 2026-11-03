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
  quantity: 1,
  description: 'Sopralluogo al magazzino di Verona',
  approval: {
    channel: 'email',
    sender: 'giulia.bianchi@nordwind.example',
    received_at: '2026-10-21T09:12:00Z',
  },
}

const CONTRACT = {
  contract: {
    customer_id: CUSTOMER_ID,
    title: 'Consulenza fiscale 2027',
    start: '2027-01-01',
    end: '2027-12-31',
    renewal_type: 'tacit',
    renewal_notice_days: 90,
    termination_notice_days: 60,
    payment_days: 30,
    payment_end_of_month: true,
    billing_cadence: 'monthly',
    currency: 'EUR',
    requires_prior_approval: false,
    applies_social_charge: true,
    note: null,
  },
  rate_card: {
    type: 'fixed_recurring',
    unit: 'month',
    amount: '1800.00',
    valid_from: '2027-01-01',
    valid_to: null,
    minimum_hours: null,
    delivery_period: 'monthly',
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
function waiting(target_type: 'contract' | 'work_unit', proposed_fields: Record<string, unknown>) {
  return { target_type, proposed_fields, accepted_fields: null, status: 'pending' }
}

describe('describeProposal, a day', () => {
  const description = describeProposal(waiting('work_unit', DAY))

  it('titles the proposal with its date and quantity and names the contract it is for', () => {
    expect(description.title).toBe('Giornata del 05/11/2026 · 1 gg')
    expect(description.contractId).toBe(CONTRACT_ID)
    expect(description.customerId).toBeNull()
  })

  it('words the day and the approval it rests on', () => {
    expect(rowsOf(description, 'Giornata')).toEqual({
      Data: '05/11/2026',
      Quantità: '1 giorno',
      Description: 'Sopralluogo al magazzino di Verona',
    })
    expect(rowsOf(description, 'Approvazione del cliente')).toEqual({
      Channel: 'Email',
      Sender: 'giulia.bianchi@nordwind.example',
      'Ricevuta il': '21/10/2026',
    })
  })

  it('shows the message the approval came with when there is one', () => {
    const withMessage = describeProposal(
      waiting('work_unit', { ...DAY, approval: { ...DAY.approval, message_id: '<abc@mail>' } }),
    )
    expect(rowsOf(withMessage, 'Approvazione del cliente').Message).toBe('<abc@mail>')
  })

  it('reads a quantity sent as a decimal string and one sent as a JSON number the same way', () => {
    const fromString = describeProposal(waiting('work_unit', { ...DAY, quantity: '0.50' }))
    const fromNumber = describeProposal(waiting('work_unit', { ...DAY, quantity: 0.5 }))
    expect(fromString.title).toBe('Giornata del 05/11/2026 · 0,5 gg')
    expect(fromNumber.title).toBe(fromString.title)
    expect(rowsOf(fromNumber, 'Giornata').Quantità).toBe('0,5 giorni')
  })
})

describe('describeProposal, a contract', () => {
  const description = describeProposal(waiting('contract', CONTRACT))

  it('titles the proposal with the contract and points at its customer', () => {
    expect(description.title).toBe('Consulenza fiscale 2027')
    expect(description.customerId).toBe(CUSTOMER_ID)
    expect(description.contractId).toBeNull()
  })

  it('words the terms in the vocabulary of the contract page', () => {
    expect(rowsOf(description, 'Contratto')).toMatchObject({
      Period: '01/01/2027 – 31/12/2027',
      Renewal: 'Tacito rinnovo',
      'Preavviso di rinnovo': '90 giorni',
      'Preavviso di disdetta': '60 giorni',
      Payment: '30 giorni data fattura fine mese',
      'Approvazione preventiva': 'No',
      'Rivalsa INPS': 'Sì',
    })
  })

  it('reads the whole numbers and flags a REST caller sent as strings', () => {
    const asStrings = describeProposal(
      waiting('contract', {
        ...CONTRACT,
        contract: {
          ...CONTRACT.contract,
          payment_days: '30',
          payment_end_of_month: 'true',
          termination_notice_days: '60',
          requires_prior_approval: 'true',
        },
      }),
    )
    expect(rowsOf(asStrings, 'Contratto')).toMatchObject({
      Payment: '30 giorni data fattura fine mese',
      'Preavviso di disdetta': '60 giorni',
      'Approvazione preventiva': 'Sì',
    })
  })

  it('speaks of one day of notice in the singular', () => {
    const one = describeProposal(
      waiting('contract', { ...CONTRACT, contract: { ...CONTRACT.contract, termination_notice_days: 1 } }),
    )
    expect(rowsOf(one, 'Contratto')['Preavviso di disdetta']).toBe('1 giorno')
  })

  it('words the first rate card with its amount in the contract currency', () => {
    const rate = rowsOf(description, 'Prima scheda tariffaria')
    expect(rate.Type).toBe('Canone ricorrente')
    expect(rate.Amount?.replace(/\s/g, ' ')).toBe('1.800,00 € / mese')
    expect(rate.Validità).toBe('dal 01/01/2027')
  })

  it('does not call a flat fee «per forfait»', () => {
    const flat = describeProposal(
      waiting('contract', { ...CONTRACT, rate_card: { ...CONTRACT.rate_card, type: 'one_off', unit: 'flat' } }),
    )
    expect(rowsOf(flat, 'Prima scheda tariffaria').Amount?.replace(/\s/g, ' ')).toBe('1.800,00 € (forfait)')
  })

  it('shows what the accept would write even when no label exists for it', () => {
    const full = describeProposal(
      waiting('contract', {
        ...CONTRACT,
        contract: {
          ...CONTRACT.contract,
          expense_policy: { kind: 'reimbursable_with_cap', cap: '500.00' },
          custom_fields: { tender_code: 'Z123' },
        },
        rate_card: { ...CONTRACT.rate_card, allowed_fractions: [1, 0.5] },
      }),
    )
    expect(rowsOf(full, 'Contratto')['Politica spese']).toBe('rimborsabile con tetto · tetto: 500.00')
    expect(rowsOf(full, 'Contratto')['Campi personalizzati']).toBe('cig: Z123')
    expect(rowsOf(full, 'Prima scheda tariffaria')['Frazioni ammesse']).toBe('1, 0,5')
  })

  it('says a payment term left to the customer instead of leaving the row out', () => {
    const inherited = describeProposal(
      waiting('contract', {
        ...CONTRACT,
        contract: { ...CONTRACT.contract, payment_days: null, payment_end_of_month: null },
      }),
    )
    expect(rowsOf(inherited, 'Contratto').Payment).toBe('Come da condizioni del cliente')
  })

  it('says an open-ended contract is open-ended instead of dropping the end', () => {
    const open = describeProposal(
      waiting('contract', { ...CONTRACT, contract: { ...CONTRACT.contract, end: null } }),
    )
    expect(rowsOf(open, 'Contratto').Period).toBe('01/01/2027 – a tempo indeterminato')
  })
})

describe('describeProposal once a proposal is decided', () => {
  const corrected = {
    ...CONTRACT,
    contract: { ...CONTRACT.contract, title: 'Consulenza fiscale 2027 (corretto)' },
  }

  it('describes what was accepted, which is what the contract was built from', () => {
    const accepted = describeProposal({
      target_type: 'contract',
      proposed_fields: CONTRACT,
      accepted_fields: corrected,
      status: 'accepted',
    })
    expect(accepted.title).toBe('Consulenza fiscale 2027 (corretto)')
  })

  it('keeps describing what was proposed for a rejected one, which built nothing', () => {
    const rejected = describeProposal({
      target_type: 'contract',
      proposed_fields: CONTRACT,
      accepted_fields: corrected,
      status: 'rejected',
    })
    expect(rejected.title).toBe('Consulenza fiscale 2027')
  })
})

describe('describeProposal on a shape it does not know', () => {
  it('draws no group instead of throwing, and keeps a fallback title', () => {
    expect(describeProposal(waiting('contract', { other: 1 }))).toEqual({
      title: 'Nuovo contratto',
      customerId: null,
      contractId: null,
      // The inherited-payment row is a fact about an absent value, so even an empty
      // contract reads one; every other row needs a value to exist.
      groups: [{ title: 'Contratto', rows: [{ label: 'Pagamento', value: 'Come da condizioni del cliente' }] }],
    })
    expect(describeProposal(waiting('work_unit', {})).title).toBe('Giornata')
  })
})

describe('decisionLine', () => {
  it('says who decided and when', () => {
    expect(
      decisionLine({ status: 'accepted', decided_by: 'Lorenzo Fiore', decided_at: '2026-10-25T10:00:00Z' }),
    ).toMatch(/^Accettata da Lorenzo Fiore · 25 ott 2026/)
  })

  it('does not print a placeholder for what it does not know', () => {
    expect(decisionLine({ status: 'rejected', decided_by: null, decided_at: null })).toBe('Rifiutata')
    expect(decisionLine({ status: 'rejected', decided_by: 'Ada', decided_at: null })).toBe('Rifiutata da Ada')
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
    expect(confidenceBand('0.49')).toBe('low')
    expect(confidenceBand('0.00')).toBe('low')
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
    expect(validateProposalsSearch({})).toEqual({ status: 'pending' })
    expect(validateProposalsSearch({ status: 'accepted' })).toEqual({ status: 'accepted' })
    expect(validateProposalsSearch({ status: 'rejected' })).toEqual({ status: 'rejected' })
    expect(validateProposalsSearch({ status: 'boh' })).toEqual({ status: 'pending' })
    expect(validateProposalsSearch({ status: 3 })).toEqual({ status: 'pending' })
  })
})
