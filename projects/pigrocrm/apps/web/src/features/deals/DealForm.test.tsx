import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DealForm, dealToFormValues } from './DealForm'
import type { Deal } from './queries'
import { api } from '@/lib/api'
import type { FieldDefinition } from '@/lib/schema'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Link: ({ children, params }: { children: React.ReactNode; params: Record<string, string> }) => (
      <a href={`/contracts/${params.contractId}`}>{children}</a>
    ),
  }
})

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`), mirroring
// `PersonForm.test.tsx`: `DealForm` mounts `OrganizationPicker` (for the "Cliente"
// picker), which calls `useOrganizations` through the real `unwrap`, so what is under test is this form's own
// handling of what that hook returns, not a reimplementation of it.
const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')

function organizationPage(items: { id: string; legal_name: string }[]) {
  return Promise.resolve({
    data: { items, next_cursor: null },
    response: new Response(null, { status: 200 }),
  })
}

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockReturnValue(organizationPage([]))
  mockPost.mockReset()
  mockPost.mockImplementation((() =>
    Promise.resolve({
      data: { expected_value: '26000.00', one_off_value: '20000.00', monthly_recurring_value: '500.00' },
      response: new Response(null, { status: 200 }),
    })) as never)
})

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const BASE_DEAL: Deal = {
  id: 'd1',
  legal_entity_id: 'a-1',
  name: 'Sito vetrina',
  customer_id: 'cust-1',
  pipeline_stage_id: 's1',
  expected_value: null,
  one_off_value: null,
  monthly_recurring_value: null,
  probability: 10,
  expected_close_date: null,
  owner_id: null,
  budgeted_hours: null,
  budgeted_value: null,
  // See `columns.test.ts`: `DealRead.hourly_rate` arrived with slice 4A and is
  // `null` for a deal that never overrode the user's default rate, and
  // `DealRead.closed_at` with slice 6 -- `null` while the deal is open.
  hourly_rate: null,
  closed_at: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

const SOURCE: FieldDefinition = {
  key: 'fonte',
  label: 'Fonte',
  type: 'select',
  required: false,
  options: ['referral', 'outbound'],
}

const URGENT: FieldDefinition = {
  key: 'urgente',
  label: 'Urgente',
  type: 'checkbox',
  required: false,
  options: [],
}

function submitted(onSubmit: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const [first] = onSubmit.mock.calls
  if (!first) throw new Error('onSubmit was never called')
  return first[0] as Record<string, unknown>
}

describe('DealForm', () => {
  it('round-trips a value for a field the active schema still defines', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[SOURCE]}
        initial={dealToFormValues({ ...BASE_DEAL, custom_fields: { 'fonte': 'referral' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    // `probabilita: 10` round-trips too: `BASE_DEAL.probability` is a real,
    // non-blank value the user never touched, and an untouched native field is
    // resent unchanged, not stripped out -- the same "idempotent resend" this
    // form gives every other already-set field (see `submit`'s own comment).
    expect(submitted(onSubmit)).toEqual({
      name: 'Sito vetrina',
      probability: 10,
      custom_fields: { 'fonte': 'referral' },
    })
  })

  /**
   * The defect this guards, identical in shape to `OrganizationForm`/`PersonForm`'s
   * own (reproduced live there first): archive a field definition while a
   * record still carries a value for it, open Edit, press Save with
   * nothing else changed. A flat form state would re-derive "is this key
   * custom?" from the *active* schema, reclassify the value as native, and
   * 422 on `DealUpdate`'s `extra="forbid"`.
   */
  it('neither sends an archived field as a native column nor drops its stored value', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={dealToFormValues({ ...BASE_DEAL, custom_fields: { 'fonte': 'referral' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload).not.toHaveProperty('fonte')
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({ name: 'Sito vetrina', probability: 10, custom_fields: {} })
  })

  it('sends an untouched checkbox as false on create, never as an absent key', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[URGENT]} onSubmit={onSubmit} />,
    )

    await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto nuovo')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      name: 'Progetto nuovo',
      custom_fields: { 'urgente': false },
    })
  })

  /** REB-879: `note` on a new deal is its first note, written by the creating actor. After
   *  that the thread is the page's own, and `DealUpdate` refuses a `note` outright. */
  it('draws «Nota iniziale» and sends it as `note` on create', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
    )

    await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto nuovo')
    await userEvent.type(screen.getByLabelText('Nota iniziale'), 'Contatto arrivato dal sito')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      name: 'Progetto nuovo',
      note: 'Contatto arrivato dal sito',
      custom_fields: {},
    })
  })

  it('draws no note field on edit and never puts `note` in the payload', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={dealToFormValues(BASE_DEAL)}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.queryByLabelText('Nota iniziale')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Note')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('note')
  })

  /** The other side of the same rule: doing this on edit would persist
   *  `urgente: false` on a record that never had a value for it, as a side
   *  effect of touching Nome alone. */
  it('does not backfill an untouched checkbox on edit, whatever else changed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[URGENT]}
        initial={dealToFormValues(BASE_DEAL)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText(/^Nome/), ' 2')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      name: 'Sito vetrina 2',
      probability: 10,
      custom_fields: {},
    })
  })

  it('clears a custom field the user emptied with null, and a native text field with an empty string', async () => {
    const onSubmit = vi.fn()
    const initial = dealToFormValues({
      ...BASE_DEAL,
      custom_fields: { 'fonte': 'referral' },
    })
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[SOURCE]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText(/^Nome/))
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      name: '',
      probability: 10,
      custom_fields: { 'fonte': null },
    })
  })

  it('clears a native numeric or date column with null, not with an empty string', async () => {
    // The payoff of task 4B-1 (residual A14), on the form that shows it. `""` is not a
    // decimal and not a date, so before this these three keys came back as a 422 from
    // Pydantic and the estimate the user was trying to take back stayed put -- reading,
    // in the budget report, as an infinite overrun. `null` is what clears them now, and
    // `name` in the same payload proves the text spelling did not move.
    const onSubmit = vi.fn()
    const initial = dealToFormValues({
      ...BASE_DEAL,
      budgeted_hours: '40.00',
      budgeted_value: '10000.00',
      expected_close_date: '2026-12-31',
    })
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText('Ore preventivate'))
    await userEvent.clear(screen.getByLabelText('Valore preventivato'))
    await userEvent.clear(screen.getByLabelText('Chiusura prevista'))
    await userEvent.clear(screen.getByLabelText(/^Nome/))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      name: '',
      probability: 10,
      budgeted_hours: null,
      budgeted_value: null,
      expected_close_date: null,
      custom_fields: {},
    })
  })

  describe('customer selection', () => {
    it('shows a required Cliente picker on create', () => {
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={vi.fn()} />,
      )
      expect(screen.getByText('Cliente')).toBeInTheDocument()
      expect(screen.getByRole('textbox', { name: 'Cerca un cliente' })).toBeInTheDocument()
    })

    it('hides the Cliente picker on edit -- a deal cannot be reassigned to a different customer', () => {
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={dealToFormValues(BASE_DEAL)}
          onSubmit={vi.fn()}
        />,
      )
      expect(screen.queryByText('Cliente')).not.toBeInTheDocument()
      expect(screen.queryByRole('textbox', { name: 'Cerca un cliente' })).not.toBeInTheDocument()
    })

    /**
     * A fix-round defect: `isCreate` used to gate only the JSX, while the
     * customer request itself ran unconditionally at `DealForm`'s own top
     * level -- so every deal detail page (which mounts `DealForm` behind
     * `open={false}` for as long as it is simply being viewed) fired a
     * `GET /api/organizations` nobody could ever see the result of. The picker is
     * its own component (`OrganizationPicker`), mounted -- and therefore only ever
     * searching organizations -- inside the same `{isCreate && ...}` branch the
     * previous test already covers visually; this test covers the network side
     * of the identical branch.
     */
    it('does not fetch the customer list while editing -- the picker is not rendered there', () => {
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={dealToFormValues(BASE_DEAL)}
          onSubmit={vi.fn()}
        />,
      )
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('asks the server for a few organizations on create, not for all of them', () => {
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={vi.fn()} />,
      )
      expect(mockGet).toHaveBeenCalledWith(
        '/api/organizations',
        expect.objectContaining({ params: expect.objectContaining({ query: expect.objectContaining({ limit: 8 }) }) }),
      )
    })

    it('omits customer_id on create when none was chosen, adding no client-side gate of its own', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
      )

      await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto senza cliente')
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      const payload = submitted(onSubmit)
      expect(payload).not.toHaveProperty('customer_id')
    })

    it('sends the chosen customer_id on create', async () => {
      mockGet.mockReturnValue(organizationPage([{ id: 'cust-1', legal_name: 'ACME Srl' }]))
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
      )

      await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto ACME')
      await userEvent.click(await screen.findByRole('button', { name: 'ACME Srl' }))
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      expect(submitted(onSubmit)).toEqual({
        name: 'Progetto ACME',
        customer_id: 'cust-1',
        custom_fields: {},
      })
    })

    it('never includes customer_id in an edit payload', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={dealToFormValues(BASE_DEAL)}
          onSubmit={onSubmit}
        />,
      )

      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      expect(submitted(onSubmit)).not.toHaveProperty('customer_id')
    })
  })
})

describe('the «Responsabile» picker (REB-780)', () => {
  const OWNERS = [
    { id: 'u-1', name: 'Giulia' },
    { id: 'u-2', name: 'Marco' },
  ]

  it('shows the stored owner and sends nothing while it is unchanged', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        owners={OWNERS}
        initial={dealToFormValues({ ...BASE_DEAL, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    expect(screen.getByLabelText('Responsabile')).toHaveTextContent('Giulia')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('owner_id')
  })

  it('sends the colleague chosen instead, and null for «Nessuno»', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        owners={OWNERS}
        initial={dealToFormValues({ ...BASE_DEAL, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    await userEvent.click(screen.getByLabelText('Responsabile'))
    await userEvent.click(await screen.findByRole('option', { name: 'Marco' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit).owner_id).toBe('u-2')
  })

  it('shows the signed-in person on a new deal and leaves the default to the server', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Nuovo deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        owners={OWNERS}
        defaultOwnerId="u-2"
        onSubmit={onSubmit}
      />,
    )
    expect(screen.getByLabelText('Responsabile')).toHaveTextContent('Marco')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('owner_id')
  })
})

describe('«Nessuno» on the owner picker (REB-780)', () => {
  it('clears the owner of an existing deal, and is not offered on a new one', async () => {
    const onSubmit = vi.fn()
    const owners = [{ id: 'u-1', name: 'Giulia' }]
    const { unmount } = renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        owners={owners}
        initial={dealToFormValues({ ...BASE_DEAL, owner_id: 'u-1' })}
        onSubmit={onSubmit}
      />,
    )
    await userEvent.click(screen.getByLabelText('Responsabile'))
    await userEvent.click(await screen.findByRole('option', { name: 'Nessuno' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit).owner_id).toBeNull()
    unmount()

    renderWithClient(
      <DealForm
        title="Nuovo deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        owners={owners}
        defaultOwnerId="u-1"
        onSubmit={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByLabelText('Responsabile'))
    await screen.findByRole('option', { name: 'Giulia' })
    expect(screen.queryByRole('option', { name: 'Nessuno' })).not.toBeInTheDocument()
  })

  describe('value lines', () => {
    it('opens a new deal with one lump sum, and sends nothing for a price left empty', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
      )
      expect(screen.getByRole('group', { name: 'Riga 1' })).toBeInTheDocument()
      expect(screen.getByLabelText('Unità riga 1')).toHaveTextContent('A corpo')
      expect(screen.getByLabelText('Periodo riga 1')).toHaveTextContent('Una tantum')
      // A lump sum is its price: no quantity, and paid once it has no duration either.
      expect(screen.queryByLabelText('Quantità riga 1')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Durata (mesi) riga 1')).not.toBeInTheDocument()

      await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto nuovo')
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      const body = submitted(onSubmit)
      expect(body).not.toHaveProperty('value_lines')
      expect(body).not.toHaveProperty('expected_value')
      expect(mockPost).not.toHaveBeenCalled()
    })

    it('sends the lines, never expected_value, and shows the three figures the server previewed', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
      )
      await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto nuovo')
      await userEvent.type(screen.getByLabelText('Importo (€) riga 1'), '20000')
      await userEvent.click(screen.getByRole('button', { name: 'Aggiungi riga' }))
      await userEvent.type(screen.getByLabelText('Descrizione riga 2'), 'Manutenzione')
      await userEvent.type(screen.getByLabelText('Importo (€) riga 2'), '500')
      await userEvent.click(screen.getByLabelText('Periodo riga 2'))
      await userEvent.click(await screen.findByRole('option', { name: 'Mensile' }))

      // The figures are the server's answer to the lines as typed, not a sum made here.
      await waitFor(() => {
        const sent = (mockPost.mock.calls.at(-1) as unknown as [string, { body: { value_lines: unknown[] } }] | undefined)
        expect(sent?.[1].body.value_lines).toHaveLength(2)
      })
      await waitFor(() => expect(screen.getByTestId('preview-total')).toHaveTextContent('26.000,00'))
      expect(screen.getByTestId('preview-one-off')).toHaveTextContent('20.000,00')
      expect(screen.getByTestId('preview-monthly')).toHaveTextContent('500,00')
      expect(screen.getByTestId('preview-monthly')).toHaveTextContent('/mese')
      const lastCall = mockPost.mock.calls.at(-1) as unknown as [string, { body: unknown }]
      expect(lastCall[0]).toBe('/api/deals/value-preview')
      expect(lastCall[1].body).toEqual({
        value_lines: [
          { description: null, unit: 'flat', delivery_period: 'one_off', unit_price: '20000', quantity: '1', start: null, duration_months: null },
          { description: 'Manutenzione', unit: 'flat', delivery_period: 'monthly', unit_price: '500', quantity: '1', start: null, duration_months: null },
        ],
        expected_close_date: null,
      })

      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
      const body = submitted(onSubmit)
      expect(body).not.toHaveProperty('expected_value')
      expect(body.value_lines).toEqual((lastCall[1].body as { value_lines: unknown }).value_lines)
    })

    it('asks for a quantity only on a day or hour line', async () => {
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={vi.fn()} />,
      )
      await userEvent.click(screen.getByLabelText('Unità riga 1'))
      await userEvent.click(await screen.findByRole('option', { name: 'Giorni' }))
      expect(screen.getByLabelText('Quantità riga 1')).toBeInTheDocument()
      expect(screen.getByLabelText('Durata (mesi) riga 1')).toBeInTheDocument()
    })

    it('puts the server sentence on the line it names', async () => {
      const problem = {
        type: 'about:blank', title: 'Dati non validi', status: 422, code: 'validation_failed',
        field: 'value_lines', detail: 'riga 2 (Canone): il totale della riga è troppo grande',
      }
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          problem={problem}
          initial={dealToFormValues({
            ...BASE_DEAL,
            value_lines: [
              { id: 'a', position: 0, description: null, unit: 'flat', delivery_period: 'one_off', unit_price: '10.00', quantity: '1.00', start: null, duration_months: null },
              { id: 'b', position: 1, description: 'Canone', unit: 'flat', delivery_period: 'monthly', unit_price: '99999999.00', quantity: '1.00', start: null, duration_months: null },
            ],
          })}
          onSubmit={vi.fn()}
        />,
      )
      const second = screen.getByRole('group', { name: 'Riga 2' })
      expect(second).toHaveTextContent('il totale della riga è troppo grande')
      expect(screen.getByRole('group', { name: 'Riga 1' })).not.toHaveTextContent('troppo grande')
    })

    it('resends the lines on edit only when they changed', async () => {
      const onSubmit = vi.fn()
      const line = { id: 'a', position: 0, description: null, unit: 'flat' as const, delivery_period: 'one_off' as const, unit_price: '10.00', quantity: '1.00', start: null, duration_months: null }
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={dealToFormValues({ ...BASE_DEAL, value_lines: [line] })}
          onSubmit={onSubmit}
        />,
      )
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
      expect(submitted(onSubmit)).not.toHaveProperty('value_lines')

      await userEvent.clear(screen.getByLabelText('Importo (€) riga 1'))
      await userEvent.type(screen.getByLabelText('Importo (€) riga 1'), '12')
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
      expect((onSubmit.mock.calls[1]![0] as Record<string, unknown>).value_lines).toEqual([
        { description: null, unit: 'flat', delivery_period: 'one_off', unit_price: '12', quantity: '1', start: null, duration_months: null },
      ])
    })

    const LINE = { id: 'a', position: 0, description: null, unit: 'flat' as const, delivery_period: 'one_off' as const, unit_price: '10.00', quantity: '1.00', start: null, duration_months: null }

    it('shows no figures once every line is gone, while the save sends an empty list', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm
          title="Modifica deal"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={dealToFormValues({ ...BASE_DEAL, value_lines: [LINE] })}
          onSubmit={onSubmit}
        />,
      )
      await waitFor(() => expect(screen.getByTestId('preview-total')).toHaveTextContent('26.000,00'))

      await userEvent.click(screen.getByRole('button', { name: 'Rimuovi riga 1' }))
      // Immediately, not after the debounce: the old total must not outlive its lines.
      expect(screen.getByTestId('preview-total')).toHaveTextContent('—')
      expect(screen.getByTestId('preview-monthly')).toHaveTextContent('—')

      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
      expect(submitted(onSubmit).value_lines).toEqual([])
    })

    it("does not show another deal's figures when the form is opened again", async () => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const form = (open: boolean, price: string) => (
        <QueryClientProvider client={client}>
          <DealForm
            title="Modifica deal"
            open={open}
            onOpenChange={vi.fn()}
            customFields={[]}
            initial={dealToFormValues({ ...BASE_DEAL, value_lines: [{ ...LINE, unit_price: price }] })}
            onSubmit={vi.fn()}
          />
        </QueryClientProvider>
      )
      const { rerender } = render(form(true, '10.00'))
      await waitFor(() => expect(screen.getByTestId('preview-total')).toHaveTextContent('26.000,00'))

      rerender(form(false, '10.00'))
      // The next deal's answer never arrives: what shows meanwhile must not be the last one's.
      mockPost.mockImplementation((() => new Promise(() => {})) as never)
      rerender(form(true, '77.00'))
      expect(screen.getByTestId('preview-total')).toHaveTextContent('—')
    })
  })

  /** Once the deal is a contract the cards are the price: the lines show, nothing edits them,
   *  and the save never sends them (the server would refuse a changed list). */
  it('shows the lines of a converted deal read-only, linked to the contract, and sends none', async () => {
    const onSubmit = vi.fn()
    const converted = {
      ...BASE_DEAL,
      contract_id: 'k1',
      value_lines: [
        {
          id: 'l1',
          position: 0,
          description: 'Sviluppo',
          unit: 'flat' as const,
          delivery_period: 'one_off' as const,
          unit_price: '20000.00',
          quantity: '1.00',
          start: null,
          duration_months: null,
        },
      ],
    }
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[]}
        initial={dealToFormValues(converted)}
        contractId="k1"
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByText(/Il prezzo ora è nel contratto/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri il contratto' })).toHaveAttribute(
      'href',
      '/contracts/k1',
    )
    expect(screen.getByText(/Sviluppo/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Aggiungi riga/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/^Descrizione/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(submitted(onSubmit)).not.toHaveProperty('value_lines')
    expect(mockPost).not.toHaveBeenCalled()
  })
})
