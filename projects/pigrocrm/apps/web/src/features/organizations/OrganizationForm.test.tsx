import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState, type ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { OrganizationForm, organizationToFormValues } from './OrganizationForm'
import type { Organization } from './queries'
import type { FieldDefinition } from '@/lib/schema'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PATCH: vi.fn() } }
})

import { api } from '@/lib/api'

const GET = api.GET as unknown as Mock
const PATCH = api.PATCH as unknown as Mock

const BASE_CUSTOMER: Organization = {
  id: 'c1',
  legal_entity_id: 'a-1',
  owner_id: null,
  legal_name: 'ACME Srl',
  vat_number: null,
  tax_code: null,
  sdi_code: null,
  certified_email: null,
  address: null,
  postal_code: null,
  city: null,
  province: null,
  country: 'IT',
  email: null,
  phone: null,
  contact_points: [],
  website: null,
  status: null,
  payment_days: null,
  payment_end_of_month: false,
  supplier_payment_days: null,
  supplier_payment_end_of_month: false,
  roles: ['customer'],
  supplier_kind: null,
  supplier_area: null,
  supplier_supplies: null,
  customer_kind: null,
  is_withholding_agent: false,
  hub_freelancer_id: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

const INDUSTRY: FieldDefinition = {
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
function renderWithClient(ui: ReactElement, off: readonly ModuleKey[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // The form asks which modules are on (the supplier role needs `purchases`): answered up front,
  // so a test about what the form requests sees only its own requests.
  seedModules(client, off)
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('OrganizationForm', () => {
  it('round-trips a value for a field the active schema still defines', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[INDUSTRY]}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, custom_fields: { 'settore': 'PMI' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      legal_name: 'ACME Srl',
      country: 'IT',
      payment_end_of_month: false,
      custom_fields: { 'settore': 'PMI' },
    })
  })

  /**
   * The defect this guards, reproduced live before the fix: archive a field
   * definition while a record still carries a value for it, open Edit, press
   * Save with nothing else changed. A flat form state has to re-derive "is this key
   * custom?" from the *active* schema, which no longer lists the archived key, so the
   * value was reclassified as a native column and sent as a top-level key --
   * `OrganizationUpdate` is `extra="forbid"`, so the PATCH came back 422
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
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        // The definition was archived: `GET /api/schema/organization` no longer lists it,
        // so nothing renders it -- but the record still carries the value.
        customFields={[]}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, custom_fields: { 'settore': 'PMI' } })}
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
      legal_name: 'ACME Srl',
      country: 'IT',
      payment_end_of_month: false,
      custom_fields: {},
    })
  })

  it('sends an untouched checkbox as false on create, never as an absent key', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
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
    // `payment_end_of_month` is the one native checkbox (REB-326) and follows the same
    // rule: the unchecked box the user saw said "no", so "no" is what is sent.
    expect(submitted(onSubmit)).toEqual({
      country: 'IT',
      roles: ['customer'],
      payment_end_of_month: false,
      // The customer's own checkbox, drawn on a new customer, answers like the other one.
      is_withholding_agent: false,
      custom_fields: { vip: false },
    })
  })

  /**
   * The defect the create-side seeding introduced, reproduced live before this fix:
   * a record with `custom_fields: {}` was edited to change only Comune, and the
   * save persisted `vip: false` -- a value the user never chose, written as a side
   * effect of touching an unrelated field. Untouched native columns are never
   * rewritten; an untouched checkbox must not be either. The meaning is carried by
   * `renderFieldValue` reading an absent checkbox as "No" instead.
   */
  it('does not backfill an untouched checkbox on edit, whatever else changed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={organizationToFormValues(BASE_CUSTOMER)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText('Comune'), 'Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      legal_name: 'ACME Srl',
      country: 'IT',
      city: 'Milano',
      // A stored native value, like the two above it: sent as it is, not backfilled.
      payment_end_of_month: false,
      custom_fields: {},
    })
  })

  it('sends false for a checkbox the user actually turns off on edit', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, custom_fields: { vip: true } })}
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
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[VIP]}
        initial={organizationToFormValues(BASE_CUSTOMER)}
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
    const initial = organizationToFormValues({
      ...BASE_CUSTOMER,
      city: 'Milano',
      custom_fields: { 'settore': 'PMI' },
    })
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[INDUSTRY]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText('Comune'))
    await userEvent.click(screen.getByRole('combobox', { name: 'Settore' }))
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      legal_name: 'ACME Srl',
      country: 'IT',
      city: '',
      payment_end_of_month: false,
      custom_fields: { 'settore': null },
    })
  })
})

describe('the «Azienda» picker (REB-626)', () => {
  const HUMANCRAFT = { id: 'a-1', name: 'humancraft', active: true } as LegalEntityRecord
  const REBASE = { id: 'a-2', name: 'rebase ltd', active: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    legal_entities: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }

  /** `GET /api/aziende/proposta` answers the rule the server applies: GB has its own
   *  azienda, everything else gets the default. */
  function serveProposal() {
    GET.mockImplementation((path: string, options: { params: { query: { country: string } } }) => {
      if (path !== '/api/legal-entities/suggestion') throw new Error(`unexpected GET ${path}`)
      const proposed = options.params.query.country === 'GB' ? REBASE : HUMANCRAFT
      return Promise.resolve({ data: proposed, response: { status: 200 } })
    })
  }

  function renderWithLegalEntities(ui: ReactElement, value: LegalEntityValue = TWO) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedModules(client)
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
    renderWithLegalEntities(
      <OrganizationForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    await waitFor(() => expect(picker).toHaveTextContent('humancraft'))
    expect(screen.getByText(/Proposta dalla nazione/)).toBeInTheDocument()

    const country = screen.getByLabelText('Nazione')
    await userEvent.clear(country)
    await userEvent.type(country, 'GB')
    await waitFor(() => expect(picker).toHaveTextContent('rebase ltd'))

    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Overseas Ltd')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ legal_name: 'Overseas Ltd', country: 'GB', legal_entity_id: 'a-2' })
  })

  it('keeps a hand-picked azienda whatever the nation says afterwards', async () => {
    const onSubmit = vi.fn()
    renderWithLegalEntities(
      <OrganizationForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    await waitFor(() => expect(picker).toHaveTextContent('humancraft'))
    await userEvent.click(picker)
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    expect(screen.queryByText(/Proposta dalla nazione/)).not.toBeInTheDocument()

    const country = screen.getByLabelText('Nazione')
    await userEvent.clear(country)
    await userEvent.type(country, 'FR')
    expect(picker).toHaveTextContent('rebase ltd')
    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Choisie')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ country: 'FR', legal_entity_id: 'a-2' })
  })

  it('while editing shows the stored azienda, asks for no proposal, and sends it only when changed', async () => {
    const onSubmit = vi.fn()
    renderWithLegalEntities(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        initial={organizationToFormValues(BASE_CUSTOMER)}
        onSubmit={onSubmit}
      />,
    )
    const picker = screen.getByRole('combobox', { name: 'Azienda' })
    expect(picker).toHaveTextContent('humancraft')
    expect(GET).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('legal_entity_id')

    onSubmit.mockReset()
    await userEvent.click(picker)
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ legal_entity_id: 'a-2' })
  })

  it('is not drawn in a one-legal-entity space, and the body carries no azienda', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm title="Nuovo cliente" open onOpenChange={() => {}} customFields={[]} onSubmit={onSubmit} />,
    )
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Sola')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('legal_entity_id')
    expect(GET).not.toHaveBeenCalled()
  })
})

describe('a customer stranded on a deactivated azienda (REB-626, Greptile on PR #509)', () => {
  const HUMANCRAFT = { id: 'a-1', name: 'humancraft', active: true } as LegalEntityRecord
  const ONE_LEFT: LegalEntityValue = {
    legal_entities: [HUMANCRAFT],
    selected: null,
    select: vi.fn(),
    several: false, scoped: false, pinned: false, loaded: true,
    byId: (id) => (id === 'a-1' ? HUMANCRAFT : undefined),
  }

  it('still offers the active aziende while editing, so the customer can move', async () => {
    GET.mockReset()
    const onSubmit = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedModules(client)
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={ONE_LEFT}>
          <OrganizationForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[]}
            initial={organizationToFormValues({ ...BASE_CUSTOMER, legal_entity_id: 'a-gone' })}
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
    expect(submitted(onSubmit)).toMatchObject({ legal_entity_id: 'a-1' })
    expect(GET).not.toHaveBeenCalled()
  })

  it('draws nothing for a customer of the one active azienda', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedModules(client)
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={ONE_LEFT}>
          <OrganizationForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[]}
            initial={organizationToFormValues(BASE_CUSTOMER)}
            onSubmit={vi.fn()}
          />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).not.toBeInTheDocument()
  })
})

describe('the picker beside a custom field that happens to be named azienda_id (CodeRabbit, PR #509)', () => {
  const HUMANCRAFT = { id: 'a-1', name: 'humancraft', active: true } as LegalEntityRecord
  const REBASE = { id: 'a-2', name: 'rebase ltd', active: true } as LegalEntityRecord
  const TWO: LegalEntityValue = {
    legal_entities: [HUMANCRAFT, REBASE],
    selected: null,
    select: vi.fn(),
    several: true, scoped: false, pinned: false, loaded: true,
    byId: (id) => [HUMANCRAFT, REBASE].find((a) => a.id === id),
  }
  const HOMONYM: FieldDefinition = {
    key: 'legal_entity_id',
    label: 'Codice azienda interno',
    type: 'text',
    required: false,
    options: [],
  }

  it('sends the chosen azienda as the native column, never inside custom_fields', async () => {
    GET.mockReset()
    const onSubmit = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    seedModules(client)
    render(
      <QueryClientProvider client={client}>
        <LegalEntityContext value={TWO}>
          <OrganizationForm
            title="Modifica cliente"
            open
            onOpenChange={() => {}}
            customFields={[HOMONYM]}
            initial={organizationToFormValues(BASE_CUSTOMER)}
            onSubmit={onSubmit}
          />
        </LegalEntityContext>
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getByRole('combobox', { name: 'Azienda' }))
    await userEvent.click(await screen.findByRole('option', { name: 'rebase ltd' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const body = submitted(onSubmit)
    expect(body.legal_entity_id).toBe('a-2')
    expect(body.custom_fields).toEqual({})
    // And the homonymous custom control never shows the picked id as its own value.
    expect(screen.getByLabelText('Codice azienda interno')).toHaveValue('')
  })
})

describe('the «Responsabile» picker (REB-773)', () => {
  const OWNERS = [
    { id: 'u-1', name: 'Giulia' },
    { id: 'u-2', name: 'Marco' },
  ]

  it('is not drawn without the people to choose from, and the body carries no owner', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    expect(screen.queryByLabelText('Responsabile')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('owner_id')
  })

  it('shows the stored owner and sends nothing while it is unchanged', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        owners={OWNERS}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    expect(screen.getByLabelText('Responsabile')).toHaveTextContent('Giulia')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('owner_id')
  })

  it('sends the person chosen instead', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        owners={OWNERS}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    await userEvent.click(screen.getByLabelText('Responsabile'))
    await userEvent.click(await screen.findByRole('option', { name: 'Marco' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit).owner_id).toBe('u-2')
  })

  it('sends null for «Nessuno»', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={() => {}}
        customFields={[]}
        owners={OWNERS}
        initial={organizationToFormValues({ ...BASE_CUSTOMER, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    await userEvent.click(screen.getByLabelText('Responsabile'))
    await userEvent.click(await screen.findByRole('option', { name: 'Nessuno' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit).owner_id).toBeNull()
  })
})

describe('the contact points of a customer', () => {
  const STORED = [
    { id: 'cp1', kind: 'email', value: 'info@acme.it', label: 'work', note: null, is_primary: true, position: 0 },
    { id: 'cp2', kind: 'phone', value: '02 123456', label: 'billing', note: 'centralino', is_primary: true, position: 1 },
  ] as Organization['contact_points']
  const WITH_POINTS: Organization = { ...BASE_CUSTOMER, contact_points: STORED }

  it('sends the list typed on a new customer, with no id, no position and no flat email or phone', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm title="Nuovo cliente" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
    )

    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'ACME Srl')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.type(screen.getByLabelText('Valore recapito 1'), ' info@acme.it ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.contact_points).toEqual([
      { kind: 'email', value: 'info@acme.it', label: 'work', note: null, is_primary: true },
    ])
    expect(payload).not.toHaveProperty('email')
    expect(payload).not.toHaveProperty('phone')
  })

  it('says nothing about the list when it was left alone on an edit', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={organizationToFormValues(WITH_POINTS)}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByLabelText('Valore recapito 1')).toHaveValue('info@acme.it')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).not.toHaveProperty('contact_points')
  })

  it('sends the whole list, without id or position, once a point changed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={organizationToFormValues(WITH_POINTS)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText('Nota recapito 1'), 'solo ordini')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit).contact_points).toEqual([
      { kind: 'email', value: 'info@acme.it', label: 'work', note: 'solo ordini', is_primary: true },
      { kind: 'phone', value: '02 123456', label: 'billing', note: 'centralino', is_primary: true },
    ])
  })

  it('clears the stored list with an empty one when every point is removed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={organizationToFormValues(WITH_POINTS)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi recapito 2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi recapito 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit).contact_points).toEqual([])
  })

  it('refuses to send a bad address and says so under its row', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm title="Nuovo cliente" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.type(screen.getByLabelText('Valore recapito 1'), 'non-una-email')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Indirizzo email non valido')
  })

  it('puts the server sentence on the row it names, not in a banner', () => {
    renderWithClient(
      <OrganizationForm
        title="Modifica cliente"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={organizationToFormValues(WITH_POINTS)}
        problem={{
          type: 'about:blank',
          title: 'Dati non validi',
          status: 422,
          code: 'validation_failed',
          detail: 'customer.contact_points[1]: numero di telefono non valido',
          field: 'contact_points[1]',
          reason: 'numero di telefono non valido',
        }}
        onSubmit={vi.fn()}
      />,
    )

    const alerts = screen.getAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent('numero di telefono non valido')
    expect(screen.getByRole('group', { name: 'Recapito 2' })).toContainElement(alerts[0] as HTMLElement)
  })
})

/** The registry's roles on the form (the approved brief of 2026-11-10). */
describe('the roles and the supplier fields', () => {
  const NONE = { data: { items: [], next_cursor: null }, response: new Response(null, { status: 200 }) }

  function open(props: Partial<React.ComponentProps<typeof OrganizationForm>> = {}, off: readonly ModuleKey[] = []) {
    const onSubmit = vi.fn()
    renderWithClient(
      <OrganizationForm
        title="Nuovo fornitore"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        onSubmit={onSubmit}
        {...props}
      />,
      off,
    )
    return onSubmit
  }

  beforeEach(() => {
    GET.mockReset()
    PATCH.mockReset()
    GET.mockResolvedValue(NONE)
  })

  it('opens a supplier view as a supplier: its group, not the customer\'s', async () => {
    const onSubmit = open({ defaultRoles: ['supplier'] })
    expect(screen.getByRole('heading', { name: 'Come fornitore' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Come cliente' })).toBeNull()
    expect(screen.queryByLabelText('Giorni di pagamento')).toBeNull()
    expect(screen.getByRole('checkbox', { name: 'Fornitore' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Cliente' })).not.toBeChecked()

    await userEvent.type(screen.getByLabelText(/Ragione sociale/), 'Zanetti Impianti S.r.l.')
    await userEvent.type(screen.getByLabelText('Giorni concessi dal fornitore'), '30')
    await userEvent.click(screen.getByLabelText('Fine mese (fornitore)'))
    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo fiscale' }))
    await userEvent.click(screen.getByRole('option', { name: 'Professionista' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      legal_name: 'Zanetti Impianti S.r.l.',
      country: 'IT',
      roles: ['supplier'],
      supplier_kind: 'professional',
      supplier_payment_days: 30,
      supplier_payment_end_of_month: true,
      custom_fields: {},
    })
  })

  it('asks where a foreign supplier is and what it supplies, and lets go of both when the kind changes', async () => {
    const onSubmit = open({
      defaultRoles: ['supplier'],
      initial: organizationToFormValues({
        ...BASE_CUSTOMER,
        roles: ['supplier'],
        supplier_kind: 'foreign',
        supplier_area: 'extra_eu',
        supplier_supplies: 'services',
      }),
    })
    expect(screen.getByRole('combobox', { name: 'Area' })).toHaveTextContent('extra UE')
    expect(screen.getByRole('combobox', { name: 'Fornitura' })).toHaveTextContent('servizi')

    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo fiscale' }))
    await userEvent.click(screen.getByRole('option', { name: 'Società' }))
    expect(screen.queryByRole('combobox', { name: 'Area' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload).toMatchObject({ supplier_kind: 'company', supplier_area: null, supplier_supplies: null })
    // The roles did not change: not sent again.
    expect(payload).not.toHaveProperty('roles')
  })

  it('draws no supplier role, group or field with the purchases module off, and sends nothing of them', async () => {
    const onSubmit = open(
      {
        title: 'Modifica anagrafica',
        initial: organizationToFormValues({
          ...BASE_CUSTOMER,
          roles: ['customer', 'supplier'],
          supplier_kind: 'company',
          supplier_payment_days: 45,
        }),
      },
      ['purchases'],
    )
    expect(screen.queryByRole('heading', { name: 'Come fornitore' })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Ruoli' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'Come cliente' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const payload = submitted(onSubmit)
    expect(payload).not.toHaveProperty('supplier_kind')
    expect(payload).not.toHaveProperty('supplier_payment_days')
    expect(payload).not.toHaveProperty('roles')
  })

  it('adds a role from an edit and never takes one away', async () => {
    const onSubmit = open({
      title: 'Modifica anagrafica',
      initial: organizationToFormValues(BASE_CUSTOMER),
    })
    expect(screen.getByRole('checkbox', { name: 'Cliente' })).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Fornitore' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).toMatchObject({ roles: ['customer', 'supplier'] })
  })

  it('names the server\'s refusal of a kind under the kind', async () => {
    open({
      defaultRoles: ['supplier'],
      problem: {
        type: 'x',
        title: 'x',
        status: 422,
        code: 'validation_failed',
        field: 'supplier_kind',
        detail: 'valore non valido: x',
        reason: 'un freelance e\' un professionista',
      },
    })
    expect(screen.getByText('un freelance e\' un professionista')).toBeInTheDocument()
    // Said once, under the control, and not a second time as a banner above the form.
    expect(screen.queryAllByRole('alert')).toHaveLength(0)
  })

  describe('the duplicate catch under the VAT number', () => {
    const ZANETTI = {
      ...BASE_CUSTOMER,
      id: 'z1',
      legal_name: 'Zanetti Impianti S.r.l.',
      vat_number: 'IT 01234567890',
      roles: ['customer'],
    }

    function listing(...items: Organization[]) {
      GET.mockResolvedValue({
        data: { items, next_cursor: null },
        response: new Response(null, { status: 200 }),
      })
    }

    it('says who has the number and offers the role the form is for', async () => {
      listing(ZANETTI)
      open({ defaultRoles: ['supplier'] })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'it01234567890')

      expect(await screen.findByText('Esiste già: Zanetti Impianti S.r.l. (cliente)')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Aggiungi il ruolo Fornitore' })).toBeInTheDocument()
      // The lookup is across every role, on what was typed.
      expect(GET).toHaveBeenCalledWith(
        '/api/organizations',
        expect.objectContaining({
          params: { query: expect.objectContaining({ role: 'any', search: 'it01234567890' }) },
        }),
      )
      // A second record for the same company is not offered.
      expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    })

    it('adds the role to the existing organization and goes to it, saving nothing new', async () => {
      listing(ZANETTI)
      PATCH.mockResolvedValue({
        data: { ...ZANETTI, roles: ['customer', 'supplier'] },
        response: new Response(null, { status: 200 }),
      })
      const onOpenExisting = vi.fn()
      const onOpenChange = vi.fn()
      const onSubmit = open({ defaultRoles: ['supplier'], onOpenExisting, onOpenChange })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT01234567890')
      await userEvent.click(await screen.findByRole('button', { name: 'Aggiungi il ruolo Fornitore' }))

      await waitFor(() => expect(onOpenExisting).toHaveBeenCalledOnce())
      expect(PATCH).toHaveBeenCalledWith('/api/organizations/{organization_id}', {
        params: { path: { organization_id: 'z1' } },
        body: { roles: ['customer', 'supplier'] },
      })
      expect(onOpenExisting.mock.calls[0]![0]).toMatchObject({ id: 'z1' })
      expect(onOpenChange).toHaveBeenCalledWith(false)
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('offers «Cliente» when the form is for a customer and the existing one is only a supplier', async () => {
      listing({ ...ZANETTI, roles: ['supplier'] })
      open({ defaultRoles: ['customer'] })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT01234567890')
      expect(await screen.findByText('Esiste già: Zanetti Impianti S.r.l. (fornitore)')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Aggiungi il ruolo Cliente' })).toBeInTheDocument()
    })

    it('offers to open the page when the existing organization already has the role', async () => {
      listing({ ...ZANETTI, roles: ['customer', 'supplier'] })
      const onOpenExisting = vi.fn()
      open({ defaultRoles: ['supplier'], onOpenExisting })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT01234567890')
      expect(
        await screen.findByText('Esiste già: Zanetti Impianti S.r.l. (cliente e fornitore)'),
      ).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Aggiungi il ruolo/ })).toBeNull()
      await userEvent.click(screen.getByRole('button', { name: 'Apri la scheda' }))
      expect(onOpenExisting).toHaveBeenCalledOnce()
    })

    it('refuses to pick between two matches: no action, and nothing to save', async () => {
      listing(ZANETTI, { ...ZANETTI, id: 'z2', legal_name: 'Zanetti Servizi S.r.l.' })
      open({ defaultRoles: ['supplier'] })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT01234567890')
      expect(await screen.findByText(/già su più anagrafiche/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Aggiungi il ruolo/ })).toBeNull()
      expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    })

    it('compares the number without case or punctuation, and only inside the country', async () => {
      listing({ ...ZANETTI, vat_number: 'DE 123.456.789', country: 'DE' })
      open({ defaultRoles: ['supplier'] })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'de123456789')
      // A German number is not an Italian company's: the country of the form is IT.
      await waitFor(() => expect(GET).toHaveBeenCalled())
      expect(screen.queryByText(/Esiste già/)).toBeNull()
      expect(screen.getByRole('button', { name: 'Salva' })).toBeEnabled()
    })

    it('does not match a number that only starts like the typed one', async () => {
      listing({ ...ZANETTI, vat_number: 'IT01234567890' })
      open({ defaultRoles: ['supplier'] })
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT0123456')
      await waitFor(() => expect(GET).toHaveBeenCalled())
      expect(screen.queryByText(/Esiste già/)).toBeNull()
    })

    it('leaves the organization being edited out of it, and asks nothing while the number is unchanged', async () => {
      listing({ ...ZANETTI, id: 'c1' })
      open({
        title: 'Modifica anagrafica',
        organizationId: 'c1',
        initial: organizationToFormValues({ ...BASE_CUSTOMER, vat_number: 'IT 01234567890' }),
      })
      expect(GET).not.toHaveBeenCalledWith('/api/organizations', expect.anything())
      await userEvent.type(screen.getByLabelText('P.IVA'), '1')
      await userEvent.type(screen.getByLabelText('P.IVA'), '{Backspace}')
      await waitFor(() => expect(screen.getByRole('button', { name: 'Salva' })).toBeEnabled())
      expect(screen.queryByText(/Esiste già/)).toBeNull()
    })

    it('never names the record it just saved when the lookup answers after «Salva», and catches again after a refusal', async () => {
      // The lookup is still on its way when «Salva» is pressed; it answers after the create
      // committed, with the record this form saved (REB-1056).
      const pending: Array<(value: unknown) => void> = []
      GET.mockImplementation((path: string) =>
        path === '/api/organizations'
          ? new Promise((resolve) => {
              pending.push(resolve)
            })
          : Promise.resolve(NONE),
      )
      // The parent holds the server's refusal, as the list route does with its mutation's error.
      type Problem = React.ComponentProps<typeof OrganizationForm>['problem']
      let refuse: (problem: Problem) => void = () => undefined
      const onSubmit = vi.fn()
      function Parent() {
        const [problem, setProblem] = useState<Problem>(null)
        refuse = setProblem
        return (
          <OrganizationForm
            title="Nuovo fornitore"
            open
            onOpenChange={vi.fn()}
            customFields={[]}
            defaultRoles={['supplier']}
            onSubmit={onSubmit}
            problem={problem}
          />
        )
      }
      renderWithClient(<Parent />)
      await userEvent.type(screen.getByLabelText('P.IVA'), 'IT01234567890')
      await waitFor(() =>
        expect(GET).toHaveBeenCalledWith(
          '/api/organizations',
          expect.objectContaining({ params: { query: expect.objectContaining({ search: 'IT01234567890' }) } }),
        ),
      )
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
      expect(onSubmit).toHaveBeenCalledOnce()

      await act(async () => {
        for (const answer of pending) {
          answer({ data: { items: [ZANETTI], next_cursor: null }, response: new Response(null, { status: 200 }) })
        }
      })
      // Give the query the time to hand its answer to the form before looking for the hint.
      await act(() => new Promise((resolve) => setTimeout(resolve, 100)))
      expect(screen.queryByText(/Esiste già/)).toBeNull()

      // The server refused the save: the number may really be taken, so the catch is back.
      act(() => refuse({ type: 'x', title: 'x', status: 409, code: 'conflict', detail: 'partita IVA già presente' }))
      expect(await screen.findByText('Esiste già: Zanetti Impianti S.r.l. (cliente)')).toBeInTheDocument()
    })
  })
})
