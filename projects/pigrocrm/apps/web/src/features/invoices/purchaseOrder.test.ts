import { describe, expect, it } from 'vitest'
import { purchaseOrderChanges, purchaseOrderDraft, validatePurchaseOrder } from './purchaseOrder'
import type { Invoice } from './queries'

const EMPTY_DRAFT = { purchase_order_number: '', purchase_order_date: '', purchase_order_tender_code: '', purchase_order_project_code: '' }

describe('purchaseOrder', () => {
  it('starts from the stored order, with empty strings for what is missing', () => {
    const invoice = {
      purchase_order_number: 'ODA-14',
      purchase_order_date: null,
      purchase_order_tender_code: 'Z0A1B2C3D4',
      purchase_order_project_code: null,
    } as unknown as Invoice
    expect(purchaseOrderDraft(invoice)).toEqual({
      purchase_order_number: 'ODA-14',
      purchase_order_date: '',
      purchase_order_tender_code: 'Z0A1B2C3D4',
      purchase_order_project_code: '',
    })
  })

  it('reads an invoice from an API that predates the columns as no order', () => {
    expect(purchaseOrderDraft({} as unknown as Invoice)).toEqual(EMPTY_DRAFT)
  })

  it('sends only the touched fields, trimmed, with null for an empty input', () => {
    const draft = {
      purchase_order_number: ' ODA-14 ',
      purchase_order_date: '2026-10-09',
      purchase_order_tender_code: '',
      purchase_order_project_code: '  ',
    }
    expect(purchaseOrderChanges(draft, new Set(['purchase_order_number', 'purchase_order_project_code']))).toEqual({
      purchase_order_number: 'ODA-14',
      purchase_order_project_code: null,
    })
    expect(purchaseOrderChanges(draft, new Set(['purchase_order_date', 'purchase_order_tender_code']))).toEqual({
      purchase_order_date: '2026-10-09',
      purchase_order_tender_code: null,
    })
    expect(purchaseOrderChanges(EMPTY_DRAFT, new Set())).toEqual({})
    expect(purchaseOrderChanges(EMPTY_DRAFT, new Set(['purchase_order_date']))).toEqual({ purchase_order_date: null })
  })

  it('refuses a date, a CIG or a CUP without the number, and nothing else', () => {
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, purchase_order_number: ' ', purchase_order_tender_code: 'Z0A1B2C3D4' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, purchase_order_date: '2026-10-09' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, purchase_order_project_code: 'J41E22000110005' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder(EMPTY_DRAFT)).toBeUndefined()
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, purchase_order_number: '8', purchase_order_tender_code: 'Z0A1B2C3D4' })).toBeUndefined()
  })
})
