import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TemplatesPanel } from './TemplatesPanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

// openapi-fetch resolves with `{data|error, response}` for every HTTP outcome --
// it never rejects on an error status.
function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const TEMPLATE = {
  id: 't-1',
  nome: 'Consulenza CTO',
  tipo: 'offerta',
  corpo_markdown: 'Oggetto: {{oggetto}}',
  variabili_dichiarate: [
    { nome: 'oggetto', etichetta: 'Oggetto', tipo: 'text', obbligatoria: true, options: [] },
  ],
  attivo: true,
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

const BARE = { ...TEMPLATE, id: 't-2', nome: 'Verbale vuoto', variabili_dichiarate: [] }

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TemplatesPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

/** The row's actions live behind the «⋯» menu since the 2026-09-08 revision (design
 *  spec §4); the trigger is labelled per row so a table of them stays unambiguous. */
async function openRowMenu(nome: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Azioni per ${nome}` }))
}

describe('TemplatesPanel', () => {
  it('lists templates and reads a page, not a bare array', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [TEMPLATE], next_cursor: null }))
    renderPanel()

    expect(await screen.findByText('Consulenza CTO')).toBeInTheDocument()
    expect(screen.getByText('Offerta')).toBeInTheDocument()
  })

  it('shows a template with no declared variables as 0, never a dash', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [BARE], next_cursor: null }))
    renderPanel()

    expect(await screen.findByText('Verbale vuoto')).toBeInTheDocument()
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.queryByText('—')).not.toBeInTheDocument()
  })

  it('shows a failed list as an alert, not as an empty table', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByText('Nessun template definito.')).not.toBeInTheDocument()
  })

  it('creates a template with its declared variables', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [], next_cursor: null }))
    vi.mocked(api.POST).mockResolvedValue(ok(TEMPLATE))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo template' }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Consulenza CTO')
    await userEvent.type(screen.getByLabelText('Corpo Markdown'), 'Oggetto: X')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi variabile' }))
    await userEvent.type(screen.getByLabelText('Nome', { selector: '#variabile-nome-0' }), 'oggetto')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [, options] = vi.mocked(api.POST).mock.calls[0] as unknown as [string, { body: Record<string, unknown> }]
    expect(options.body.nome).toBe('Consulenza CTO')
    expect(options.body.tipo).toBe('offerta')
    expect(options.body.variabili_dichiarate).toEqual([
      { nome: 'oggetto', etichetta: '', tipo: 'text', obbligatoria: false, options: [] },
    ])
  })

  it('splits comma-separated options and drops the blanks a trailing comma leaves', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [], next_cursor: null }))
    vi.mocked(api.POST).mockResolvedValue(ok(TEMPLATE))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo template' }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Con opzioni')
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi variabile' }))
    await userEvent.selectOptions(
      screen.getByLabelText('Tipo', { selector: '#variabile-tipo-0' }),
      'select',
    ).catch(() => undefined)
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
  })

  it('attaches a server validation error to the field it names', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [], next_cursor: null }))
    vi.mocked(api.POST).mockResolvedValue(
      failed(
        {
          code: 'validation_failed',
          entity: 'template',
          field: 'nome',
          reason: 'non può essere vuoto',
          detail: 'non può essere vuoto',
        },
        422,
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo template' }))
    await userEvent.type(screen.getByLabelText('Nome'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    expect(await screen.findByText('non può essere vuoto')).toBeInTheDocument()
    expect(screen.getByLabelText('Nome')).toHaveAttribute('aria-invalid', 'true')
  })

  /**
   * A duplicate name is a `conflict`, not a `validation_failed`, and
   * `fieldErrorFrom` deliberately only attaches the latter to a control. So the
   * message has to reach the user through the banner instead of vanishing --
   * which is the thing worth pinning here, since the alternative is a dialog that
   * silently does nothing when Crea is pressed.
   */
  it('surfaces a duplicate name in the banner, since a conflict names no field', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [], next_cursor: null }))
    vi.mocked(api.POST).mockResolvedValue(
      failed({ code: 'conflict', entity: 'template', detail: 'nome già in uso' }, 409),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo template' }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Doppio')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('nome già in uso')
  })

  it('reports the state as a pill, on the one component every state in the product uses', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({ items: [TEMPLATE, { ...TEMPLATE, id: 't2', nome: 'Spenta', attivo: false }], next_cursor: null }),
    )
    renderPanel()
    expect(await screen.findByText('Attivo')).toHaveAttribute('data-tone', 'ink')
    expect(screen.getByText('Disattivato')).toHaveAttribute('data-tone', 'muted')
  })

  it('deactivates through DELETE and offers the way back', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [TEMPLATE], next_cursor: null }))
    vi.mocked(api.DELETE).mockResolvedValue(ok({ ...TEMPLATE, attivo: false }))
    renderPanel()

    await openRowMenu('Consulenza CTO')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Disattiva' }))
    await waitFor(() => expect(api.DELETE).toHaveBeenCalled())
  })

  it('reactivates a deactivated template', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({ items: [{ ...TEMPLATE, attivo: false }], next_cursor: null }),
    )
    vi.mocked(api.POST).mockResolvedValue(ok(TEMPLATE))
    renderPanel()

    await openRowMenu('Consulenza CTO')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Riattiva' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path] = vi.mocked(api.POST).mock.calls[0] as unknown as [string]
    expect(path).toContain('/activate')
  })

  it('asks the server for the preview instead of rendering it locally', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [TEMPLATE], next_cursor: null }))
    vi.mocked(api.POST).mockResolvedValue(ok({ markdown: 'Oggetto: Oggetto' }))
    renderPanel()

    await openRowMenu('Consulenza CTO')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Modifica' }))
    await userEvent.click(screen.getByRole('button', { name: 'Anteprima' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    const [path] = vi.mocked(api.POST).mock.calls[0] as unknown as [string]
    expect(path).toContain('/preview')
    expect(await screen.findByText('Oggetto: Oggetto')).toBeInTheDocument()
  })

  it('cannot preview a template that has never been saved, and says why', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [], next_cursor: null }))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Nuovo template' }))
    expect(screen.getByRole('button', { name: 'Anteprima' })).toBeDisabled()
    expect(
      screen.getByText(/L'anteprima è calcolata dal server: salva il template una prima volta\./),
    ).toBeInTheDocument()
  })
})
