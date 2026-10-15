import { describe, expect, it } from 'vitest'
import { purchaseOrderBody, purchaseOrderDraft, validatePurchaseOrder } from './purchaseOrder'
import type { Invoice } from './queries'

const EMPTY_DRAFT = { ordine_numero: '', ordine_data: '', ordine_cig: '', ordine_cup: '' }

describe('purchaseOrder', () => {
  it('starts from the stored order, with empty strings for what is missing', () => {
    const invoice = {
      ordine_numero: 'ODA-14',
      ordine_data: null,
      ordine_cig: 'Z0A1B2C3D4',
      ordine_cup: null,
    } as unknown as Invoice
    expect(purchaseOrderDraft(invoice)).toEqual({
      ordine_numero: 'ODA-14',
      ordine_data: '',
      ordine_cig: 'Z0A1B2C3D4',
      ordine_cup: '',
    })
  })

  it('reads an invoice from an API that predates the columns as no order', () => {
    expect(purchaseOrderDraft({} as unknown as Invoice)).toEqual(EMPTY_DRAFT)
  })

  it('sends every field, trimmed, with null for an empty input', () => {
    expect(
      purchaseOrderBody({
        ordine_numero: ' ODA-14 ',
        ordine_data: '2026-10-09',
        ordine_cig: '',
        ordine_cup: '  ',
      }),
    ).toEqual({ ordine_numero: 'ODA-14', ordine_data: '2026-10-09', ordine_cig: null, ordine_cup: null })
    expect(purchaseOrderBody(EMPTY_DRAFT)).toEqual({
      ordine_numero: null,
      ordine_data: null,
      ordine_cig: null,
      ordine_cup: null,
    })
  })

  it('refuses a date, a CIG or a CUP without the number, and nothing else', () => {
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_numero: ' ', ordine_cig: 'Z0A1B2C3D4' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_data: '2026-10-09' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_cup: 'J41E22000110005' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder(EMPTY_DRAFT)).toBeUndefined()
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_numero: '8', ordine_cig: 'Z0A1B2C3D4' })).toBeUndefined()
  })
})
