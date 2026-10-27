import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import type { DuplicateList, DuplicatePair, DuplicatePerson, MergeResult } from '@/lib/api'
import { AdminDuplicati } from './Duplicati'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function person(id: string, nome: string, cognome: string, extra: Partial<DuplicatePerson> = {}): DuplicatePerson {
  return {
    freelancer_id: id,
    user_id: `u-${id}`,
    nome,
    cognome,
    email: `${nome.toLowerCase()}@${id}.it`,
    created_at: '2026-09-10T10:00:00Z',
    cv_uploaded_at: null,
    ha_cv: false,
    ...extra,
  }
}

/** The newer CV is on Grace's card, so she stays; Ada's is older. */
const LINKED: DuplicatePair = {
  survivor: person('f2', 'Grace', 'Hopper', { ha_cv: true, cv_uploaded_at: '2026-10-02T09:00:00Z' }),
  assorbito: person('f1', 'Ada', 'Lovelace', { ha_cv: true, cv_uploaded_at: '2026-09-11T09:00:00Z' }),
  segnali: ['linkedin'],
  unibile: true,
}
const SAME_NAME: DuplicatePair = {
  survivor: person('f4', 'Marco', 'Rossi'),
  assorbito: person('f3', 'Marco', 'Rossi'),
  segnali: ['nome', 'telefono'],
  unibile: false,
}
const BOTH: DuplicateList = { unibili: [LINKED], da_rivedere: [SAME_NAME], iscrizioni: 0 }
const NONE: DuplicateList = { unibili: [], da_rivedere: [], iscrizioni: 0 }

const MERGED: MergeResult = {
  survivor_freelancer_id: 'f2',
  absorbed_freelancer_id: 'f1',
  action_id: 'act-1',
  moved: { logins: 3, comments: 2, matches: 1 },
  lasciati: {},
  cv: 'invariato',
  scheda_da_rigenerare: true,
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: '/admin/talent/duplicates', component: AdminDuplicati })
  const talent = createRoute({ getParentRoute: () => root, path: '/admin/talent', component: () => <p>talenti</p> })
  const card = createRoute({ getParentRoute: () => root, path: '/admin/freelance/$id', component: () => <p>scheda</p> })
  const router = createRouter({
    routeTree: root.addChildren([page, talent, card]),
    history: createMemoryHistory({ initialEntries: ['/admin/talent/duplicates'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

/** The API of this page: the pairs (a function, so a merge can change them) and the merge. */
function serve(
  list: () => DuplicateList,
  merge: (url: string, body: { da: string }) => Response = () => answer(200, MERGED),
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url === '/api/hub/talent/duplicati') return answer(200, list())
    if (url.endsWith('/merge') && init?.method === 'POST') return merge(url, JSON.parse(init.body as string))
    throw new Error(`unhandled fetch in this test: ${init?.method ?? 'GET'} ${url}`)
  })
}

function mergeCalls(spy: MockInstance<typeof fetch>) {
  return spy.mock.calls.filter(([, init]) => init?.method === 'POST')
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('«Duplicati» (REB-800)', () => {
  it('lists each pair with both people side by side, which one stays, and the CV of each', async () => {
    serve(() => BOTH)
    mount()
    const pair = (await screen.findByRole('region', { name: 'Stessa persona' })).querySelector('li')!
    expect(within(pair).getByText('Stesso profilo LinkedIn')).toBeInTheDocument()
    expect(within(pair).getByRole('link', { name: 'Grace Hopper' })).toHaveAttribute('href', '/admin/freelance/f2')
    expect(within(pair).getByRole('link', { name: 'Ada Lovelace' })).toHaveAttribute('href', '/admin/freelance/f1')
    expect(within(pair).getByText('grace@f2.it')).toBeInTheDocument()
    expect(within(pair).getByText('ada@f1.it')).toBeInTheDocument()
    expect(within(pair).getByText('CV caricato il 2 ott 2026')).toBeInTheDocument()
    expect(within(pair).getByText('CV caricato il 11 set 2026')).toBeInTheDocument()
    expect(within(pair).getByText('Resta')).toBeInTheDocument()
    expect(within(pair).getByText('Viene nascosta')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('2')
  })

  it('says a card with no CV has none', async () => {
    serve(() => ({ ...BOTH, da_rivedere: [] }))
    mount()
    const pair = (await screen.findByRole('region', { name: 'Stessa persona' })).querySelector('li')!
    expect(within(pair).queryByText('Nessun CV')).toBeNull()
    cleanup()
    serve(() => ({ ...NONE, unibili: [{ ...LINKED, assorbito: person('f1', 'Ada', 'Lovelace') }] }))
    mount()
    expect(await screen.findByText('Nessun CV')).toBeInTheDocument()
  })

  it('lists the pairs that share only a name or a number apart, as «Da guardare», with the question', async () => {
    serve(() => BOTH)
    mount()
    const look = await screen.findByRole('region', { name: 'Da guardare' })
    expect(within(look).getByText('Stesso nome')).toBeInTheDocument()
    expect(within(look).getByText('Stesso telefono')).toBeInTheDocument()
    expect(within(look).getByText('Sono la stessa persona?')).toBeInTheDocument()
    expect(within(look).getAllByRole('link', { name: 'Marco Rossi' })).toHaveLength(2)
    const sure = screen.getByRole('region', { name: 'Stessa persona' })
    expect(within(sure).queryByText('Sono la stessa persona?')).toBeNull()
  })

  it('says there is no duplicate when there is none', async () => {
    serve(() => NONE)
    mount()
    expect(await screen.findByText('Nessun duplicato.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Unisci/ })).toBeNull()
  })

  it('says how many sign-ups repeat a profile and that there is nothing to merge in them', async () => {
    serve(() => ({ ...BOTH, iscrizioni: 2 }))
    mount()
    expect(await screen.findByText(/2 iscrizioni ripetono un profilo già in lista/)).toBeInTheDocument()
  })

  it('says when the pairs cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount()
    expect(await screen.findByText('Non riesco a leggere i duplicati.')).toBeInTheDocument()
  })

  it('asks before it merges, says what moves and that undoing is the shell, and writes nothing on «Annulla»', async () => {
    const spy = serve(() => BOTH)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Grace Hopper e Ada Lovelace' }))
    const dialog = await screen.findByRole('dialog', { name: 'Unire le due schede?' })
    expect(dialog).toHaveTextContent('Resta Grace Hopper (grace@f2.it). Viene nascosta Ada Lovelace (ada@f1.it).')
    expect(dialog).toHaveTextContent('Passano alla scheda che resta')
    expect(dialog).toHaveTextContent('L’altra scheda è nascosta, non cancellata.')
    expect(dialog).toHaveTextContent('Da qui non si annulla')
    expect(dialog).toHaveTextContent('rebase talent-duplicates --undo')
    expect(mergeCalls(spy)).toHaveLength(0)

    await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(mergeCalls(spy)).toHaveLength(0)
  })

  it('merges the absorbed card into the survivor, says what moved, and reads the list again', async () => {
    let merged = false
    const spy = serve(
      () => (merged ? NONE : BOTH),
      () => {
        merged = true
        return answer(200, MERGED)
      },
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Grace Hopper e Ada Lovelace' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Conferma e unisci' }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Unite: 6 righe spostate. La scheda anonima va riscritta.')
    expect(status).toHaveTextContent('rebase talent-duplicates --undo act-1 --admin EMAIL')
    expect(mergeCalls(spy)).toHaveLength(1)
    expect(mergeCalls(spy)[0]).toEqual([
      '/api/hub/freelancers/f2/merge',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ da: 'f1' }) }),
    ])
    expect(await screen.findByText('Nessun duplicato.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the other card instead when the admin turns the pair round', async () => {
    const spy = serve(() => BOTH)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Grace Hopper e Ada Lovelace' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Tieni l’altra scheda' }))
    expect(dialog).toHaveTextContent('Resta Ada Lovelace (ada@f1.it). Viene nascosta Grace Hopper (grace@f2.it).')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Conferma e unisci' }))
    await screen.findByRole('status')
    expect(mergeCalls(spy)[0]).toEqual([
      '/api/hub/freelancers/f1/merge',
      expect.objectContaining({ body: JSON.stringify({ da: 'f2' }) }),
    ])
  })

  it('shows the API\'s sentence as it is when it refuses, and leaves the dialog and the list as they were', async () => {
    const sentence = 'Le due schede hanno un contratto quadro vivo: unirle li confonderebbe.'
    serve(
      () => BOTH,
      () => answer(409, { detail: sentence }),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Grace Hopper e Ada Lovelace' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Conferma e unisci' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(sentence)
    expect(screen.queryByRole('status')).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Conferma e unisci' })).toBeEnabled()
  })

  it('says it could not merge when the call itself fails', async () => {
    serve(() => BOTH, () => {
      throw new TypeError('Failed to fetch')
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Grace Hopper e Ada Lovelace' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Conferma e unisci' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Non riesco a unire le schede.')
  })

  it('words the merge of a «Da guardare» pair as a question', async () => {
    const spy = serve(() => BOTH)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Unisci Marco Rossi e Marco Rossi' }))
    const dialog = await screen.findByRole('dialog', { name: 'Sono la stessa persona?' })
    expect(dialog).toHaveTextContent('Hanno in comune solo il nome o il telefono')
    expect(dialog).toHaveTextContent('L’altra scheda è nascosta, non cancellata.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Conferma e unisci' }))
    await screen.findByRole('status')
    expect(mergeCalls(spy)[0]![0]).toBe('/api/hub/freelancers/f4/merge')
  })
})
