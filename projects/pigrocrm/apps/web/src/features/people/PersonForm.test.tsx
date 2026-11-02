import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonForm, personToFormValues } from './PersonForm'
import type { Person } from './queries'
import { api } from '@/lib/api'
import type { FieldDefinition } from '@/lib/schema'

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`), mirroring
// `Timeline.test.tsx`: `PersonForm` calls `useCustomers` (for the "Azienda (cliente)"
// picker), a real query through the real `unwrap`, so what is under test is this
// form's own handling of what that hook returns, not a reimplementation of it.
const mockGet = vi.spyOn(api, 'GET')

function customerPage(items: { id: string; ragione_sociale: string }[]) {
  return Promise.resolve({
    data: { items, next_cursor: null },
    response: new Response(null, { status: 200 }),
  })
}

beforeEach(() => {
  mockGet.mockReset()
  // Every test that renders PersonForm with the dialog open reaches
  // `CustomerPicker`, which calls `useCustomers` for the "Azienda (cliente)" picker -- an
  // empty page is enough for every one of those that does not care about its
  // contents; the ones that do override this before rendering. The one test
  // below that renders with the dialog closed never calls `api.GET` at all, so
  // this default simply goes unused there.
  mockGet.mockReturnValue(customerPage([]))
})

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const BASE_PERSON: Person = {
  id: 'p1',
  nome: 'Mario',
  cognome: 'Rossi',
  email: null,
  telefono: null,
  ruolo: null,
  linkedin: null,
  customer_id: null,
  custom_fields: {},
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:00:00Z',
}

const SENIORITY: FieldDefinition = {
  key: 'seniority',
  label: 'Seniority',
  type: 'select',
  required: false,
  options: ['Junior', 'Senior'],
}

const DISPONIBILE: FieldDefinition = {
  key: 'disponibile',
  label: 'Disponibile',
  type: 'checkbox',
  required: false,
  options: [],
}

/**
 * A health response for `GET /api/gmail/account`. `syncing` is the only distinction the
 * notice cares about: a mailbox that is connected, active and holds the read scope is
 * one that will actually run another cycle.
 */
function gmailHealth(syncing: boolean) {
  return {
    account: syncing
      ? {
          id: 'g1',
          email_address: 'io@example.it',
          scopes_granted: ['https://www.googleapis.com/auth/gmail.readonly'],
          status: 'active',
          consent_expires_at: null,
          last_error: null,
          last_error_at: null,
          last_sync_at: null,
          sync_watermark: null,
          gmail_store_bodies: true,
          connected_at: '2026-08-01T09:00:00Z',
          disconnected_at: null,
        }
      : null,
    banner: null,
    banner_text: null,
    missing_scopes: syncing ? [] : ['https://www.googleapis.com/auth/gmail.readonly'],
    configured: true,
  }
}

/** Routes by path, because this form now reads two endpoints: the customer list for its
 *  picker and the Gmail health row for the sync notice. */
function mockGmail(syncing: boolean) {
  mockGet.mockImplementation(
    ((path: string) =>
      path === '/api/gmail/account'
        ? Promise.resolve({ data: gmailHealth(syncing), response: new Response(null, { status: 200 }) })
        : customerPage([])) as never,
  )
}

function submitted(onSubmit: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const [first] = onSubmit.mock.calls
  if (!first) throw new Error('onSubmit was never called')
  return first[0] as Record<string, unknown>
}

describe('PersonForm', () => {
  it('round-trips a value for a field the active schema still defines', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <PersonForm
        title="Modifica persona"
        open
        onOpenChange={vi.fn()}
        customFields={[SENIORITY]}
        initial={personToFormValues({ ...BASE_PERSON, custom_fields: { seniority: 'Senior' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      nome: 'Mario',
      cognome: 'Rossi',
      custom_fields: { seniority: 'Senior' },
    })
  })

  /**
   * The defect this guards, identical in shape to `CustomerForm`'s own
   * (reproduced live there first -- see task-6-report.md): archive a field
   * definition while a record still carries a value for it, open Modifica,
   * press Salva with nothing else changed. A flat form state would re-derive
   * "is this key custom?" from the *active* schema, reclassify the value as
   * native, and 422 on `PersonUpdate`'s `extra="forbid"`.
   */
  it('neither sends an archived field as a native column nor drops its stored value', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <PersonForm
        title="Modifica persona"
        open
        onOpenChange={vi.fn()}
        // The definition was archived: nothing in `customFields` renders it, but
        // the record still carries the value.
        customFields={[]}
        initial={personToFormValues({ ...BASE_PERSON, custom_fields: { seniority: 'Senior' } })}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload).not.toHaveProperty('seniority')
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({ nome: 'Mario', cognome: 'Rossi', custom_fields: {} })
  })

  it('sends an untouched checkbox as false on create, never as an absent key', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <PersonForm
        title="Nuova persona"
        open
        onOpenChange={vi.fn()}
        customFields={[DISPONIBILE]}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText(/^Nome/), 'Luigi')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({ nome: 'Luigi', custom_fields: { disponibile: false } })
  })

  /** The other side of the same rule (see `DynamicForm.test.tsx`): doing this on
   *  edit would persist `disponibile: false` on a record that never had a value
   *  for it, as a side effect of touching Telefono alone. */
  it('does not backfill an untouched checkbox on edit, whatever else changed', async () => {
    const onSubmit = vi.fn()
    renderWithClient(
      <PersonForm
        title="Modifica persona"
        open
        onOpenChange={vi.fn()}
        customFields={[DISPONIBILE]}
        initial={personToFormValues(BASE_PERSON)}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.type(screen.getByLabelText('Telefono'), '02123456')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    const payload = submitted(onSubmit)
    expect(payload.custom_fields).toEqual({})
    expect(payload).toEqual({
      nome: 'Mario',
      cognome: 'Rossi',
      telefono: '02123456',
      custom_fields: {},
    })
  })

  it('clears a custom field the user emptied with null, and a native one with an empty string', async () => {
    const onSubmit = vi.fn()
    const initial = personToFormValues({
      ...BASE_PERSON,
      telefono: '02123456',
      custom_fields: { seniority: 'Senior' },
    })
    renderWithClient(
      <PersonForm
        title="Modifica persona"
        open
        onOpenChange={vi.fn()}
        customFields={[SENIORITY]}
        initial={initial}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.clear(screen.getByLabelText('Telefono'))
    // Two comboboxes exist in this render: index 0 is the "Azienda (cliente)" picker every
    // PersonForm renders unconditionally, index 1 is the Seniority custom field.
    const [, seniorityCombobox] = screen.getAllByRole('combobox')
    await userEvent.click(seniorityCombobox!)
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(submitted(onSubmit)).toEqual({
      nome: 'Mario',
      cognome: 'Rossi',
      telefono: '',
      custom_fields: { seniority: null },
    })
  })

  describe('customer association', () => {
    /**
     * A fix-round defect, back-ported from `DealForm.tsx`'s identical fix:
     * `useCustomers({limit: 200})` used to run at `PersonForm`'s own top level,
     * unconditionally -- and this form is mounted by both the Persone list and
     * every person detail page regardless of `open` (only the dialog's own
     * visibility toggles on it), so every view of either screen fired a
     * `GET /api/customers?limit=200` for a dropdown nobody had opened. The
     * picker is now its own component (`CustomerPicker`), mounted -- and
     * therefore only ever calling `useCustomers` -- inside `DialogContent`,
     * which Radix does not render at all while `open` is false.
     */
    it('does not fetch the customer list while the dialog is closed', () => {
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open={false}
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues(BASE_PERSON)}
          onSubmit={vi.fn()}
        />,
      )
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('does not send customer_id or detach when there was never a customer to begin with', async () => {
      const onSubmit = vi.fn()
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues(BASE_PERSON)}
          onSubmit={onSubmit}
        />,
      )

      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      const payload = submitted(onSubmit)
      expect(payload).not.toHaveProperty('customer_id')
      expect(payload).not.toHaveProperty('detach')
    })

    it('sends a chosen customer_id on create', async () => {
      mockGet.mockReturnValue(customerPage([{ id: 'cust-1', ragione_sociale: 'ACME Srl' }]))
      const onSubmit = vi.fn()
      renderWithClient(
        <PersonForm
          title="Nuova persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          onSubmit={onSubmit}
        />,
      )

      await userEvent.type(screen.getByLabelText(/^Nome/), 'Luigi')
      await userEvent.click(screen.getByRole('combobox'))
      await userEvent.click(await screen.findByRole('option', { name: 'ACME Srl' }))
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      expect(submitted(onSubmit)).toEqual({
        nome: 'Luigi',
        customer_id: 'cust-1',
        custom_fields: {},
      })
    })

    /**
     * The bug the brief's own sample would have shipped, found by reading
     * `PersonUpdate`/`PersonService.update` rather than assumed from Customer's
     * shape (Customer has no such relationship to generalise from): there is no
     * way to clear `customer_id` with `null` (`model_dump(exclude_none=True)`
     * drops it before the service ever sees it) or `""` (fails UUID parsing,
     * 422). Only an explicit `detach: true` removes an existing association.
     * Selecting "Nessun cliente" on a person who had one must send that flag --
     * omitting the key, or sending a value the server silently ignores or
     * rejects, both leave the stale association in place.
     */
    it('detaches an existing customer with `detach: true`, never `customer_id: null`', async () => {
      mockGet.mockReturnValue(customerPage([{ id: 'cust-1', ragione_sociale: 'ACME Srl' }]))
      const onSubmit = vi.fn()
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues({ ...BASE_PERSON, customer_id: 'cust-1' })}
          onSubmit={onSubmit}
        />,
      )

      await userEvent.click(await screen.findByRole('combobox'))
      await userEvent.click(await screen.findByRole('option', { name: 'Nessun cliente' }))
      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      const payload = submitted(onSubmit)
      expect(payload.detach).toBe(true)
      expect(payload).not.toHaveProperty('customer_id')
    })

    it('keeps sending the same customer_id when the association is untouched on edit', async () => {
      mockGet.mockReturnValue(customerPage([{ id: 'cust-1', ragione_sociale: 'ACME Srl' }]))
      const onSubmit = vi.fn()
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues({ ...BASE_PERSON, customer_id: 'cust-1' })}
          onSubmit={onSubmit}
        />,
      )

      await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

      const payload = submitted(onSubmit)
      expect(payload.customer_id).toBe('cust-1')
      expect(payload).not.toHaveProperty('detach')
    })
  })

  /**
   * Spec 4.4 and 11. A newly known address is picked up by the *next* cycle -- the sync
   * splits the roster into established addresses (searched from the watermark) and
   * fresh ones (searched back over `gmail_backfill_days`), and only then records them
   * as seen. Saying so beats letting somebody discover it by refreshing an empty tab.
   */
  describe('the Gmail sync notice', () => {
    const NOTICE = /le conversazioni con questo indirizzo compariranno al prossimo sync/i

    it('tells the user when a newly added address will start showing conversations', async () => {
      mockGmail(true)
      renderWithClient(
        <PersonForm
          title="Nuova persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          onSubmit={vi.fn()}
        />,
      )

      expect(screen.queryByText(NOTICE)).not.toBeInTheDocument()
      await userEvent.type(screen.getByLabelText('Email'), 'ada@acme.it')
      expect(await screen.findByText(NOTICE)).toBeInTheDocument()
    })

    /**
     * The promise is only true where a cycle will actually run. With no mailbox
     * connected -- or one whose consent was revoked, or which never got the read scope
     * -- "al prossimo sync" describes an event that is not going to happen, and a CRM
     * that says it anyway has told the user something false about its own behaviour.
     */
    it('does not promise a sync that is not going to run', async () => {
      mockGmail(false)
      renderWithClient(
        <PersonForm
          title="Nuova persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          onSubmit={vi.fn()}
        />,
      )

      await userEvent.type(screen.getByLabelText('Email'), 'ada@acme.it')
      expect(screen.queryByText(NOTICE)).not.toBeInTheDocument()
    })

    /**
     * Nothing changed, so nothing is pending: an address already in the roster is
     * already being searched from the watermark, and repeating "at the next sync" on
     * every edit would train the reader to ignore the one time it matters.
     */
    it('says nothing when an existing address was not touched', async () => {
      mockGmail(true)
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues({ ...BASE_PERSON, email: 'ada@acme.it' })}
          onSubmit={vi.fn()}
        />,
      )

      expect(await screen.findByDisplayValue('ada@acme.it')).toBeInTheDocument()
      expect(screen.queryByText(NOTICE)).not.toBeInTheDocument()
    })

    it('says it again when an existing address is changed to a different one', async () => {
      mockGmail(true)
      renderWithClient(
        <PersonForm
          title="Modifica persona"
          open
          onOpenChange={vi.fn()}
          customFields={[]}
          initial={personToFormValues({ ...BASE_PERSON, email: 'ada@acme.it' })}
          onSubmit={vi.fn()}
        />,
      )

      await userEvent.type(await screen.findByLabelText('Email'), '.uk')
      expect(await screen.findByText(NOTICE)).toBeInTheDocument()
    })
  })
})
