import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { PeriodsPanel } from './PeriodsPanel'

/**
 * The brief wrote these against `msw`. It is not a dependency of this package, and
 * adding it would not help anyway: `lib/api.ts` builds one openapi-fetch client at
 * import time, so a handler installed on `globalThis.fetch` afterwards intercepts
 * nothing. Spying on the client's own verbs is what every other query-backed test in
 * this codebase does (`features/costs/queries.test.tsx`, `EmitterPanel.test.tsx`), and
 * it is what actually runs the panel's real hooks.
 */
const mockGet = vi.spyOn(api, 'GET')
const mockPost = vi.spyOn(api, 'POST')
const mockDelete = vi.spyOn(api, 'DELETE')
// Restored, not merely reset: a `window.confirm` left stubbed by one test would answer
// for the next one silently, and the tests here disagree on purpose about its answer.
const mockConfirm = vi.spyOn(window, 'confirm')

afterEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
  mockDelete.mockReset()
  mockConfirm.mockReset()
})

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

/** Routes by path, because the panel reads two endpoints at once and a single
 *  `mockResolvedValue` would answer both with the same document. */
function respond(routes: Record<string, () => Promise<never>>) {
  mockGet.mockImplementation((path: string) => {
    const route = routes[path]
    if (!route) throw new Error(`unexpected GET ${path}`)
    return route()
  })
}

const LOCK = { anno: 2026, mese: 3, chiuso_il: '2026-04-02T10:00:00Z', chiuso_da: 'u1' }
const IVAN = {
  id: 'u1',
  nome: 'Ivan',
  email: 'i@x.test',
  ruolo: 'admin',
  attivo: true,
  tariffa_oraria_default: null,
  costo_orario_default: null,
  created_at: '2026-01-01T00:00:00Z',
}

/** The row's one action lives behind the «⋯» menu since the 2026-09-08 revision
 *  (design spec §4); the trigger is labelled per month so a list of them is unambiguous.
 *  `findByRole` already waits on the right thing -- the trigger only exists once
 *  `usePeriodLocks()` resolves and the row renders, so there is no earlier signal to
 *  wait on instead. What is not enough is the default one-second `asyncUtilTimeout`: on
 *  a loaded full `pnpm --filter web test` run this lost the race (REB-405), the same
 *  shape `vite.config.ts`'s own `testTimeout: 20_000` comment already describes for the
 *  suite as a whole. Matching that headroom here. */
async function openRowMenu(mese: string) {
  await userEvent.click(
    await screen.findByRole('button', { name: `Azioni per ${mese}` }, { timeout: 10_000 }),
  )
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PeriodsPanel />
    </QueryClientProvider>,
  )
}

describe('PeriodsPanel', () => {
  it('lists the closed months with who closed them and when', async () => {
    respond({
      '/api/period-locks': () => ok([LOCK]),
      '/api/users': () => ok([IVAN]),
    })
    renderPanel()
    expect(await screen.findByText(/marzo 2026/i)).toBeInTheDocument()
    expect(await screen.findByText(/Ivan/)).toBeInTheDocument()
  })

  it('says plainly that closing is optional', async () => {
    respond({ '/api/period-locks': () => ok([]), '/api/users': () => ok([]) })
    renderPanel()
    // §6.4's deliberate property: somebody who closes nothing gets the previous
    // behaviour, and no screen demands a ritual before it works.
    expect(await screen.findByText(/chiudere un periodo non è obbligatorio/i)).toBeInTheDocument()
  })

  it('asks for confirmation before reopening, and shows the server error if it fails', async () => {
    respond({
      '/api/period-locks': () => ok([{ ...LOCK, chiuso_da: null }]),
      '/api/users': () => ok([]),
    })
    mockConfirm.mockReturnValue(true)
    mockDelete.mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/permission_denied',
          title: 'Permesso negato',
          status: 403,
          detail: 'reopen_period requires one of [admin], actor has collaboratore',
          code: 'permission_denied',
          instance: '/api/period-locks/2026/3',
        },
        403,
      ),
    )
    renderPanel()
    await openRowMenu('marzo 2026')
    await userEvent.click(screen.getByRole('menuitem', { name: /riapri/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/requires one of/i))
  })

  it('does not reopen anything when the confirmation is dismissed', async () => {
    respond({ '/api/period-locks': () => ok([LOCK]), '/api/users': () => ok([]) })
    mockDelete.mockImplementation(() => ok(undefined, 204))
    // A closed period is an audited state and reopening it writes an activity, so a
    // single misclick must not do it. The guard is the caller's own `window.confirm`,
    // the house pattern for a destructive row action (`PipelinePanel`), rather than a
    // second button that used to swap itself into the row.
    mockConfirm.mockReturnValue(false)
    renderPanel()
    await openRowMenu('marzo 2026')
    await userEvent.click(screen.getByRole('menuitem', { name: /riapri/i }))
    expect(mockConfirm).toHaveBeenCalled()
    expect(mockDelete).not.toHaveBeenCalled()
  })

  it('reopens the month through the real endpoint once confirmed', async () => {
    respond({ '/api/period-locks': () => ok([LOCK]), '/api/users': () => ok([]) })
    mockDelete.mockImplementation(() => ok(undefined, 204))
    mockConfirm.mockReturnValue(true)
    renderPanel()
    await openRowMenu('marzo 2026')
    await userEvent.click(screen.getByRole('menuitem', { name: /riapri/i }))
    await waitFor(() =>
      expect(mockDelete).toHaveBeenCalledWith('/api/period-locks/{anno}/{mese}', {
        params: { path: { anno: 2026, mese: 3 } },
      }),
    )
  })

  /** Marked destructive: reopening unlocks a month whose numbers have already been
   *  reported, which is the one thing this panel exists to prevent by accident. */
  it('marks the reopen item as the destructive one', async () => {
    respond({ '/api/period-locks': () => ok([LOCK]), '/api/users': () => ok([]) })
    renderPanel()
    await openRowMenu('marzo 2026')
    expect(screen.getByRole('menuitem', { name: /riapri/i })).toHaveAttribute(
      'data-variant',
      'destructive',
    )
  })

  it('closes the month picked in the form', async () => {
    respond({ '/api/period-locks': () => ok([]), '/api/users': () => ok([]) })
    mockPost.mockImplementation(() => ok(LOCK, 201))
    renderPanel()
    const picker = await screen.findByLabelText(/chiudi il mese/i)
    await userEvent.clear(picker)
    await userEvent.type(picker, '2026-03')
    await userEvent.click(screen.getByRole('button', { name: /chiudi periodo/i }))
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/period-locks', {
        body: { anno: 2026, mese: 3 },
      }),
    )
  })

  it('renders a banner, never an empty list, when the request fails', async () => {
    respond({
      '/api/period-locks': () => failed({ detail: 'Boom' }, 500),
      '/api/users': () => ok([]),
    })
    renderPanel()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(/nessun periodo chiuso/i)).not.toBeInTheDocument()
  })
})
