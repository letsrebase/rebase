import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DealForm, dealToFormValues } from './DealForm'
import type { Deal } from './queries'
import { api } from '@/lib/api'
import type { FieldDefinition } from '@/lib/schema'

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`), mirroring
// `PersonForm.test.tsx`: `DealForm` calls `useCustomers` (for the "Cliente"
// picker) through the real `unwrap`, so what is under test is this form's own
// handling of what that hook returns, not a reimplementation of it.
const mockGet = vi.spyOn(api, 'GET')

function customerPage(items: { id: string; ragione_sociale: string }[]) {
  return Promise.resolve({
    data: { items, next_cursor: null },
    response: new Response(null, { status: 200 }),
  })
}

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockReturnValue(customerPage([]))
})

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const BASE_DEAL: Deal = {
  id: 'd1',
  azienda_id: 'a-1',
  nome: 'Sito vetrina',
  customer_id: 'cust-1',
  pipeline_stage_id: 's1',
  valore_previsto: null,
  probabilita: 10,
  data_chiusura_prevista: null,
  owner_id: null,
  note: null,
  ore_preventivate: null,
  valore_preventivato: null,
  // See `columns.test.ts`: `DealRead.tariffa_oraria` arrived with slice 4A and is
  // `null` for a deal that never overrode the user's default rate, and
  // `DealRead.chiuso_il` with slice 6 -- `null` while the deal is open.
  tariffa_oraria: null,
  chiuso_il: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

const FONTE: FieldDefinition = {
  key: 'fonte',
  label: 'Fonte',
  type: 'select',
  required: false,
  options: ['referral', 'outbound'],
}

const URGENTE: FieldDefinition = {
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
        customFields={[FONTE]}
        initial={dealToFormValues({ ...BASE_DEAL, custom_fields: { fonte: 'referral' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    // `probabilita: 10` round-trips too: `BASE_DEAL.probabilita` is a real,
    // non-blank value the user never touched, and an untouched native field is
    // resent unchanged, not stripped out -- the same "idempotent resend" this
    // form gives every other already-set field (see `submit`'s own comment).
    expect(submitted(onSubmit)).toEqual({
      nome: 'Sito vetrina',
      probabilita: 10,
      custom_fields: { fonte: 'referral' },
    })
  })

  /**
   * The defect this guards, identical in shape to `CustomerForm`/`PersonForm`'s
   * own (reproduced live there first): archive a field definition while a
   * record still carries a value for it, open Modifica, press Salva with
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
        initial={dealToFormValues({ ...BASE_DEAL, custom_fields: { fonte: 'referral' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload).not.toHaveProperty('fonte')
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({ nome: 'Sito vetrina', probabilita: 10, custom_fields: {} })
  })

  it('sends an untouched checkbox as false on create, never as an absent key', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[URGENTE]} onSubmit={onSubmit} />,
    )

    await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto nuovo')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      nome: 'Progetto nuovo',
      custom_fields: { urgente: false },
    })
  })

  /** The other side of the same rule: doing this on edit would persist
   *  `urgente: false` on a record that never had a value for it, as a side
   *  effect of touching Note alone. */
  it('does not backfill an untouched checkbox on edit, whatever else changed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[URGENTE]}
        initial={dealToFormValues(BASE_DEAL)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText('Note'), 'Richiamare a settembre')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      nome: 'Sito vetrina',
      probabilita: 10,
      note: 'Richiamare a settembre',
      custom_fields: {},
    })
  })

  it('clears a custom field the user emptied with null, and a native text field with an empty string', async () => {
    const onSubmit = vi.fn()
    const initial = dealToFormValues({
      ...BASE_DEAL,
      note: 'Vecchia nota',
      custom_fields: { fonte: 'referral' },
    })
    renderWithClient(
      <DealForm
        title="Modifica deal"
        open
        onOpenChange={vi.fn()}
        customFields={[FONTE]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText('Note'))
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      nome: 'Sito vetrina',
      probabilita: 10,
      note: '',
      custom_fields: { fonte: null },
    })
  })

  it('clears a native numeric or date column with null, not with an empty string', async () => {
    // The payoff of task 4B-1 (residual A14), on the form that shows it. `""` is not a
    // decimal and not a date, so before this these three keys came back as a 422 from
    // Pydantic and the estimate the user was trying to take back stayed put -- reading,
    // in the budget report, as an infinite overrun. `null` is what clears them now, and
    // `note` in the same payload proves the text spelling did not move.
    const onSubmit = vi.fn()
    const initial = dealToFormValues({
      ...BASE_DEAL,
      note: 'Vecchia nota',
      ore_preventivate: '40.00',
      valore_preventivato: '10000.00',
      data_chiusura_prevista: '2026-12-31',
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
    await userEvent.clear(screen.getByLabelText('Note'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      nome: 'Sito vetrina',
      probabilita: 10,
      ore_preventivate: null,
      valore_preventivato: null,
      data_chiusura_prevista: null,
      note: '',
      custom_fields: {},
    })
  })

  describe('customer selection', () => {
    it('shows a required Cliente picker on create', () => {
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={vi.fn()} />,
      )
      expect(screen.getByText('Cliente')).toBeInTheDocument()
      expect(screen.getByRole('combobox')).toBeInTheDocument()
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
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    })

    /**
     * A fix-round defect: `isCreate` used to gate only the JSX, while
     * `useCustomers({limit: 200})` itself ran unconditionally at `DealForm`'s
     * own top level -- so every deal detail page (which mounts `DealForm`
     * behind `open={false}` for as long as it is simply being viewed) fired
     * a `GET /api/customers?limit=200` nobody could ever see the result of.
     * The picker is now its own component (`CustomerPicker`), mounted -- and
     * therefore only ever calling `useCustomers` -- inside the same
     * `{isCreate && ...}` branch the previous test already covers visually;
     * this test covers the network side of the identical branch.
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

    it('does fetch the customer list on create -- there is a picker to fill', () => {
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={vi.fn()} />,
      )
      expect(mockGet).toHaveBeenCalledWith(
        '/api/customers',
        expect.objectContaining({ params: expect.objectContaining({ query: expect.objectContaining({ limit: 200 }) }) }),
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
      mockGet.mockReturnValue(customerPage([{ id: 'cust-1', ragione_sociale: 'ACME Srl' }]))
      const onSubmit = vi.fn()
      renderWithClient(
        <DealForm title="Nuovo deal" open onOpenChange={vi.fn()} customFields={[]} onSubmit={onSubmit} />,
      )

      await userEvent.type(screen.getByLabelText(/^Nome/), 'Progetto ACME')
      await userEvent.click(screen.getByRole('combobox'))
      await userEvent.click(await screen.findByRole('option', { name: 'ACME Srl' }))
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      expect(submitted(onSubmit)).toEqual({
        nome: 'Progetto ACME',
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
