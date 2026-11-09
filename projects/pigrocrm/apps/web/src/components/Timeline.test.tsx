import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Timeline } from './Timeline'
import { api } from '@/lib/api'
import type { components } from '@/lib/api-types'

type Entry = components['schemas']['ActivityRead']

// `api.GET` is spied on directly (not `vi.mock('@/lib/api', ...)`) so `unwrap` and
// `toProblem` stay the real implementation: what is under test here is Timeline's
// own handling of what they produce (a resolved list, a thrown `ProblemDetail`),
// not a reimplementation of either.
// Typed as a plain mock: `spyOn` types it against the union of every GET of the API, so what it
// accepts changes with each route added (REB-971 added three), and this test is about the
// timeline's handling of what the spy returns.
const mockGet = vi.spyOn(api, 'GET') as unknown as ReturnType<typeof vi.fn>

beforeEach(() => {
  mockGet.mockReset()
})

function ok(data: Entry[]) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) })
}

function renderWithClient(ui: ReactElement) {
  // `retry: false`: a deliberately-failing test below would otherwise wait out
  // queryClient's real retry/backoff policy (lib/query.ts) before `isError` ever
  // turns true.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

function entry(overrides: Partial<Entry>): Entry {
  return {
    id: crypto.randomUUID(),
    entity_type: 'customer',
    entity_id: crypto.randomUUID(),
    kind: 'created',
    actor_id: crypto.randomUUID(),
    actor_name: null,
    actor_type: 'user',
    triggered_by_name: null,
    payload: {},
    occurred_at: '2026-08-07T20:26:33.337592Z',
    ...overrides,
  }
}

describe('Timeline', () => {
  it('shows a distinguishable loading state before the first response arrives', () => {
    mockGet.mockReturnValue(new Promise(() => {})) // never resolves within this test
    renderWithClient(<Timeline entityType="customer" entityId="c1" />)
    expect(screen.getByRole('status', { name: 'Caricamento timeline' })).toBeInTheDocument()
  })

  it('shows an honest empty state once loading really has finished with zero entries', async () => {
    mockGet.mockReturnValue(ok([]))
    renderWithClient(<Timeline entityType="customer" entityId="c1" />)
    expect(await screen.findByText('Nessuna attività registrata.')).toBeInTheDocument()
  })

  it('shows the API problem detail, not the empty-state message, when the request fails', async () => {
    // `api.GET` is one overloaded function covering every GET path in the generated
    // `paths` type (lib/api-types.ts), and adding new paths (slice 2's documents,
    // templates, emitter) shifts which overload TS resolves for an unnarrowed mock
    // return value -- the same fragility `deals/queries.test.tsx`'s own `ok`/`failed`
    // helpers document. The cast says so honestly rather than fighting the overload
    // set to make TS re-derive it.
    mockGet.mockReturnValue(
      Promise.resolve({
        error: {
          type: 'about:blank',
          title: 'Non trovato',
          status: 404,
          detail: 'Cliente non trovato',
          code: 'not_found',
        },
        response: new Response(null, { status: 404 }),
      }) as never,
    )
    renderWithClient(<Timeline entityType="customer" entityId="c1" />)
    expect(await screen.findByText('Cliente non trovato')).toBeInTheDocument()
    expect(screen.queryByText('Nessuna attività registrata.')).not.toBeInTheDocument()
  })

  it('turns a raw network failure into the same generic Italian message every other caller of unwrap gets', async () => {
    mockGet.mockRejectedValue(new TypeError('Failed to fetch'))
    renderWithClient(<Timeline entityType="customer" entityId="c1" />)
    expect(await screen.findByText(/si è verificato un errore imprevisto/i)).toBeInTheDocument()
  })

  describe('per-entity path resolution -- the type-safe replacement for the two `as never` casts', () => {
    it('calls the customer timeline path with customer_id and the default limit', async () => {
      mockGet.mockReturnValue(ok([]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      await screen.findByText('Nessuna attività registrata.')
      expect(mockGet).toHaveBeenCalledExactlyOnceWith('/api/customers/{customer_id}/timeline', {
        params: { path: { customer_id: 'c1' }, query: { limit: 50 } },
      })
    })

    it('calls the person timeline path with person_id', async () => {
      mockGet.mockReturnValue(ok([]))
      renderWithClient(<Timeline entityType="person" entityId="p1" />)
      await screen.findByText('Nessuna attività registrata.')
      expect(mockGet).toHaveBeenCalledExactlyOnceWith('/api/people/{person_id}/timeline', {
        params: { path: { person_id: 'p1' }, query: { limit: 50 } },
      })
    })

    it('calls the deal timeline path with deal_id', async () => {
      mockGet.mockReturnValue(ok([]))
      renderWithClient(<Timeline entityType="deal" entityId="d1" />)
      await screen.findByText('Nessuna attività registrata.')
      expect(mockGet).toHaveBeenCalledExactlyOnceWith('/api/deals/{deal_id}/timeline', {
        params: { path: { deal_id: 'd1' }, query: { limit: 50 } },
      })
    })

    it('forwards a caller-supplied limit instead of the default 50', async () => {
      mockGet.mockReturnValue(ok([]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" limit={5} />)
      await screen.findByText('Nessuna attività registrata.')
      expect(mockGet).toHaveBeenCalledExactlyOnceWith('/api/customers/{customer_id}/timeline', {
        params: { path: { customer_id: 'c1' }, query: { limit: 5 } },
      })
    })
  })

  describe('actor_type -- visible and obvious, not a subtle icon difference', () => {
    it('falls back to "Utente" for a browser-session actor whose name is unknown', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'user' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Utente')).toBeInTheDocument()
    })

    it('falls back to "Agente AI" for a personal-access-token (MCP) actor whose name is unknown, distinctly from a user', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'mcp' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Agente AI')).toBeInTheDocument()
      expect(screen.queryByText('Utente')).not.toBeInTheDocument()
    })

    it('labels an actor-less bootstrap action "Sistema"', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'system', actor_id: null })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Sistema')).toBeInTheDocument()
    })

    it('shows an honest, humanized label for an actor_type this build has never seen, instead of crashing', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'webhook' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Webhook')).toBeInTheDocument()
    })
  })

  describe('kind -- an unrecognised value never crashes and never renders undefined', () => {
    it.each([
      ['created', 'Creato'],
      ['updated', 'Modificato'],
      ['deleted', 'Archiviato'],
      ['restored', 'Ripristinato'],
      ['stage_changed', 'Cambio stato'],
      // Slice 9's import. Without a label of its own it read as "Imported" -- an English
      // word on an otherwise Italian timeline -- and the label has to say what happened
      // without naming the tool the document came out of.
      ['imported', 'Fattura importata'],
    ])('labels kind "%s" as "%s"', async (kind, label) => {
      mockGet.mockReturnValue(ok([entry({ kind })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText(label)).toBeInTheDocument()
    })

    it('humanizes a kind this build does not recognise instead of rendering it blank or as "undefined"', async () => {
      mockGet.mockReturnValue(ok([entry({ kind: 'email_received' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Email received')).toBeInTheDocument()
      expect(screen.queryByText('undefined')).not.toBeInTheDocument()
    })
  })

  describe('payload detail', () => {
    it('lists the changed fields of an "updated" entry, humanized', async () => {
      mockGet.mockReturnValue(
        ok([entry({ kind: 'updated', payload: { changed: ['note', 'phone'] } })]),
      )
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Campi modificati: Note, Telefono')).toBeInTheDocument()
    })

    it('reads a stage change by key name, not by object order -- JSONB does not preserve insertion order', async () => {
      // The real API was observed live returning {"to": ..., "from": ...} for a
      // payload the backend constructs as {"from": ..., "to": ...} (see
      // task-5-report.md) -- Postgres JSONB does not keep insertion order, so this
      // reverses the key order on purpose to prove the renderer does not assume it.
      mockGet.mockReturnValue(
        ok([entry({ kind: 'stage_changed', payload: { to: 'Contattato', from: 'Lead' } })]),
      )
      renderWithClient(<Timeline entityType="deal" entityId="d1" />)
      const detail = await screen.findByText((_, element) => element?.textContent === 'Da Lead a Contattato')
      expect(detail).toBeInTheDocument()
    })

    it('prints a contract’s state change in the words it was stored with, not the English codes', async () => {
      mockGet.mockReturnValue(
        ok([entry({ kind: 'state_changed', payload: { to: 'active', from: 'draft' } })]),
      )
      renderWithClient(<Timeline entityType="contract" entityId="k1" />)
      expect(await screen.findByText('A: attivo · Da: bozza')).toBeInTheDocument()
    })

    it('names the invoice events in Italian', async () => {
      mockGet.mockReturnValue(
        ok([
          entry({ kind: 'confirmed', payload: {} }),
          entry({ kind: 'issued', payload: { year: 2026, number: 1 } }),
          entry({ kind: 'payment_state_changed', payload: { payment_status: 'paid' } }),
          entry({ kind: 'transmitted_externally', payload: {} }),
        ]),
      )
      renderWithClient(<Timeline entityType="invoice" entityId="i1" />)
      expect(await screen.findByText('Confermata')).toBeInTheDocument()
      expect(screen.getByText('Emessa')).toBeInTheDocument()
      expect(screen.getByText('Stato di pagamento cambiato')).toBeInTheDocument()
      expect(screen.getByText('Stato pagamento: incassato')).toBeInTheDocument()
      expect(screen.getByText('Trasmessa esternamente')).toBeInTheDocument()
      for (const english of ['Confirmed', 'Issued', 'Payment state changed', 'Transmitted externally']) {
        expect(screen.queryByText(english)).not.toBeInTheDocument()
      }
    })

    it('names a deal’s first stage and a customer’s custom fields in Italian', async () => {
      mockGet.mockReturnValue(
        ok([entry({ kind: 'created', payload: { name: 'Deal Walk', stage: 'Lead' } })]),
      )
      renderWithClient(<Timeline entityType="deal" entityId="d1" />)
      expect(await screen.findByText('Nome: Deal Walk · Stato: Lead')).toBeInTheDocument()
    })

    it('names the custom fields of a customer in Italian', async () => {
      mockGet.mockReturnValue(
        ok([entry({ kind: 'updated', payload: { changed: ['custom_fields', 'phone'] } })]),
      )
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(
        await screen.findByText('Campi modificati: Campi personalizzati, Telefono'),
      ).toBeInTheDocument()
    })

    it('falls back to a generic, humanized dump for a "created" entry\'s per-entity payload', async () => {
      mockGet.mockReturnValue(
        ok([entry({ kind: 'created', payload: { legal_name: 'ACME Srl' } })]),
      )
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      expect(await screen.findByText('Ragione sociale: ACME Srl')).toBeInTheDocument()
    })

    it('never echoes the import provenance, and still shows the rest of the payload', async () => {
      // The live payload of an imported invoice: `imported_from` is provenance the CRM
      // keeps for itself (invoices/models.py), so the generic dump must drop it -- the
      // "Fattura importata" label above is the whole of what a reader needs -- while the
      // year, the number and the total, which are facts about the document, stay.
      mockGet.mockReturnValue(
        ok([
          entry({
            kind: 'imported',
            payload: { year: 2026, number: 2, total: '3422.00', imported_from: 'external' },
          }),
        ]),
      )
      renderWithClient(<Timeline entityType="invoice" entityId="i1" />)
      expect(await screen.findByText('Anno: 2026 · Numero: 2 · Totale: 3422.00')).toBeInTheDocument()
      expect(screen.queryByText(/esterno/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/Importata da/i)).not.toBeInTheDocument()
    })

    it('adds no detail line when the payload holds nothing but the provenance', async () => {
      mockGet.mockReturnValue(ok([entry({ kind: 'imported', payload: { imported_from: 'external' } })]))
      renderWithClient(<Timeline entityType="invoice" entityId="i1" />)
      const item = (await screen.findByText('Fattura importata')).closest('li')
      expect(item?.querySelectorAll('p')).toHaveLength(0)
    })

    it('adds no detail line for an empty payload ("deleted"/"restored") -- the label already says everything true', async () => {
      mockGet.mockReturnValue(ok([entry({ kind: 'deleted', payload: {} })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      await screen.findByText('Archiviato')
      // Nothing beyond label, badge and timestamp: nine list items only ("Archiviato",
      // "Utente" and the formatted date/time) -- no extra <p> was added below them.
      const item = screen.getByText('Archiviato').closest('li')
      expect(item?.querySelectorAll('p')).toHaveLength(0)
    })
  })

  it('formats occurred_at as a localized Italian date and time with the seconds, from a full ISO instant', async () => {
    const occurredAt = '2026-08-07T20:26:33.337592Z'
    mockGet.mockReturnValue(ok([entry({ occurred_at: occurredAt })]))
    renderWithClient(<Timeline entityType="customer" entityId="c1" />)
    const expected = new Intl.DateTimeFormat('it-IT', { dateStyle: 'medium', timeStyle: 'medium' }).format(
      new Date(occurredAt),
    )
    const time = await screen.findByText(expected)
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('datetime', occurredAt)
  })

  describe('who and from where (REB-781)', () => {
    it('names the member and marks a browser change "dal web"', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'user', actor_name: 'Giulia Bianchi' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      const item = (await screen.findByText('Giulia Bianchi')).closest('li')
      expect(item).toHaveTextContent('dal web')
      expect(item).not.toHaveTextContent('via MCP')
    })

    it('names the token owner and marks an agent change "via MCP"', async () => {
      mockGet.mockReturnValue(ok([entry({ actor_type: 'mcp', actor_name: 'Marco Verdi' })]))
      renderWithClient(<Timeline entityType="customer" entityId="c1" />)
      const item = (await screen.findByText('Marco Verdi')).closest('li')
      expect(item).toHaveTextContent('via MCP')
      expect(item).not.toHaveTextContent('dal web')
    })

    it('says who set an automation off, and over which channel', async () => {
      mockGet.mockReturnValue(
        ok([
          entry({
            actor_type: 'system',
            actor_id: null,
            kind: 'automation.stage_moved',
            payload: { rule: 'A1', triggered_by: crypto.randomUUID(), triggered_via: 'mcp' },
            triggered_by_name: 'Marco Verdi',
          }),
        ]),
      )
      renderWithClient(<Timeline entityType="deal" entityId="d1" />)
      const item = (await screen.findByText('Sistema')).closest('li')
      expect(item).toHaveTextContent('Attivata da Marco Verdi (via MCP)')
      expect(item).toHaveTextContent('Regola: A1')
      expect(item).not.toHaveTextContent('Attivata da: ')
    })

    it('shows a note event by its label alone: no note id, no door, no earlier text', async () => {
      mockGet.mockReturnValue(
        ok([
          entry({
            kind: 'note_edited',
            actor_type: 'mcp',
            actor_name: 'Marco Verdi',
            payload: { note_id: crypto.randomUUID(), source: 'mcp' },
          }),
        ]),
      )
      renderWithClient(<Timeline entityType="deal" entityId="d1" />)
      const item = (await screen.findByText('Marco Verdi')).closest('li')
      expect(item).toHaveTextContent('Nota modificata')
      expect(item).not.toHaveTextContent(/Nota id|Fonte|prima/i)
    })
  })
})
