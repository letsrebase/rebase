import { describe, expect, it } from 'vitest'
import type { FieldDefinition } from './schema'
import { asBody, isBlank, toFormValues, toRequestBody, type FormValues } from './formValues'

const FIELDS: FieldDefinition[] = [
  { key: 'name', label: 'Nome', type: 'text', required: true, options: [] },
  { key: 'value', label: 'Valore', type: 'currency', required: false, options: [] },
  { key: 'days', label: 'Giorni', type: 'number', required: false, options: [] },
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
    name: 'Acme',
    value: '10.00',
    custom_fields: { industry: 'PMI', legacy: 'x' },
  }

  it('seeds exactly the listed native keys, never the rest of the row', () => {
    const result = toFormValues(entity, ['name', 'value', 'days'])

    expect(result.native).toEqual({ name: 'Acme', value: '10.00', days: undefined })
    expect(result.native).not.toHaveProperty('id')
    expect(result.native).not.toHaveProperty('created_at')
  })

  it('copies custom_fields whole into its own namespace, detached from the row', () => {
    const result = toFormValues(entity, ['name'])

    expect(result.custom).toEqual({ industry: 'PMI', legacy: 'x' })
    result.custom.industry = 'changed'
    expect(entity.custom_fields.industry).toBe('PMI')
  })

  it('answers an empty custom namespace for an entity without custom_fields', () => {
    expect(toFormValues({}, ['name']).custom).toEqual({})
  })
})

describe('toRequestBody', () => {
  it('sends non-blank natives and always carries custom_fields', () => {
    const body = toRequestBody(values({ name: 'Acme', value: '' }), {
      fields: FIELDS,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body).toEqual({ name: 'Acme', custom_fields: {} })
  })

  it('spells a cleared native column by its type, and says nothing about one that was never set', () => {
    const initial = values({ name: 'Acme', value: '10.00', days: 30 })

    const body = toRequestBody(values({ name: '', value: '', days: null, extra: '' }), {
      fields: FIELDS,
      initial,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body).toEqual({ name: '', value: null, days: null, custom_fields: {} })
  })

  it('clears a custom field only with null, and only when it used to hold a value', () => {
    const initial = values({}, { industry: 'PMI', empty: '' })

    const body = toRequestBody(values({}, { industry: '', empty: '', fatto: false }), {
      fields: FIELDS,
      initial,
      isRenderedCustomKey: rendersEverything,
    })

    expect(body.custom_fields).toEqual({ industry: null, fatto: false })
  })

  it('leaves out a custom value whose definition is no longer rendered', () => {
    const body = toRequestBody(values({}, { archived: 'x', active: 'y' }), {
      fields: FIELDS,
      initial: values({}, { archived: 'x' }),
      isRenderedCustomKey: (key) => key === 'active',
    })

    expect(body.custom_fields).toEqual({ active: 'y' })
  })

  it('honours skipNative and coerceNative', () => {
    const body = toRequestBody(values({ name: 'Acme', days: '30', customer_id: 'c-1' }), {
      fields: FIELDS,
      initial: values({ customer_id: 'c-0' }),
      isRenderedCustomKey: rendersEverything,
      skipNative: (key) => key === 'customer_id',
      coerceNative: (key, value) => (key === 'days' ? Number(value) : value),
    })

    expect(body).toEqual({ name: 'Acme', days: 30, custom_fields: {} })
  })
})

describe('asBody', () => {
  it('hands the same object back, typed as the endpoint body', () => {
    const body = { name: 'Acme' }

    expect(asBody<{ name: string }>(body)).toBe(body)
  })
})
