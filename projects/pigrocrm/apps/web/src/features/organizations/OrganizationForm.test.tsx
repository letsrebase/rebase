import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { LegalEntityContext, type LegalEntityRecord, type LegalEntityValue } from '@/lib/legalEntity'
import { OrganizationForm, organizationToFormValues } from './OrganizationForm'
import type { Organization } from './queries'
import type { FieldDefinition } from '@/lib/schema'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

import { api } from '@/lib/api'

const GET = api.GET as unknown as Mock

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
function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
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
      payment_end_of_month: false,
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
    await userEvent.click(screen.getByRole('combobox'))
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
