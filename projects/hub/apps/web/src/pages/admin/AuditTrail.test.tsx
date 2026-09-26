import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AdminAction } from '@/lib/api'
import { AuditTrail } from './AuditTrail'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const OVERRIDE: AdminAction = {
  id: 'a2',
  entity_type: 'freelancer',
  entity_id: 'f1',
  kind: 'overridden',
  admin_id: 'u1',
  admin_nome: 'Ivan Bianchi',
  payload: {
    changed: ['tariffa_giornaliera'],
    before: { tariffa_giornaliera: null },
    after: { tariffa_giornaliera: '500.00' },
  },
  created_at: '2026-09-22T15:30:00Z',
}

const DELETE: AdminAction = {
  id: 'a1',
  entity_type: 'freelancer',
  entity_id: 'f1',
  kind: 'deleted',
  admin_id: 'u1',
  admin_nome: 'Ivan Bianchi',
  payload: {},
  created_at: '2026-09-20T09:00:00Z',
}

// `FreelancerService.clear_cv`'s own payload shape (freelancers.py): `changed` names
// the symbolic field "cv", but `before`/`after` are keyed by the three metadata
// columns instead -- the mismatch that made the diff render as empty before it read
// off `before`/`after`'s own keys.
const CLEARED: AdminAction = {
  id: 'a3',
  entity_type: 'freelancer',
  entity_id: 'f1',
  kind: 'cleared',
  admin_id: 'u1',
  admin_nome: 'Ivan Bianchi',
  payload: {
    changed: ['cv'],
    before: { cv_filename: 'cv.pdf', cv_mime: 'application/pdf', cv_size: 2048 },
    after: { cv_filename: null, cv_mime: null, cv_size: null },
  },
  created_at: '2026-09-21T10:00:00Z',
}

// A framework agreement's own action, recorded on the freelancer's own trail
// (REB-407): its `kind` alone is the whole story, exactly like `deleted`/`restored`.
const NOTICE_RECORDED: AdminAction = {
  id: 'a4',
  entity_type: 'freelancer',
  entity_id: 'f1',
  kind: 'notice_recorded',
  admin_id: 'u1',
  admin_nome: 'Ivan Bianchi',
  payload: {},
  created_at: '2026-09-24T09:00:00Z',
}

// «Riprova su Pigro» (REB-498), recorded on entity `match`: its payload is the outcome and
// the CRM's sentence, not a diff, so the kind alone names it.
const PIGRO_LINK: AdminAction = {
  id: 'a5',
  entity_type: 'match',
  entity_id: 'm1',
  kind: 'pigro_link',
  admin_id: 'u1',
  admin_nome: 'Ivan Bianchi',
  payload: { esito: 'errore', errore: 'Pigro non risponde.' },
  created_at: '2026-10-01T09:05:00Z',
}

function mount({ canRevert = true, onReverted = () => {} }: { canRevert?: boolean; onReverted?: () => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AuditTrail kind="freelancers" id="f1" canRevert={canRevert} onReverted={onReverted} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('AuditTrail', () => {
  it('shows who overrode what, when, and the before/after diff, newest first', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [OVERRIDE, DELETE]))
    mount()
    await screen.findByText('Modifica')
    const entries = screen.getAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent('Modifica')
    expect(entries[0]).toHaveTextContent('Tariffa a giornata')
    expect(entries[0]).toHaveTextContent('—')
    expect(entries[0]).toHaveTextContent('500.00')
    expect(entries[1]).toHaveTextContent('Eliminazione')
  })

  it('shows the CV that was there before a clear, even though `changed` names a field absent from before/after', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [CLEARED]))
    mount()
    const entry = await screen.findByText('CV rimosso')
    expect(entry.closest('li')).toHaveTextContent('cv.pdf')
  })

  it('shows a delete or a restore with its kind alone, no diff and no revert button', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [DELETE]))
    mount()
    await screen.findByText('Eliminazione')
    expect(screen.queryByRole('button', { name: 'Ripristina questa modifica' })).toBeNull()
  })

  it('shows a framework agreement’s own action (mail resent, cancelled, a notice) with its kind alone (REB-407)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [NOTICE_RECORDED]))
    mount()
    await screen.findByText('Disdetta registrata')
    expect(screen.queryByRole('button', { name: 'Ripristina questa modifica' })).toBeNull()
  })

  it('names a retried link to Pigro, with no diff and no revert button (REB-502)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [PIGRO_LINK]))
    mount()
    const entry = (await screen.findByText('Collegamento a Pigro')).closest('li')!
    expect(entry).toHaveTextContent('Ivan Bianchi')
    expect(entry.querySelector('dl')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ripristina questa modifica' })).toBeNull()
  })

  it('says so when there is nothing yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, []))
    mount()
    expect(await screen.findByText('Nessuna modifica registrata.')).toBeInTheDocument()
  })

  it('shows a sentence when the trail cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('boom', { status: 500 }))
    mount()
    expect(await screen.findByText('Non riesco a leggere il registro delle modifiche.')).toBeInTheDocument()
  })

  it('hides the revert button while the record itself is deleted, even on an overridden entry', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, [OVERRIDE]))
    mount({ canRevert: false })
    await screen.findByText('Tariffa a giornata')
    expect(screen.queryByRole('button', { name: 'Ripristina questa modifica' })).toBeNull()
  })

  it('reverts an overridden entry and tells the page to refetch the record', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') return answer(200, {})
      return answer(200, [OVERRIDE])
    })
    const onReverted = vi.fn()
    mount({ onReverted })
    await userEvent.click(await screen.findByRole('button', { name: 'Ripristina questa modifica' }))
    await waitFor(() => expect(onReverted).toHaveBeenCalledTimes(1))
    const [url, init] = spy.mock.calls.find((call) => call[1]?.method === 'POST')!
    expect(url).toBe('/api/hub/freelancers/f1/audit/a2/revert')
    expect(init?.method).toBe('POST')
  })

  it('shows the server sentence when a revert is refused', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') return answer(422, { detail: 'si può ripristinare solo una modifica di campo' })
      return answer(200, [OVERRIDE])
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Ripristina questa modifica' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('si può ripristinare solo una modifica di campo')
  })
})
