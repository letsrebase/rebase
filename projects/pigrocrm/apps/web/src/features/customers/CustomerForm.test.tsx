import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CustomerForm, customerToFormValues } from './CustomerForm'
import type { Customer } from './queries'
import type { FieldDefinition } from '@/lib/schema'

const BASE_CUSTOMER: Customer = {
  id: 'c1',
  ragione_sociale: 'ACME Srl',
  partita_iva: null,
  codice_fiscale: null,
  codice_sdi: null,
  pec: null,
  indirizzo: null,
  cap: null,
  comune: null,
  provincia: null,
  nazione: 'IT',
  email: null,
  telefono: null,
  sito_web: null,
  stato: null,
  note: null,
  giorni_pagamento: null,
  pagamento_fine_mese: false,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

const SETTORE: FieldDefinition = {
  key: 'settore',
  label: 'Settore',
  type: 'select',
  required: false,
  options: ['PMI', 'Enterprise'],
}

const VIP: FieldDefinition = {
  key: 'vip',
  label: 'Cliente VIP',
  type: 'checkbox',
  required: false,
  options: [],
}

function submitted(onSubmit: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const [first] = onSubmit.mock.calls
  if (!first) throw new Error('onSubmit was never called')
  return first[0] as Record<string, unknown>
}

describe('CustomerForm', () => {
  it('round-trips a value for a field the active schema still defines', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[SETTORE]}
        initial={customerToFormValues({ ...BASE_CUSTOMER, custom_fields: { settore: 'PMI' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      ragione_sociale: 'ACME Srl',
      nazione: 'IT',
      pagamento_fine_mese: false,
      custom_fields: { settore: 'PMI' },
    })
  })

  /**
   * The defect this guards, reproduced live before the fix: archive a field
   * definition while a record still carries a value for it, open Modifica, press
   * Salva with nothing else changed. A flat form state has to re-derive "is this key
   * custom?" from the *active* schema, which no longer lists the archived key, so the
   * value was reclassified as a native column and sent as a top-level key --
   * `CustomerUpdate` is `extra="forbid"`, so the PATCH came back 422
   * (`extra_forbidden`, confirmed against the running API) and the record was
   * uneditable from then on.
   *
   * Sending it back inside `custom_fields` instead is not the fix either: that 422s
   * too, with "campo non definito" -- `validate_custom_fields` refuses any key that is
   * not an active definition. Omitting it is what the server wants (verified: 200, and
   * the stored value is still there afterwards), and the value has to stay in the
   * custom namespace for that to be possible at all.
   */
  it('neither sends an archived field as a native column nor drops its stored value', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        // The definition was archived: `GET /api/schema/customer` no longer lists it,
        // so nothing renders it -- but the record still carries the value.
        customFields={[]}
        initial={customerToFormValues({ ...BASE_CUSTOMER, custom_fields: { settore: 'PMI' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    // Not a top-level key: that is the 422 this defect actually was.
    expect(payload).not.toHaveProperty('settore')
    // Not `{settore: null}` either: that would delete the stored value instead of
    // leaving it alone. Omitted is the only spelling that preserves it.
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      ragione_sociale: 'ACME Srl',
      nazione: 'IT',
      pagamento_fine_mese: false,
      custom_fields: {},
    })
  })

  it('sends an untouched checkbox as false on create, never as an absent key', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Nuovo cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    // `false`, not an omitted key: the record then reads "No", which is what the
    // unchecked box the user was looking at actually said. Omitting it stores nothing
    // and the detail page renders a dash, as if nobody had an opinion.
    // `pagamento_fine_mese` is the one native checkbox (REB-326) and follows the same
    // rule: the unchecked box the user saw said "no", so "no" is what is sent.
    expect(submitted(onSubmit)).toEqual({
      nazione: 'IT',
      pagamento_fine_mese: false,
      custom_fields: { vip: false },
    })
  })

  /**
   * The defect the create-side seeding introduced, reproduced live before this fix:
   * a record with `custom_fields: {}` was edited to change only Telefono, and the
   * save persisted `vip: false` -- a value the user never chose, written as a side
   * effect of touching an unrelated field. Untouched native columns are never
   * rewritten; an untouched checkbox must not be either. The meaning is carried by
   * `renderFieldValue` reading an absent checkbox as "No" instead.
   */
  it('does not backfill an untouched checkbox on edit, whatever else changed', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={customerToFormValues(BASE_CUSTOMER)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText('Telefono'), '02123456')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      ragione_sociale: 'ACME Srl',
      nazione: 'IT',
      telefono: '02123456',
      // A stored native value, like the two above it: sent as it is, not backfilled.
      pagamento_fine_mese: false,
      custom_fields: {},
    })
  })

  it('sends false for a checkbox the user actually turns off on edit', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={customerToFormValues({ ...BASE_CUSTOMER, custom_fields: { vip: true } })}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByLabelText('Cliente VIP')).toBeChecked()
    await userEvent.click(screen.getByLabelText('Cliente VIP'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    // `false`, not `null`: unchecking is choosing "no", not clearing the field.
    // `isBlank` treats `false` as a value, so this never takes the clear path.
    expect(submitted(onSubmit).custom_fields).toEqual({ vip: false })
  })

  it('sends false for a checkbox toggled on and back off from absent on edit', async () => {
    const onSubmit = vi.fn()
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={customerToFormValues(BASE_CUSTOMER)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByLabelText('Cliente VIP'))
    await userEvent.click(screen.getByLabelText('Cliente VIP'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit).custom_fields).toEqual({ vip: false })
  })

  it('clears a custom field the user emptied with null, and a native one with an empty string', async () => {
    const onSubmit = vi.fn()
    const initial = customerToFormValues({
      ...BASE_CUSTOMER,
      telefono: '02123456',
      custom_fields: { settore: 'PMI' },
    })
    render(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[SETTORE]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText('Telefono'))
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      ragione_sociale: 'ACME Srl',
      nazione: 'IT',
      telefono: '',
      pagamento_fine_mese: false,
      custom_fields: { settore: null },
    })
  })
})
