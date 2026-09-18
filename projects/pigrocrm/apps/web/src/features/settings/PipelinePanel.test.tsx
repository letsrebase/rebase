import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PipelinePanel } from './PipelinePanel'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

// openapi-fetch resolves with `{data|error, response}` for every HTTP outcome --
// it never rejects for an HTTP error status. See queries.test.tsx's identical
// helpers (features/deals/queries.test.tsx) for the same shape.
function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const STAGE = {
  id: 's1',
  nome: 'Lead',
  posizione: 0,
  probabilita_default: 10,
  tipo: 'open' as const,
  code: 'lead',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PipelinePanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(toast.error).mockReset()
  vi.mocked(toast.success).mockReset()
})

/** The row's actions live behind the «⋯» menu since the 2026-09-08 revision (design
 *  spec §4); the trigger is labelled per row so a table of them stays unambiguous. */
async function openRowMenu(nome: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Azioni per ${nome}` }))
}

describe('PipelinePanel', () => {
  it('lists the pipeline stages', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    renderPanel()
    expect(await screen.findByText('Lead')).toBeInTheDocument()
  })

  it('shows a failed list as a distinct alert, not an empty-looking table', async () => {
    vi.mocked(api.GET).mockReturnValue(
      Promise.resolve(failed({ code: 'http_error', detail: 'Il server non risponde.' }, 503)),
    )
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  // The brief never deleted a stage at all (this task's fourth named gap).
  it('offers a way to delete a stage, behind the row menu', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    renderPanel()
    await openRowMenu('Lead')
    expect(screen.getByRole('menuitem', { name: 'Elimina' })).toBeInTheDocument()
  })

  it('reports the stage type as a pill, tinted by the type the backend guarantees', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    renderPanel()
    // `DEAL_STAGE_TONE` keys on `tipo`, not on the (renameable) name, so this stays
    // right for a tenant that calls its open stage anything at all.
    expect(await screen.findByText('Aperto')).toHaveAttribute('data-tone', 'muted')
  })

  it('does nothing without confirmation', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPanel()

    await openRowMenu('Lead')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Elimina' }))
    expect(api.DELETE).not.toHaveBeenCalled()
  })

  it('deletes the stage through the real endpoint once confirmed', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    vi.mocked(api.DELETE).mockReturnValueOnce(Promise.resolve(ok(undefined)))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()

    await openRowMenu('Lead')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Elimina' }))

    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith(
        '/api/pipeline-stages/{stage_id}',
        expect.objectContaining({ params: { path: { stage_id: 's1' } } }),
      ),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  /**
   * This task's fourth named gap: `DELETE /api/pipeline-stages/{id}` refuses
   * (409) with a message naming how many deals -- archived included -- still
   * point at the stage (`PipelineService.delete`'s own docstring). The point of
   * this test is that the message actually reaches the user, appended with the
   * count the server reported, rather than dying in a console the way an
   * unhandled mutation rejection would.
   */
  it('surfaces the server’s conflict message, with the deal count, when the stage still has deals', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([STAGE])))
    vi.mocked(api.DELETE).mockReturnValueOnce(
      Promise.resolve(
        failed(
          {
            code: 'conflict',
            detail: 'ci sono deal in questo stato (inclusi quelli archiviati): spostali in un altro stato prima di eliminarlo',
            deals: 3,
          },
          409,
        ),
      ),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()

    await openRowMenu('Lead')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Elimina' }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('spostali in un altro stato prima di eliminarlo'),
      ),
    )
    expect(vi.mocked(toast.error).mock.calls[0]?.[0]).toContain('(3)')
  })

  it('does not gate "Crea" on the form being filled in -- the backend decides, not the client', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuovo stato/i }))
    expect(screen.getByRole('button', { name: 'Crea' })).not.toBeDisabled()
  })

  it('creates a stage with the fields the dialog collects', async () => {
    vi.mocked(api.GET).mockReturnValue(Promise.resolve(ok([])))
    vi.mocked(api.POST).mockReturnValueOnce(Promise.resolve(ok(STAGE)))
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: /nuovo stato/i }))
    await userEvent.type(screen.getByLabelText('Nome'), 'Trattativa')
    await userEvent.click(screen.getByRole('button', { name: 'Crea' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        '/api/pipeline-stages',
        expect.objectContaining({
          body: expect.objectContaining({ nome: 'Trattativa', tipo: 'open' }),
        }),
      ),
    )
  })
})
