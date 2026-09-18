import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CostCategoriesPanel } from './CostCategoriesPanel'

/**
 * The whole client, verb by verb, exactly as `EmitterPanel.test.tsx` does it: `msw` is
 * not a dependency of this package and could not help anyway, because `lib/api.ts`
 * builds one openapi-fetch client at import time and a handler installed on
 * `globalThis.fetch` afterwards intercepts nothing. `DELETE` is mocked even though the
 * panel must never call it -- that is precisely what one of the tests below asserts.
 */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

const TIMES = { created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }

const SOFTWARE = {
  id: 'cat-2',
  nome: 'Software e licenze',
  posizione: 1,
  code: 'software_licenze',
  archiviata: false,
  ...TIMES,
}
const CONSULENZA = {
  id: 'cat-1',
  nome: 'Consulenza esterna',
  posizione: 0,
  code: 'consulenza_esterna',
  archiviata: false,
  ...TIMES,
}
/** No `code`: created by hand from this very panel, and archived later. */
const VECCHIA = {
  id: 'cat-9',
  nome: 'Vecchia voce',
  posizione: 7,
  code: null,
  archiviata: true,
  ...TIMES,
}

/**
 * Answers `GET /api/cost-categories` from the `include_archived` flag the panel
 * actually sends, rather than from a call counter: the checkbox's whole job is to
 * change that flag, and a counter would pass even if the flag never moved.
 */
function respond(active: unknown[], archived: unknown[] = []) {
  vi.mocked(api.GET).mockImplementation((path: string, ...rest: unknown[]) => {
    if (path !== '/api/cost-categories') throw new Error(`unexpected GET ${path}`)
    const init = rest[0] as { params?: { query?: { include_archived?: boolean } } } | undefined
    const includeArchived = init?.params?.query?.include_archived === true
    return ok(includeArchived ? [...active, ...archived] : active)
  })
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CostCategoriesPanel />
    </QueryClientProvider>,
  )
}

/** The `<li>` a named category lives in, reached through its own name input: the row
 *  has no test id and no heading, and every assertion about it is really an assertion
 *  about that row rather than about the list. */
function rowFor(nome: string) {
  const input = screen.getByLabelText(`Nome della categoria ${nome}`)
  const row = input.closest('li')
  if (!row) throw new Error(`no row for ${nome}`)
  return row
}

/** The row's actions live behind the «⋯» menu since the 2026-09-08 revision (design
 *  spec §4); the trigger is labelled per category so a list of them stays unambiguous. */
async function openRowMenu(nome: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Azioni per ${nome}` }))
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('CostCategoriesPanel', () => {
  it('lists the categories by stored position, with the code beside each name', async () => {
    // Deliberately handed back out of order: `posizione` is the order, not arrival.
    respond([SOFTWARE, CONSULENZA])
    renderPanel()

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2))
    const [first, second] = screen.getAllByRole('listitem')
    expect(within(first as HTMLElement).getByRole('textbox')).toHaveValue('Consulenza esterna')
    expect(within(second as HTMLElement).getByRole('textbox')).toHaveValue('Software e licenze')
    expect(screen.getByText('consulenza_esterna')).toBeInTheDocument()
  })

  /**
   * The code is what a report keys on, so it must be visible and must never be
   * editable. A category created from this panel has none, and an em dash saying so
   * is the difference between "no code" and "a code nobody rendered".
   */
  it('shows a dash for a category that has no code, and offers no control for it', async () => {
    respond([], [VECCHIA])
    renderPanel()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Includi archiviate' }))

    const row = await waitFor(() => rowFor('Vecchia voce'))
    expect(within(row).getByText('—')).toBeInTheDocument()
    // Exactly one control in the row, the name: the code is rendered as text, so
    // there is nothing to type a new one into.
    expect(within(row).getAllByRole('textbox')).toHaveLength(1)
  })

  it('reads an empty list as "not configured yet", not as a failure', async () => {
    respond([])
    renderPanel()

    expect(await screen.findByText('Nessuna categoria configurata.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /categorie predefinite/i })).toBeInTheDocument()
  })

  /**
   * The seed button is offered only on an empty list. It is idempotent server-side, so
   * a second press is harmless -- but a button that visibly does nothing is worse than
   * a button that is not there, and this is the assertion that keeps it from drifting
   * into a permanent fixture of the toolbar.
   */
  it('offers the default taxonomy only while there is nothing configured', async () => {
    respond([CONSULENZA])
    renderPanel()

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
    expect(screen.queryByRole('button', { name: /categorie predefinite/i })).not.toBeInTheDocument()
  })

  it('seeds the default taxonomy from the empty state', async () => {
    respond([])
    vi.mocked(api.POST).mockImplementation(() => ok([CONSULENZA, SOFTWARE], 201))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /categorie predefinite/i }))
    await waitFor(() => expect(api.POST).toHaveBeenCalledWith('/api/cost-categories/seed'))
  })

  it('renders a failed load as an alert, never as an empty list', async () => {
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByText('Nessuna categoria configurata.')).not.toBeInTheDocument()
  })

  it('creates a category at the position after the last one', async () => {
    respond([CONSULENZA, SOFTWARE])
    vi.mocked(api.POST).mockImplementation(() => ok({ ...SOFTWARE, id: 'cat-3' }, 201))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuova categoria/i }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Formazione')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/cost-categories', {
        body: { nome: 'Formazione', posizione: 2 },
      }),
    )
  })

  /**
   * The server names the field it refused, so the message belongs on that field rather
   * than in a banner the eye has to travel to. Without `aria-invalid` a screen reader
   * is told nothing at all about which input is at fault.
   */
  it('attaches a 422 that names a field to that field', async () => {
    respond([])
    vi.mocked(api.POST).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/validation_failed',
          title: 'Dati non validi',
          status: 422,
          entity: 'cost_category',
          field: 'nome',
          reason: 'nome vuoto',
          expected: 'un nome non vuoto',
          detail: 'nome vuoto',
          code: 'validation_failed',
        },
        422,
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuova categoria/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    expect(await screen.findByText('nome vuoto (atteso: un nome non vuoto)')).toBeInTheDocument()
    expect(screen.getByLabelText('Nome')).toHaveAttribute('aria-invalid', 'true')
  })

  /**
   * A duplicate name comes back as a 409 with no `field` at all, so no control can
   * carry it. It has to reach the banner inside the dialog: the list's own banner sits
   * behind the overlay, where a refused create would report itself to nobody, and the
   * dialog must stay open with what was typed still in it.
   */
  it('reports a refused create inside the dialog, which stays open', async () => {
    respond([CONSULENZA])
    vi.mocked(api.POST).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/conflict',
          title: 'Conflitto',
          status: 409,
          detail: 'esiste già una categoria con questo nome',
          code: 'conflict',
          entity: 'cost_category',
        },
        409,
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuova categoria/i }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Consulenza esterna')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'esiste già una categoria con questo nome',
    )
    expect(within(dialog).getByLabelText('Nome')).toHaveValue('Consulenza esterna')
  })

  /**
   * Archiving is not deleting, and this is the assertion that keeps it that way: the
   * resource has no DELETE verb at all, because a deleted category with costs still
   * attached leaves rows whose `category_id` resolves to nothing. If someone ever
   * "simplifies" this button into a delete, this fails.
   */
  it('archives through the archive endpoint and never deletes', async () => {
    respond([CONSULENZA])
    vi.mocked(api.POST).mockImplementation(() => ok({ ...CONSULENZA, archiviata: true }))
    renderPanel()

    await openRowMenu('Consulenza esterna')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Archivia' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/cost-categories/{category_id}/archive', {
        params: { path: { category_id: 'cat-1' } },
      }),
    )
    expect(api.DELETE).not.toHaveBeenCalled()
  })

  /**
   * The other half of "archive is not delete": the row is still there, still named,
   * and still restorable. A cost recorded against it last year keeps showing this
   * name, so the panel has to be able to show it too -- and an archived category
   * offers "Ripristina", never "Archivia" again.
   */
  it('keeps an archived category readable and restorable behind the checkbox', async () => {
    respond([CONSULENZA], [VECCHIA])
    renderPanel()

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
    expect(screen.queryByText('Archiviata')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Includi archiviate' }))

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2))
    expect(api.GET).toHaveBeenCalledWith('/api/cost-categories', {
      params: { query: { include_archived: true } },
    })
    // The state reads through the one pill every state in the product goes through,
    // `muted` because archiving claims nothing and undoes in one click.
    const archived = rowFor('Vecchia voce')
    expect(within(archived).getByText('Archiviata')).toHaveAttribute('data-tone', 'muted')

    await openRowMenu('Vecchia voce')
    expect(screen.getByRole('menuitem', { name: 'Ripristina' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Archivia' })).not.toBeInTheDocument()
  })

  it('restores an archived category through the unarchive endpoint', async () => {
    respond([], [VECCHIA])
    vi.mocked(api.POST).mockImplementation(() => ok({ ...VECCHIA, archiviata: false }))
    renderPanel()

    await userEvent.click(await screen.findByRole('checkbox', { name: 'Includi archiviate' }))
    await openRowMenu('Vecchia voce')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Ripristina' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/cost-categories/{category_id}/unarchive', {
        params: { path: { category_id: 'cat-9' } },
      }),
    )
  })

  /**
   * The rename sends the name and nothing else. Sending `code` back -- even unchanged
   * -- would make the one field a report keys on look editable from here, and sending
   * `posizione` would let a rename silently reorder the list.
   */
  it('renames with the trimmed name alone, never the code or the position', async () => {
    respond([CONSULENZA])
    vi.mocked(api.PATCH).mockImplementation(() => ok({ ...CONSULENZA, nome: 'Consulenze' }))
    renderPanel()

    const input = await screen.findByLabelText('Nome della categoria Consulenza esterna')
    await userEvent.clear(input)
    await userEvent.type(input, '  Consulenze  ')
    await openRowMenu('Consulenza esterna')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Salva' }))

    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/cost-categories/{category_id}', {
        params: { path: { category_id: 'cat-1' } },
        body: { nome: 'Consulenze' },
      }),
    )
  })

  /**
   * No save until something actually changed: an untouched row that offers to save
   * invites a write that records an "updated" activity for no change at all. Disabled
   * rather than absent, which is `RowActions`' own rule -- an item that comes and goes
   * from row to row is a menu nobody learns -- and the item says with its own state
   * that there is nothing to save yet.
   */
  it('disables the save item on a row nobody has edited, and enables it once edited', async () => {
    respond([CONSULENZA])
    renderPanel()

    await openRowMenu('Consulenza esterna')
    expect(screen.getByRole('menuitem', { name: 'Salva' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await userEvent.keyboard('{Escape}')

    await userEvent.type(screen.getByLabelText('Nome della categoria Consulenza esterna'), 'x')
    await openRowMenu('Consulenza esterna')
    expect(screen.getByRole('menuitem', { name: 'Salva' })).not.toHaveAttribute('aria-disabled')
  })

  /**
   * The banner is cleared before every attempt, so it can never outlive the failure it
   * describes. Without this the second, successful press printed "Categoria archiviata"
   * directly under a red alert still claiming the operation was refused -- two
   * contradictory statements about the same click.
   */
  it('clears the previous failure when the retry succeeds', async () => {
    respond([CONSULENZA])
    vi.mocked(api.POST).mockImplementationOnce(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/permission_denied',
          title: 'Permesso negato',
          status: 403,
          detail: 'archive_cost_category requires one of [admin], actor has collaboratore',
          code: 'permission_denied',
        },
        403,
      ),
    )
    renderPanel()

    await openRowMenu('Consulenza esterna')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Archivia' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/requires one of/)

    vi.mocked(api.POST).mockImplementation(() => ok({ ...CONSULENZA, archiviata: true }))
    await openRowMenu('Consulenza esterna')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Archivia' }))

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
