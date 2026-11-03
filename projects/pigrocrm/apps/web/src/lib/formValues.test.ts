import { describe, expect, it } from 'vitest'
import type { FieldDefinition } from './schema'
import { asBody, isBlank, toFormValues, toRequestBody, type FormValues } from './formValues'

const FIELDS: FieldDefinition[] = [
  { key: 'nome', label: 'Nome', type: 'text', required: true, options: [] },
  { key: 'valore', label: 'Valore', type: 'currency', required: false, options: [] },
  { key: 'giorni', label: 'Giorni', type: 'number', required: false, options: [] },
]

function values(native: Record<string, unknown>, custom: Record<string, unknown> = {}): FormValues {
  return { native, custom }
}

const rendersEverything = () => true

describe('isBlank', () => {
  it('treats null, undefined, whitespace and an empty array as no value', () => {
    for (const blank of [null, undefined, '', '   ', []]) expect(isBlank(blank)).toBe(true)
  })

  it('treats false, 0 and a non-empty array as real values', () => {
    for (const real of [false, 0, 'x', ['a']]) expect(isBlank(real)).toBe(false)
  })
})

describe('toFormValues', () => {
  const entity = {
    id: 'e-1',
    created_at: '2026-01-01',
    nome: 'Acme',
    valore: '10.00',
    custom_fields: { settore: 'PMI', vecchio: 'x' },
  }

  it('seeds exactly the listed native keys, never the rest of the row', () => {
    const result = toFormValues(entity, ['nome', 'valore', 'giorni'])

    expect(result.native).toEqual({ nome: 'Acme', valore: '10.00', giorni: undefined })
    expect(result.native).not.toHaveProperty('id')
    expect(result.native).not.toHaveProperty('created_at')
  })

  it('copies custom_fields whole into its own namespace, detached from the row', () => {
    const result = toFormValues(entity, ['nome'])

    expect(result.custom).toEqual({ settore: 'PMI', vecchio: 'x' })
    result.custom.settore = 'changed'
    expect(entity.custom_fields.settore).toBe('PMI')
  })

  it('answers an empty custom namespace for an entity without custom_fields', () => {
    expect(toFormValues({}, ['nome']).custom).toEqual({})
  })
})

describe('toRequestBody', () => {
  it('sends non-blank natives and always carries custom_fields', () => {
    const body = toRequestBody(values({ nome: 'Acme', valore: '' }), {
      fields: FIELDS,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body).toEqual({ nome: 'Acme', custom_fields: {} })
  })

  it('spells a cleared native column by its type, and says nothing about one that was never set', () => {
    const initial = values({ nome: 'Acme', valore: '10.00', giorni: 30 })

    const body = toRequestBody(values({ nome: '', valore: '', giorni: null, extra: '' }), {
      fields: FIELDS,
      initial,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body).toEqual({ nome: '', valore: null, giorni: null, custom_fields: {} })
  })

  it('clears a custom field only with null, and only when it used to hold a value', () => {
    const initial = values({}, { settore: 'PMI', vuoto: '' })

    const body = toRequestBody(values({}, { settore: '', vuoto: '', fatto: false }), {
      fields: FIELDS,
      initial,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body.custom_fields).toEqual({ settore: null, fatto: false })
  })

  it('leaves out a custom value whose definition is no longer rendered', () => {
    const body = toRequestBody(values({}, { archiviato: 'x', attivo: 'y' }), {
      fields: FIELDS,
      initial: values({}, { archiviato: 'x' }),
      isRenderedCustomKey: (key) => key === 'attivo',
    })

    expect(body.custom_fields).toEqual({ attivo: 'y' })
  })

  it('honours skipNative and coerceNative', () => {
    const body = toRequestBody(values({ nome: 'Acme', giorni: '30', customer_id: 'c-1' }), {
      fields: FIELDS,
      initial: values({ customer_id: 'c-0' }),
      isRenderedCustomKey: rendersEverything,
      skipNative: (key) => key === 'customer_id',
      coerceNative: (key, value) => (key === 'giorni' ? Number(value) : value),
    })

    expect(body).toEqual({ nome: 'Acme', giorni: 30, custom_fields: {} })
  })
})

describe('asBody', () => {
  it('hands the same object back, typed as the endpoint body', () => {
    const body = { nome: 'Acme' }

    expect(asBody<{ nome: string }>(body)).toBe(body)
  })
})
