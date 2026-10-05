import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { CustomerForm, customerToFormValues } from './CustomerForm'
import type { Customer } from './queries'
import type { FieldDefinition } from '@/lib/schema'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

import { api } from '@/lib/api'

const GET = api.GET as unknown as Mock

const BASE_CUSTOMER: Customer = {
  id: 'c1',
  azienda_id: 'a-1',
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


/** The form mounts a query (the azienda proposal, disabled with one azienda), so every
 *  render needs a client even where no request is ever made. */
function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('CustomerForm', () => {
  it('round-trips a value for a field the active schema still defines', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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
    renderWithClient(
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

describe('the «Azienda» picker (REB-626)', () => {
  const HUMANCRAFT = { id: 'a-1', nome: 'humancraft', attiva: true } as LegalEntityRecord
  const REBASE = { id: 'a-2', nome: 'rebase ltd', attiva: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    aziende: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  /** `GET /api/aziende/proposta` answers the rule the server applies: GB has its own
   *  azienda, everything else gets the default. */
  function serveProposal() {
    GET.mockImplementation((path: string, options: { params: { query: { nazione: string } } }) => {
      if (path !== '/api/aziende/proposta') throw new Error(`unexpected GET ${path}`)
      const proposed = options.params.query.nazione === 'GB' ? REBASE : HUMANCRAFT
      return Promise.resolve({ data: proposed, response: { status: 200 } })
    })
  }

  function renderWithAziende(ui: ReactElement, value: LegalEntityValue = TWO) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={value}>{ui}</LegalEntityContext>
      </QueryClientProvider>,
    )
  }

  beforeEach(() => {
    GET.mockReset()
    serveProposal()
  })

  it('shows the azienda the nation proposes, follows the nation, and sends what it shows', async () => {
    const onSubmit = vi.fn()
    renderWithAziende(
      <CustomerForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    await waitFor(() => expect(picker).toHaveTextContent('humancraft'))
    expect(screen.getByText(/Proposta dalla nazione/)).toBeInTheDocument()

    const nazione = screen.getByLabelText('Nazione')
    await userEvent.clear(nazione)
    await userEvent.type(nazione, 'GB')
    await waitFor(() => expect(picker).toHaveTextContent('rebase ltd'))

    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Overseas Ltd')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ ragione_sociale: 'Overseas Ltd', nazione: 'GB', azienda_id: 'a-2' })
  })

  it('keeps a hand-picked azienda whatever the nation says afterwards', async () => {
    const onSubmit = vi.fn()
    renderWithAziende(
      <CustomerForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    await waitFor(() => expect(picker).toHaveTextContent('humancraft'))
    await userEvent.click(picker)
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    expect(screen.queryByText(/Proposta dalla nazione/)).not.toBeInTheDocument()

    const nazione = screen.getByLabelText('Nazione')
    await userEvent.clear(nazione)
    await userEvent.type(nazione, 'FR')
    expect(picker).toHaveTextContent('rebase ltd')
    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Choisie')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ nazione: 'FR', azienda_id: 'a-2' })
  })

  it('while editing shows the stored azienda, asks for no proposal, and sends it only when changed', async () => {
    const onSubmit = vi.fn()
    renderWithAziende(
      <CustomerForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        initial={customerToFormValues(BASE_CUSTOMER)}
        onSubmit={onSubmit}
      />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    expect(picker).toHaveTextContent('humancraft')
    expect(GET).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('azienda_id')

    onSubmit.mockReset()
    await userEvent.click(picker)
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ azienda_id: 'a-2' })
  })

  it('is not drawn in a one-azienda space, and the body carries no azienda', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <CustomerForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Sola')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('azienda_id')
    expect(GET).not.toHaveBeenCalled()
  })
})

describe('a customer stranded on a deactivated azienda (REB-626, Greptile on PR #509)', () => {
  const HUMANCRAFT = { id: 'a-1', nome: 'humancraft', attiva: true } as LegalEntityRecord
  const ONE_LEFT: LegalEntityValue = {
    aziende: [HUMANCRAFT],
    selected: null,
    select: vi.fn(),
    several: false, scoped: false, pinned: false,
    byId: (id) => (id === 'a-1' ? HUMANCRAFT : undefined),
  }

  it('still offers the active aziende while editing, so the customer can move', async () => {
    GET.mockReset()
    const onSubmit = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={ONE_LEFT}>
          <CustomerForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[]}
            initial={customerToFormValues({ ...BASE_CUSTOMER, azienda_id: 'a-gone' })}
            onSubmit={onSubmit}
          />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    expect(screen.getByText(/non è più attiva/)).toBeInTheDocument()
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    await userEvent.click(picker)
    await userEvent.click(await screen.findByRole('option', { name: 'humancraft' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ azienda_id: 'a-1' })
    expect(GET).not.toHaveBeenCalled()
  })

  it('draws nothing for a customer of the one active azienda', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={ONE_LEFT}>
          <CustomerForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[]}
            initial={customerToFormValues(BASE_CUSTOMER)}
            onSubmit={vi.fn()}
          />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })
})

describe('the picker beside a custom field that happens to be named azienda_id (CodeRabbit, PR #509)', () => {
  const HUMANCRAFT = { id: 'a-1', nome: 'humancraft', attiva: true } as LegalEntityRecord
  const REBASE = { id: 'a-2', nome: 'rebase ltd', attiva: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    aziende: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }
  const HOMONYM: FieldDefinition = {
    key: 'azienda_id',
    label: 'Codice azienda interno',
    type: 'text',
    required: false,
    options: [],
  }

  it('sends the chosen azienda as the native column, never inside custom_fields', async () => {
    GET.mockReset()
    const onSubmit = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={TWO}>
          <CustomerForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[HOMONYM]}
            initial={customerToFormValues(BASE_CUSTOMER)}
            onSubmit={onSubmit}
          />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getByRole('combobox', { name: 'Azienda' }))
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const body = submitted(onSubmit)
    expect(body.azienda_id).toBe('a-2')
    expect(body.custom_fields).toEqual({})
    // And the homonymous custom control never shows the picked id as its own value.
    expect(screen.getByLabelText('Codice azienda interno')).toHaveValue('')
  })
})
