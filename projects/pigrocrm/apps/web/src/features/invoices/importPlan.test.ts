import { describe, expect, it } from 'vitest'
import {
  batchFiles,
  countWrites,
  customersOf,
  decisionsFor,
  newParties,
  registerYears,
  sortReviewRows,
  willBeWritten,
  type ConfirmRow,
  type ParsedInvoice,
  type ReviewOutcome,
  type ReviewRow,
} from './importPlan'

function invoice(overrides: Partial<ParsedInvoice['customer']> = {}): ParsedInvoice {
  return {
    number: '2026/6',
    issue_date: '2026-03-15',
    sdi_document_type: 'invoice',
    currency: 'EUR',
    supplier: party({ legal_name: 'Chiara Bianchi', vat_number: 'IT01234567890' }),
    customer: party(overrides),
    lines: [],
    recaps: [],
    taxable_amount: '1000.00',
    vat_amount: '0.00',
    total: '1000.00',
    social_security_pension_fund: [],
    termini_payment: [],
    transmission: {} as ParsedInvoice['transmission'],
  }
}

function party(overrides: Partial<ParsedInvoice['customer']> = {}): ParsedInvoice['customer'] {
  return {
    legal_name: 'Esempio Servizi S.r.l.',
    vat_number: 'IT09876543210',
    address: 'Piazza dei Modelli 4',
    cap: '00187',
    city: 'Roma',
    country: 'IT',
    ...overrides,
  }
}

function row(outcome: ReviewOutcome, sha = 'a', overrides: Partial<ReviewRow> = {}): ReviewRow {
  return {
    file: `${sha}.xml`,
    sha256: sha,
    outcome,
    invoice: outcome === 'unclaimed' ? null : invoice(),
    party_key: outcome === 'unclaimed' ? null : '09876543210',
    ...overrides,
  }
}

const NONE = new Set<string>()

describe('the order of a review', () => {
  it('puts what needs a look first and keeps the file order inside each verdict', () => {
    const sorted = sortReviewRows([
      row('ready', '1'),
      row('already_present', '2'),
      row('ready', '3'),
      row('conflict', '4'),
      row('needs_customer_confirmation', '5'),
      row('unclaimed', '6'),
      row('incoming_skipped', '7'),
    ])

    expect(sorted.map((r) => r.sha256)).toEqual(['4', '6', '5', '7', '2', '1', '3'])
  })
})

describe('the new customers of a review', () => {
  it('are one party for every invoice that names them, by the key the server gave', () => {
    const rows = [
      row('needs_customer_confirmation', '1'),
      row('needs_customer_confirmation', '2'),
      row('needs_customer_confirmation', '3', {
        party_key: '11111111111',
        legal_entity_id: 'az-2',
        invoice: invoice({ legal_name: 'Altro S.r.l.', vat_number: 'IT11111111111' }),
      }),
      row('ready', '4'),
    ]

    // `legalEntityId` is the azienda the party's invoices are written on, which bounds the
    // customers it may be attached to.
    expect(newParties(rows)).toEqual([
      {
        key: '09876543210',
        name: 'Esempio Servizi S.r.l.',
        taxId: 'IT09876543210',
        invoices: 2,
        legalEntityId: null,
        mixedLegalEntities: false,
      },
      {
        key: '11111111111',
        name: 'Altro S.r.l.',
        taxId: 'IT11111111111',
        invoices: 1,
        legalEntityId: 'az-2',
        mixedLegalEntities: false,
      },
    ])
  })

  it('flags a party whose invoices are written on more than one azienda', () => {
    const rows = [
      row('needs_customer_confirmation', '1', { legal_entity_id: 'az-1' }),
      row('needs_customer_confirmation', '2', { legal_entity_id: 'az-2' }),
    ]

    expect(newParties(rows)).toMatchObject([{ invoices: 2, legalEntityId: 'az-1', mixedLegalEntities: true }])
  })
})

describe('what a confirmation would write', () => {
  const rows = [
    row('ready', '1'),
    row('needs_customer_confirmation', '2'),
    row('already_present', '3'),
    row('conflict', '4'),
    row('incoming_skipped', '5'),
    row('unclaimed', '6'),
  ]

  it('is the ready invoices and the new customers left ticked, and nothing else', () => {
    expect(rows.filter((r) => willBeWritten(r, NONE)).map((r) => r.sha256)).toEqual(['1', '2'])
    expect(countWrites(rows, NONE)).toBe(2)
  })

  it('leaves out the invoices of a customer the person unticked', () => {
    const declined = new Set(['09876543210'])

    expect(countWrites(rows, declined)).toBe(1)
    expect(willBeWritten(rows[1]!, declined)).toBe(false)
    expect(willBeWritten(rows[0]!, declined)).toBe(true)
  })

  it('sends one decision per new customer: create for one ticked, an explicit skip for one unticked', () => {
    expect(decisionsFor(rows, NONE)).toEqual({ customers: { '09876543210': { create_customer: true } } })
    expect(decisionsFor(rows, new Set(['09876543210']))).toEqual({
      customers: { '09876543210': { skip: true } },
    })
  })

  it('attaches by id a customer an earlier batch already created, instead of creating it again', () => {
    const created = new Map([['09876543210', 'customer-1']])

    expect(decisionsFor(rows, NONE, created)).toEqual({
      customers: { '09876543210': { customer_id: 'customer-1' } },
    })
  })

  it('attaches by id the customer a person picked from the list, instead of creating one', () => {
    const chosen = new Map([['09876543210', { id: 'customer-9', name: 'Acme Ltd' }]])

    expect(decisionsFor(rows, NONE, new Map(), chosen)).toEqual({
      customers: { '09876543210': { customer_id: 'customer-9' } },
    })
  })

  it('lets an unticked customer win over a picked one, and an earlier batch over a pick', () => {
    const chosen = new Map([['09876543210', { id: 'customer-9', name: 'Acme Ltd' }]])
    const created = new Map([['09876543210', 'customer-1']])

    expect(decisionsFor(rows, new Set(['09876543210']), new Map(), chosen)).toEqual({
      customers: { '09876543210': { skip: true } },
    })
    expect(decisionsFor(rows, NONE, created, chosen)).toEqual({
      customers: { '09876543210': { customer_id: 'customer-1' } },
    })
  })

  it('names two customers of one file separately, so unticking one leaves the other', () => {
    const two = [
      row('needs_customer_confirmation', '1', { party_key: 'AAA' }),
      row('needs_customer_confirmation', '1', { party_key: 'BBB' }),
    ]

    expect(decisionsFor(two, new Set(['AAA']))).toEqual({
      customers: { AAA: { skip: true }, BBB: { create_customer: true } },
    })
    expect(countWrites(two, new Set(['AAA']))).toBe(1)
  })
})

describe('the customers an import created', () => {
  it('are read from the confirmed rows, per party', () => {
    const rows = [
      { file: 'a', sha256: 'a', outcome: 'imported', party_key: 'AAA', invoice: { customer_id: 'c1' } },
      { file: 'b', sha256: 'b', outcome: 'conflict', party_key: 'BBB' },
    ] as ConfirmRow[]

    expect([...customersOf(rows)]).toEqual([['AAA', 'c1']])
  })
})

describe('the batches a year of files is cut into', () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name)

  it('holds at most the file count, in order', () => {
    const files = Array.from({ length: 7 }, (_, i) => file(`${i}.xml`, 10))

    expect(batchFiles(files, 3, 1000).map((b) => b.map((f) => f.name))).toEqual([
      ['0.xml', '1.xml', '2.xml'],
      ['3.xml', '4.xml', '5.xml'],
      ['6.xml'],
    ])
  })

  it('closes a batch before it passes the byte ceiling, and never splits one file', () => {
    const files = [file('a.xml', 600), file('b.zip', 5000), file('c.xml', 300), file('d.xml', 300)]

    expect(batchFiles(files, 25, 1000).map((b) => b.map((f) => f.name))).toEqual([
      ['a.xml'],
      ['b.zip'],
      ['c.xml', 'd.xml'],
    ])
  })

  it('is no batches for no files', () => {
    expect(batchFiles([])).toEqual([])
  })
})

describe('the register after an import', () => {
  function imported(year: number, number: number, missing: number[], legal_entity = 'az-1'): ConfirmRow {
    return {
      file: `${number}.xml`,
      sha256: String(number),
      outcome: 'imported',
      legal_entity_id: legal_entity,
      invoice: { year, number } as ConfirmRow['invoice'],
      undeclared_gaps: missing,
    }
  }

  it('is what the last invoice written into each year reports, oldest year first', () => {
    const years = registerYears([
      imported(2026, 1, [2, 3]),
      imported(2025, 9, [1]),
      imported(2026, 2, [3]),
      { file: 'x', sha256: 'x', outcome: 'already_present', legal_entity_id: 'az-1' },
    ])

    expect(years).toEqual([
      { legalEntityId: 'az-1', year: 2025, missing: [1] },
      { legalEntityId: 'az-1', year: 2026, missing: [3] },
    ])
  })

  it('keeps two aziende of the same year apart', () => {
    const years = registerYears([imported(2026, 1, [], 'az-1'), imported(2026, 1, [4], 'az-2')])

    expect(years.map((y) => [y.legalEntityId, y.missing])).toEqual([
      ['az-1', []],
      ['az-2', [4]],
    ])
  })
})
