import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, fetchWithRefresh } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { DayDetail } from './DayDetail'
import { DAY_ID, detail, ok, refused, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() }, fetchWithRefresh: vi.fn() }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
}))

const auth = { ruolo: 'collaboratore' as 'readonly' | 'collaboratore' | 'admin' }
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.ruolo !== 'readonly' }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function serving(shown = detail()) {
  vi.mocked(api.GET).mockResolvedValue(ok(shown) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  auth.ruolo = 'collaboratore'
})

describe('a day’s page', () => {
  it('shows its state, what was done, and the log in the order it happened', async () => {
    serving(
      detail({
        stato: 'contestato',
        azioni: ['lavorato'],
        transizioni: [
          { id: 't1', work_unit_id: DAY_ID, stato_precedente: null, stato_nuovo: 'approvato', attore: { kind: 'human', id: 'u1' }, motivo: 'creazione', created_at: '2026-03-01T09:00:00Z', seq: 1 },
          { id: 't2', work_unit_id: DAY_ID, stato_precedente: 'approvato', stato_nuovo: 'lavorato', attore: { kind: 'human', id: 'u1' }, motivo: 'Giornata svolta', created_at: '2026-03-02T17:00:00Z', seq: 2 },
          { id: 't3', work_unit_id: DAY_ID, stato_precedente: 'lavorato', stato_nuovo: 'contestato', attore: { kind: 'human', id: 'u1' }, motivo: 'Il cliente contesta le ore', created_at: '2026-03-09T10:00:00Z', seq: 3 },
        ],
      }),
    )
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Giornata del 02/03/2026' })).toBeInTheDocument()
    expect(screen.getByText('Consulenza architetturale, Acme S.r.l.')).toBeInTheDocument()
    expect(screen.getByText('Mezza giornata')).toBeInTheDocument()
    expect(screen.getByText('Revisione architettura servizi')).toBeInTheDocument()
    const history = screen.getByRole('list', { name: '' })
    const steps = within(history).getAllByRole('listitem')
    expect(steps).toHaveLength(3)
    expect(steps[0]).toHaveTextContent('Registrata come approvata')
    expect(steps[1]).toHaveTextContent('Approvata')
    expect(steps[1]).toHaveTextContent('Lavorata')
    expect(steps[1]).toHaveTextContent('Giornata svolta')
    expect(steps[2]).toHaveTextContent('Il cliente contesta le ore')
    expect(steps[2]).toHaveTextContent('una persona')
    expect(within(steps[2] as HTMLElement).getByText(/2026/)).toBeInTheDocument()
  })

  it('offers exactly the moves the API says the state allows', async () => {
    serving(detail({ azioni: ['lavorato', 'revocato'] }))
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    expect(await screen.findByRole('button', { name: 'Segna come lavorata' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revoca' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Contesta' })).not.toBeInTheDocument()
  })

  it('marks an approved day as worked in one click, with the act as its reason', async () => {
    // The server is the one that moves the day: what it answers after the POST is the new state.
    let current = detail()
    vi.mocked(api.GET).mockImplementation((() => Promise.resolve(ok(current))) as never)
    vi.mocked(api.POST).mockImplementation((() => {
      current = detail({ stato: 'lavorato', azioni: ['contestato'] })
      return Promise.resolve(ok(current))
    }) as never)
    const user = userEvent.setup()
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)

    await user.click(await screen.findByRole('button', { name: 'Segna come lavorata' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Segna come lavorata' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/work-units/{work_unit_id}/transitions')
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toMatchObject({
      params: { path: { work_unit_id: DAY_ID } },
      body: { stato: 'lavorato', motivo: 'Giornata svolta' },
    })
    // The page redraws from the answer: the day is worked and contesting is now on offer.
    expect(await screen.findByRole('button', { name: 'Contesta' })).toBeInTheDocument()
  })

  it('will not contest a day without a reason', async () => {
    serving(detail({ stato: 'lavorato', azioni: ['contestato'] }))
    vi.mocked(api.POST).mockResolvedValue(ok(detail({ stato: 'contestato', azioni: [] })) as never)
    const user = userEvent.setup()
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)

    await user.click(await screen.findByRole('button', { name: 'Contesta' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Contesta' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Motivo'), 'Il cliente contesta le ore')
    await user.click(within(dialog).getByRole('button', { name: 'Contesta' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toMatchObject({
      body: { stato: 'contestato', motivo: 'Il cliente contesta le ore' },
    })
  })

  it('keeps the dialog open and says why when the move is refused', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, { title: 'x', status: 409, detail: "una giornata 'pagato' non puo' passare a 'lavorato'", code: 'conflict', instance: '/' }) as never,
    )
    const user = userEvent.setup()
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    await user.click(await screen.findByRole('button', { name: 'Segna come lavorata' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Segna come lavorata' }))
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(/non puo' passare/)
  })

  it('says a day worked without the required approval is flagged, with no approval to show', async () => {
    serving(detail({ stato: 'lavorato_senza_approvazione', requires_prior_approval: true, azioni: ['contestato'] }))
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    expect(await screen.findByText(/non è collegata nessuna/)).toBeInTheDocument()
    expect(screen.getByText('Nessuna approvazione collegata, e il contratto ne richiede una.')).toBeInTheDocument()
  })

  it('shows the approval a day rests on and links its document', async () => {
    serving(
      detail({
        stato: 'lavorato',
        approval_id: 'a1',
        approval: {
          id: 'a1',
          contract_id: 'c',
          canale: 'posta_certificata',
          mittente: 'giulia@nordwind.example',
          ricevuto_il: '2026-02-20T09:12:00Z',
          message_id: null,
          document_id: 'doc-9',
          estratto: 'Confermo le giornate di marzo.',
          origine: { kind: 'manuale' },
          created_at: '2026-02-20T09:12:00Z',
          updated_at: '2026-02-20T09:12:00Z',
          documento_titolo: 'Mail di conferma',
        },
      }),
    )
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    expect(await screen.findByText(/da giulia@nordwind.example, per posta certificata/)).toBeInTheDocument()
    expect(screen.getByText('Confermo le giornate di marzo.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Mail di conferma/ })).toHaveAttribute('href', '/app/documents/doc-9')
  })

  it('offers to record the client’s approval on a day that can use one, and not on one that has it', async () => {
    serving(detail({ stato: 'lavorato_senza_approvazione', requires_prior_approval: true, azioni: ['contestato'] }))
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    const link = await screen.findByRole('link', { name: /Registra l’approvazione del cliente/ })
    expect(link.getAttribute('href')).toBe('/app/approvals/new')
  })

  it('links the contract the day belongs to', async () => {
    serving()
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    const link = await screen.findByRole('link', { name: 'Consulenza architetturale' })
    expect(link).toHaveAttribute('href', '/app/contracts/5b2c70c2-0000-4000-8000-000000000001')
  })

  it('says when the date is outside the contract', async () => {
    serving(detail({ fuori_termine: 'dopo_la_fine', contratto_fine: '2026-02-28' }))
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    expect(await screen.findByText('La data è dopo la fine del contratto, il 28/02/2026.')).toBeInTheDocument()
  })

  it('shows a reader the day and no buttons', async () => {
    auth.ruolo = 'readonly'
    serving()
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('button', { name: 'Segna come lavorata' })).not.toBeInTheDocument()
  })

  it('says so when the day is not there', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      refused(404, { title: 'x', status: 404, detail: 'work_unit non trovato', code: 'not_found', instance: '/' }) as never,
    )
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)
    expect(await screen.findByText('Giornata non trovata.')).toBeInTheDocument()
  })

  it('downloads the dispute bundle through the session-refreshing fetch, for a reader too', async () => {
    auth.ruolo = 'readonly'
    serving(detail({ stato: 'contestato', azioni: [] }))
    vi.mocked(fetchWithRefresh).mockResolvedValue({
      ok: true,
      headers: new Headers({ 'Content-Disposition': 'attachment; filename="fascicolo-x.zip"' }),
      blob: () => Promise.resolve(new Blob(['zip'])),
    } as Response)
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const names: string[] = []
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download)
      })
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Scarica il fascicolo' }))

    await waitFor(() =>
      expect(fetchWithRefresh).toHaveBeenCalledWith(`/api/work-units/${DAY_ID}/dispute-bundle`),
    )
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x'))
    expect(click).toHaveBeenCalled()
    expect(names).toEqual(['fascicolo-x.zip'])
  })

  it('says why when the bundle cannot be downloaded', async () => {
    serving(detail({ stato: 'contestato', azioni: [] }))
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ title: 'x', status: 404, detail: 'work_unit non trovato' }), {
        status: 404,
      }),
    )
    renderWithQuery(<DayDetail workUnitId={DAY_ID} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Scarica il fascicolo' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
  })
})
