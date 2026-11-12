import { describe, expect, it } from 'vitest'
import {
  carriesWithholding,
  cleanVatLines,
  defaultPurchaseFormValues,
  fieldsFor,
  isForeignCurrency,
  isZeroRate,
  purchaseToFormValues,
  toPurchaseRequestBody,
  type PurchaseFormValues,
} from './formValues'
import type { Purchase } from './queries'

const KEEP_ALL = () => true

function values(native: Record<string, unknown>): PurchaseFormValues {
  const base = defaultPurchaseFormValues(new Date(2026, 2, 5))
  return { ...base, native: { ...base.native, ...native } }
}

describe('the fields each kind draws', () => {
  const keys = (kind: Parameters<typeof fieldsFor>[0], foreign = false) =>
    fieldsFor(kind, foreign).map((field) => field.key)

  it('gives an invoice and a credit note the document fields and the recap flags', () => {
    for (const kind of ['invoice', 'credit_note'] as const) {
      expect(keys(kind)).toEqual(
        expect.arrayContaining(['number', 'iva_period', 'stamp_duty', 'withholding_rate', 'fund_rate', 'split_payment', 'reverse_charge']),
      )
      expect(keys(kind)).not.toContain('merchant_name')
    }
  })

  it('leaves the withholding block out when the supplier carries none, and keeps the flags', () => {
    const drawn = fieldsFor('invoice', false, { withholding: false }).map((field) => field.key)
    for (const key of ['stamp_duty', 'withholding_rate', 'withholding_base', 'withholding_amount', 'fund_rate', 'fund_amount']) {
      expect(drawn).not.toContain(key)
    }
    expect(drawn).toEqual(expect.arrayContaining(['split_payment', 'reverse_charge', 'iva_period']))
  })

  it('takes away the VAT period, the recap flags and nothing else when the pack allows none', () => {
    const drawn = fieldsFor('invoice', false, {
      vatRecap: false,
      reverseCharge: false,
      splitPayment: false,
    }).map((field) => field.key)
    for (const key of ['iva_period', 'reverse_charge', 'split_payment']) {
      expect(drawn).not.toContain(key)
    }
    expect(drawn).toEqual(expect.arrayContaining(['number', 'amount', 'withholding_rate']))
  })

  it('names the supplier kinds that carry a withholding', () => {
    expect(carriesWithholding('professional')).toBe(true)
    expect(carriesWithholding('occasional')).toBe(true)
    expect(carriesWithholding('company')).toBe(false)
    expect(carriesWithholding(null)).toBe(false)
    expect(carriesWithholding(undefined)).toBe(false)
  })

  it('gives a receipt and an expense the total only', () => {
    for (const kind of ['receipt', 'expense'] as const) {
      expect(keys(kind)).toContain('merchant_name')
      expect(keys(kind)).not.toContain('iva_period')
      expect(keys(kind)).not.toContain('stamp_duty')
      expect(keys(kind)).not.toContain('split_payment')
    }
  })

  it('adds the foreign block only for a foreign currency', () => {
    expect(keys('expense')).not.toContain('exchange_rate')
    expect(keys('expense', true)).toEqual(
      expect.arrayContaining(['currency_amount', 'exchange_rate', 'exchange_rate_date']),
    )
    expect(isForeignCurrency('EUR')).toBe(false)
    expect(isForeignCurrency('')).toBe(false)
    expect(isForeignCurrency(' usd ')).toBe(true)
  })
})

describe('the VAT recap', () => {
  it('reads a zero rate off its digits', () => {
    expect(['0', '0.00', '00', '.0'].map(isZeroRate)).toEqual([true, true, true, true])
    expect(['', '22', '4.00', '0.5', 'x'].map(isZeroRate)).toEqual([false, false, false, false, false])
  })

  it('drops blank rows and sends the nature on a zero rate only', () => {
    expect(
      cleanVatLines([
        { rate: '22', nature: 'N1', taxable_amount: '100', vat_amount: '22', deductible_share: '100' },
        { rate: '0', nature: ' N2.2 ', taxable_amount: '50', vat_amount: '0', deductible_share: '' },
        { rate: '22', nature: '', taxable_amount: '', vat_amount: '', deductible_share: '100' },
      ]),
    ).toEqual([
      { rate: '22', taxable_amount: '100', vat_amount: '22', deductible_share: '100' },
      { rate: '0', nature: 'N2.2', taxable_amount: '50', vat_amount: '0', deductible_share: '100' },
    ])
  })
})

describe('toPurchaseRequestBody', () => {
  it('sends the kind on create and the recap of a document, as typed', () => {
    const body = toPurchaseRequestBody(
      values({
        kind: 'invoice',
        supplier_id: 's1',
        category_id: 'cat1',
        amount: '122.00',
        description: 'Hosting',
        vat_lines: [
          { rate: '22', nature: '', taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100' },
        ],
      }),
      { isRenderedCustomKey: KEEP_ALL },
    )
    expect(body).toMatchObject({
      kind: 'invoice',
      supplier_id: 's1',
      amount: '122.00',
      vat_lines: [{ rate: '22', taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100' }],
    })
    expect(typeof (body.amount as unknown)).toBe('string')
  })

  it('leaves the supplier, the recap and the document fields out of an expense', () => {
    const body = toPurchaseRequestBody(
      values({
        kind: 'expense',
        supplier_id: 's1',
        stamp_duty: '2.00',
        iva_period: '2026-03-01',
        merchant_name: 'Bar Roma',
        vat_lines: [{ rate: '22', nature: '', taxable_amount: '1', vat_amount: '1', deductible_share: '100' }],
      }),
      { isRenderedCustomKey: KEEP_ALL },
    )
    expect(body).toMatchObject({ kind: 'expense', merchant_name: 'Bar Roma' })
    for (const key of ['supplier_id', 'stamp_duty', 'iva_period', 'vat_lines']) {
      expect(body).not.toHaveProperty(key)
    }
  })

  it('does not send a withholding column that was not drawn', () => {
    const body = toPurchaseRequestBody(
      values({ kind: 'invoice', supplier_id: 's1', withholding_rate: '20', stamp_duty: '2' }),
      { isRenderedCustomKey: KEEP_ALL, shown: { withholding: false } },
    )
    expect(body).not.toHaveProperty('withholding_rate')
    expect(body).not.toHaveProperty('stamp_duty')
  })

  it('never sends a part the pack hides: no recap, no period, no flag', () => {
    const body = toPurchaseRequestBody(
      values({
        kind: 'invoice',
        supplier_id: 's1',
        amount: '122.00',
        iva_period: '2026-03-01',
        reverse_charge: true,
        split_payment: true,
        vat_lines: [{ rate: '22', nature: '', taxable_amount: '100', vat_amount: '22', deductible_share: '100' }],
      }),
      {
        isRenderedCustomKey: KEEP_ALL,
        shown: { vatRecap: false, reverseCharge: false, splitPayment: false },
      },
    )
    for (const key of ['vat_lines', 'iva_period', 'reverse_charge', 'split_payment']) {
      expect(body).not.toHaveProperty(key)
    }
    expect(body).toMatchObject({ kind: 'invoice', supplier_id: 's1', amount: '122.00' })
  })

  it('names the credit-note correction only on a credit note', () => {
    const native = { supplier_id: 's1', corrects_purchase_id: 'inv1' }
    expect(
      toPurchaseRequestBody(values({ ...native, kind: 'credit_note' }), { isRenderedCustomKey: KEEP_ALL }),
    ).toMatchObject({ corrects_purchase_id: 'inv1' })
    expect(
      toPurchaseRequestBody(values({ ...native, kind: 'invoice' }), { isRenderedCustomKey: KEEP_ALL }),
    ).not.toHaveProperty('corrects_purchase_id')
  })

  it('sends the foreign block upper-cased for a foreign currency, and none of it in euro', () => {
    const foreign = toPurchaseRequestBody(
      values({ currency: 'usd', currency_amount: '110', exchange_rate: '1.10', exchange_rate_date: '2026-03-05' }),
      { isRenderedCustomKey: KEEP_ALL },
    )
    expect(foreign).toMatchObject({ currency: 'USD', currency_amount: '110', exchange_rate: '1.10', exchange_rate_date: '2026-03-05' })

    const euro = toPurchaseRequestBody(
      values({ currency: 'EUR', currency_amount: '110', exchange_rate: '1.10' }),
      { isRenderedCustomKey: KEEP_ALL },
    )
    expect(euro).not.toHaveProperty('currency_amount')
    expect(euro).not.toHaveProperty('exchange_rate')
  })
})

describe('the deductibility of a purchase (REB-1089)', () => {
  const stated = { deductibility_kind: 'phone', asset_group: 'cars' }

  it('sends the kind and the asset group, and leaves both out when the pack does not read them', () => {
    const on = toPurchaseRequestBody(values({ kind: 'expense', ...stated }), { isRenderedCustomKey: KEEP_ALL })
    expect(on).toMatchObject(stated)
    const off = toPurchaseRequestBody(values({ kind: 'expense', ...stated }), {
      isRenderedCustomKey: KEEP_ALL,
      shown: { deductibility: false },
    })
    expect(off).not.toHaveProperty('deductibility_kind')
    expect(off).not.toHaveProperty('asset_group')
  })

  it('never sends an asset group for a credit note, but sends its kind', () => {
    const body = toPurchaseRequestBody(
      values({ kind: 'credit_note', supplier_id: 's1', ...stated }),
      { isRenderedCustomKey: KEEP_ALL },
    )
    expect(body).toMatchObject({ deductibility_kind: 'phone' })
    expect(body).not.toHaveProperty('asset_group')
  })

  it('clears a stated kind with null on an edit, and sends nothing when both stayed blank', () => {
    const initial = values({ kind: 'expense', ...stated })
    const cleared = toPurchaseRequestBody(
      values({ kind: 'expense', deductibility_kind: null, asset_group: 'cars' }),
      { initial, isRenderedCustomKey: KEEP_ALL },
    )
    expect(cleared).toMatchObject({ deductibility_kind: null })
    expect(cleared).not.toHaveProperty('asset_group')

    const blank = values({ kind: 'expense', deductibility_kind: null, asset_group: null })
    const untouched = toPurchaseRequestBody(blank, { initial: blank, isRenderedCustomKey: KEEP_ALL })
    expect(untouched).not.toHaveProperty('deductibility_kind')
    expect(untouched).not.toHaveProperty('asset_group')
  })
})

describe('editing an existing purchase', () => {
  const PURCHASE = {
    id: 'p1',
    kind: 'invoice',
    status: 'registered',
    deal_id: 'd1',
    legal_entity_id: null,
    category_id: 'cat1',
    supplier_id: 's1',
    merchant_name: null,
    number: 'F-1',
    date: '2026-03-05',
    received_on: null,
    iva_period: null,
    description: 'Hosting',
    amount: '122.00',
    currency: 'EUR',
    currency_amount: null,
    exchange_rate: null,
    exchange_rate_date: null,
    cost_amount: '100.00',
    vat_lines: [
      { rate: '22.00', nature: null, taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100.00', position: 0 },
    ],
    stamp_duty: null,
    withholding_rate: null,
    withholding_base: null,
    withholding_amount: null,
    fund_rate: null,
    fund_amount: null,
    split_payment: false,
    reverse_charge: false,
    corrects_purchase_id: null,
    custom_fields: {},
  } as unknown as Purchase

  it('seeds the form from the row without the keys the update refuses', () => {
    const seeded = purchaseToFormValues(PURCHASE)
    expect(seeded.native).not.toHaveProperty('id')
    expect(seeded.native).not.toHaveProperty('status')
    expect(seeded.native).not.toHaveProperty('deal_id')
    expect(seeded.native).not.toHaveProperty('cost_amount')
    expect(seeded.native.vat_lines).toEqual([
      { rate: '22.00', nature: '', taxable_amount: '100.00', vat_amount: '22.00', deductible_share: '100.00' },
    ])
  })

  it('sends neither the kind nor an unchanged recap, and the edited column', () => {
    const initial = purchaseToFormValues(PURCHASE)
    const edited = { ...initial, native: { ...initial.native, description: 'Hosting 2026' } }
    const body = toPurchaseRequestBody(edited, { initial, isRenderedCustomKey: KEEP_ALL })
    expect(body).toMatchObject({ description: 'Hosting 2026' })
    // What was not touched is not echoed: the server would read it as a possible move.
    expect(body).not.toHaveProperty('supplier_id')
    expect(body).not.toHaveProperty('amount')
    expect(body).not.toHaveProperty('category_id')
    expect(body).not.toHaveProperty('kind')
    expect(body).not.toHaveProperty('vat_lines')
  })

  it('sends nothing but an empty custom_fields for an edit that changed nothing', () => {
    const initial = purchaseToFormValues(PURCHASE)
    expect(toPurchaseRequestBody(initial, { initial, isRenderedCustomKey: KEEP_ALL })).toEqual({
      custom_fields: {},
    })
  })

  it('keeps the currency and its foreign block together when one of them moves', () => {
    const initial = purchaseToFormValues({
      ...PURCHASE,
      currency: 'USD',
      currency_amount: '110.00',
      exchange_rate: '1.100000',
      exchange_rate_date: '2026-03-05',
    })
    const body = toPurchaseRequestBody(
      { ...initial, native: { ...initial.native, currency_amount: '120.00' } },
      { initial, isRenderedCustomKey: KEEP_ALL },
    )
    expect(body).toMatchObject({
      currency: 'USD',
      currency_amount: '120.00',
      exchange_rate: '1.100000',
      exchange_rate_date: '2026-03-05',
    })
  })

  it('clears the foreign block of a purchase that moves back to euro', () => {
    const initial = purchaseToFormValues({
      ...PURCHASE,
      currency: 'USD',
      currency_amount: '110.00',
      exchange_rate: '1.100000',
      exchange_rate_date: '2026-03-05',
    })
    const body = toPurchaseRequestBody(
      { ...initial, native: { ...initial.native, currency: 'EUR' } },
      { initial, isRenderedCustomKey: KEEP_ALL },
    )
    expect(body).toMatchObject({
      currency: 'EUR',
      currency_amount: null,
      exchange_rate: null,
      exchange_rate_date: null,
    })
  })

  it('replaces the recap when a row changed', () => {
    const initial = purchaseToFormValues(PURCHASE)
    const lines = [{ ...(initial.native.vat_lines as object[])[0]!, vat_amount: '21.00' }]
    const body = toPurchaseRequestBody(
      { ...initial, native: { ...initial.native, vat_lines: lines } },
      { initial, isRenderedCustomKey: KEEP_ALL },
    )
    expect(body.vat_lines).toEqual([
      { rate: '22.00', taxable_amount: '100.00', vat_amount: '21.00', deductible_share: '100.00' },
    ])
  })
})
