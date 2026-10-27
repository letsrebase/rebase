import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SlackPanel } from './SlackPanel'
import type { SlackHealth, SlackMember } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() },
  }
})

const GET = vi.mocked(api.GET)
const POST = vi.mocked(api.POST)
const PUT = vi.mocked(api.PUT)
const DELETE = vi.mocked(api.DELETE)

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const MARIO: SlackMember = {
  user_id: 'u-mario',
  nome: 'Mario Rossi',
  email: 'mario@acme.example',
  state: 'matched',
  slack_email: 'mario@acme.example',
}
const ADA: SlackMember = {
  user_id: 'u-ada',
  nome: 'Ada Bianchi',
  email: 'ada@studio.example',
  state: 'unmatched',
  slack_email: null,
}

const WORKSPACE = {
  team_id: 'T0TEST',
  team_name: 'Acme Test',
  status: 'active' as const,
  channel_id: null,
  channel_name: null,
  last_error: null,
  last_error_at: null,
  connected_at: '2026-10-27T09:00:00Z',
  disconnected_at: null,
}

const CONNECTED: SlackHealth = {
  configured: true,
  workspace: WORKSPACE,
  members: [MARIO, ADA],
  problem: null,
}

const NOT_CONFIGURED: SlackHealth = { configured: false, workspace: null, members: [], problem: null }
const NOT_CONNECTED: SlackHealth = { configured: true, workspace: null, members: [], problem: null }

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SlackPanel />
    </QueryClientProvider>,
  )
}

function rowOf(name: string) {
  const row = screen.getByText(name).closest('li')
  if (row === null) throw new Error(`no row for ${name}`)
  return within(row)
}

beforeEach(() => {
  for (const method of [GET, POST, PUT, DELETE]) method.mockReset()
})

describe('SlackPanel', () => {
  it('says Slack is not available on this installation rather than offering a button', async () => {
    GET.mockResolvedValue(ok(NOT_CONFIGURED))
    renderPanel()

    expect(await screen.findByText(/Slack non è configurato su questa installazione/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Collega Slack' })).not.toBeInTheDocument()
  })

  it('offers the install when Slack is configured and nothing is connected', async () => {
    GET.mockResolvedValue(ok(NOT_CONNECTED))
    renderPanel()

    expect(await screen.findByRole('link', { name: 'Collega Slack' })).toHaveAttribute(
      'href',
      '/api/slack/oauth/start',
    )
    expect(screen.getByText(/messaggio diretto/)).toBeInTheDocument()
  })

  it('says when a workspace was disconnected and offers the install again', async () => {
    GET.mockResolvedValue(
      ok({
        ...NOT_CONNECTED,
        workspace: { ...WORKSPACE, status: 'disconnected', disconnected_at: '2026-10-27T10:00:00Z' },
      }),
    )
    renderPanel()

    expect(await screen.findByText(/Il workspace Acme Test è stato scollegato il/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Collega Slack' })).toBeInTheDocument()
  })

  it('renders the error banner instead of an empty panel when the request failed', async () => {
    GET.mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByRole('button', { name: 'Scollega Slack' })).not.toBeInTheDocument()
  })

  it('shows the workspace and which users were matched, and which were not', async () => {
    GET.mockResolvedValue(ok(CONNECTED))
    renderPanel()

    expect(await screen.findByText('Acme Test')).toBeInTheDocument()
    expect(screen.getByText(/Stato: attivo/)).toBeInTheDocument()
    expect(rowOf('Mario Rossi').getByText('Riconosciuto dall’email')).toBeInTheDocument()
    expect(rowOf('Ada Bianchi').getByText('Non trovato')).toBeInTheDocument()
    expect(rowOf('Ada Bianchi').getByText(/su Slack non riceve nulla/)).toBeInTheDocument()
    // The count in the intro names the problem once, in the singular.
    expect(screen.getByText(/Un utente non è stato trovato/)).toBeInTheDocument()
  })

  it('looks again at everybody on «Cerca di nuovo» and shows what Slack answered', async () => {
    const user = userEvent.setup()
    GET.mockResolvedValue(ok(CONNECTED))
    POST.mockResolvedValue(
      ok({ ...CONNECTED, members: [MARIO, { ...ADA, state: 'matched', slack_email: ADA.email }] }),
    )
    renderPanel()
    await screen.findByText('Acme Test')

    await user.click(screen.getByRole('button', { name: 'Cerca di nuovo' }))

    expect(POST).toHaveBeenCalledWith('/api/slack/account/rematch')
    await waitFor(() =>
      expect(rowOf('Ada Bianchi').getByText('Riconosciuto dall’email')).toBeInTheDocument(),
    )
  })

  it('fixes a mismatch with the address the person has in Slack', async () => {
    const user = userEvent.setup()
    GET.mockResolvedValue(ok(CONNECTED))
    PUT.mockResolvedValue(
      ok({
        ...CONNECTED,
        members: [
          MARIO,
          { ...ADA, state: 'manual', slack_email: 'ada.bianchi@acme.example' },
        ],
      }),
    )
    renderPanel()
    await screen.findByText('Acme Test')

    await user.click(screen.getByRole('button', { name: 'Correggi l’abbinamento di Ada Bianchi' }))
    const abbina = screen.getByRole('button', { name: 'Abbina' })
    expect(abbina).toBeDisabled()
    await user.type(screen.getByLabelText('Indirizzo di Ada Bianchi in Slack'), ' ada.bianchi@acme.example ')
    await user.click(abbina)

    expect(PUT).toHaveBeenCalledWith('/api/slack/users/{user_id}/link', {
      params: { path: { user_id: 'u-ada' } },
      body: { email: 'ada.bianchi@acme.example' },
    })
    await waitFor(() => expect(rowOf('Ada Bianchi').getByText('Abbinato a mano')).toBeInTheDocument())
    expect(rowOf('Ada Bianchi').getByText(/in Slack: ada.bianchi@acme.example/)).toBeInTheDocument()
    // The form closed with the save.
    expect(screen.queryByLabelText('Indirizzo di Ada Bianchi in Slack')).not.toBeInTheDocument()
  })

  it("shows the server's own sentence when Slack has nobody with that address, and keeps the form", async () => {
    const user = userEvent.setup()
    GET.mockResolvedValue(ok(CONNECTED))
    PUT.mockResolvedValue(
      failed({ detail: 'nessuno nel workspace Acme Test ha l’indirizzo no@acme.example' }, 409),
    )
    renderPanel()
    await screen.findByText('Acme Test')

    await user.click(screen.getByRole('button', { name: 'Correggi l’abbinamento di Ada Bianchi' }))
    await user.type(screen.getByLabelText('Indirizzo di Ada Bianchi in Slack'), 'no@acme.example')
    await user.click(screen.getByRole('button', { name: 'Abbina' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('nessuno nel workspace Acme Test')
    expect(screen.getByLabelText('Indirizzo di Ada Bianchi in Slack')).toHaveValue('no@acme.example')
  })

  it('goes back from a manual match to the automatic one', async () => {
    const user = userEvent.setup()
    GET.mockResolvedValue(
      ok({ ...CONNECTED, members: [{ ...ADA, state: 'manual', slack_email: 'ada.b@acme.example' }] }),
    )
    DELETE.mockResolvedValue(ok({ ...CONNECTED, members: [{ ...ADA, state: 'matched' }] }))
    renderPanel()
    await screen.findByText('Ada Bianchi')

    await user.click(screen.getByRole('button', { name: 'Ripristina l’abbinamento di Ada Bianchi' }))

    expect(DELETE).toHaveBeenCalledWith('/api/slack/users/{user_id}/link', {
      params: { path: { user_id: 'u-ada' } },
    })
    await waitFor(() => expect(screen.queryByText('Abbinato a mano')).not.toBeInTheDocument())
  })

  it('picks the channel for the space-wide reminders from Slack’s own list', async () => {
    const user = userEvent.setup()
    GET.mockImplementation(((path: string) =>
      Promise.resolve(
        path === '/api/slack/channels'
          ? ok([
              { id: 'C1', name: 'promemoria', is_private: false, is_member: false },
              { id: 'G1', name: 'amministrazione', is_private: true, is_member: true },
            ])
          : ok(CONNECTED),
      )) as never)
    PUT.mockResolvedValue(
      ok({ ...CONNECTED, workspace: { ...WORKSPACE, channel_id: 'C1', channel_name: 'promemoria' } }),
    )
    renderPanel()
    await screen.findByText('Acme Test')
    expect(screen.getByText(/nessun canale, solo i messaggi diretti/)).toBeInTheDocument()
    // Slack is asked for the list only when the picker opens.
    expect(GET).not.toHaveBeenCalledWith('/api/slack/channels')

    await user.click(screen.getByRole('button', { name: 'Scegli un canale' }))
    await user.click(await screen.findByRole('combobox', { name: 'Canale dei promemoria dello spazio' }))
    expect(await screen.findByRole('option', { name: 'amministrazione (privato)' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: '#promemoria' }))

    expect(PUT).toHaveBeenCalledWith('/api/slack/channel', { body: { channel_id: 'C1' } })
    expect(await screen.findByText(/Ora: #promemoria\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cambia canale' })).toBeInTheDocument()
  })

  it('clears the channel with «Nessun canale»', async () => {
    const user = userEvent.setup()
    const withChannel = {
      ...CONNECTED,
      workspace: { ...WORKSPACE, channel_id: 'C1', channel_name: 'promemoria' },
    }
    GET.mockImplementation(((path: string) =>
      Promise.resolve(
        path === '/api/slack/channels'
          ? ok([{ id: 'C1', name: 'promemoria', is_private: false, is_member: false }])
          : ok(withChannel),
      )) as never)
    PUT.mockResolvedValue(ok(CONNECTED))
    renderPanel()
    await screen.findByText(/Ora: #promemoria\./)

    await user.click(screen.getByRole('button', { name: 'Cambia canale' }))
    await user.click(await screen.findByRole('combobox', { name: 'Canale dei promemoria dello spazio' }))
    await user.click(await screen.findByRole('option', { name: 'Nessun canale' }))

    expect(PUT).toHaveBeenCalledWith('/api/slack/channel', { body: { channel_id: null } })
    expect(await screen.findByText(/nessun canale, solo i messaggi diretti/)).toBeInTheDocument()
  })

  it('reads a revoked workspace as a reconnect, with the sentence that says why', async () => {
    GET.mockResolvedValue(
      ok({
        ...CONNECTED,
        workspace: {
          ...WORKSPACE,
          status: 'revoked',
          last_error: 'Slack ha revocato l’accesso di PigroCRM per Acme Test: ricollega Slack da Impostazioni → Slack',
        },
      }),
    )
    renderPanel()

    expect(await screen.findByText(/Slack ha revocato l’accesso di PigroCRM per Acme Test/)).toBeInTheDocument()
    expect(screen.getByText(/Stato: accesso revocato/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ricollega Slack' })).toHaveAttribute(
      'href',
      '/api/slack/oauth/start',
    )
    // Nothing that calls Slack is offered while the token is dead.
    expect(screen.getByRole('button', { name: 'Cerca di nuovo' })).toBeDisabled()
  })

  it('reads the workspace again after a refused action, which may have revoked it', async () => {
    const user = userEvent.setup()
    GET.mockResolvedValueOnce(ok(CONNECTED))
    GET.mockResolvedValue(
      ok({
        ...CONNECTED,
        workspace: { ...WORKSPACE, status: 'revoked', last_error: 'Slack ha revocato l’accesso' },
      }),
    )
    POST.mockResolvedValue(failed({ detail: 'Slack ha revocato l’accesso di PigroCRM' }, 409))
    renderPanel()
    await screen.findByText('Acme Test')

    await user.click(screen.getByRole('button', { name: 'Cerca di nuovo' }))

    expect(await screen.findByRole('link', { name: 'Ricollega Slack' })).toBeInTheDocument()
  })

  it('shows the problem of a connection that looks live and cannot work', async () => {
    GET.mockResolvedValue(
      ok({ ...CONNECTED, problem: 'il token Slack memorizzato non è decifrabile: verifica PIGROCRM_SLACK_TOKEN_KEY' }),
    )
    renderPanel()

    expect(await screen.findByText(/non è decifrabile/)).toBeInTheDocument()
  })

  it('asks before disconnecting, says what is lost, and refreshes after', async () => {
    const user = userEvent.setup()
    GET.mockResolvedValueOnce(ok(CONNECTED))
    GET.mockResolvedValue(ok(NOT_CONNECTED))
    DELETE.mockResolvedValue({ response: new Response(null, { status: 204 }) } as never)
    renderPanel()
    await screen.findByText('Acme Test')

    await user.click(screen.getByRole('button', { name: 'Scollega Slack' }))
    expect(await screen.findByText(/gli abbinamenti degli utenti, quelli fatti a mano/i)).toBeInTheDocument()
    expect(DELETE).not.toHaveBeenCalled()
    const dialog = within(screen.getByRole('dialog'))
    await user.click(dialog.getByRole('button', { name: 'Scollega Slack' }))

    expect(DELETE).toHaveBeenCalledWith('/api/slack/account')
    expect(await screen.findByRole('link', { name: 'Collega Slack' })).toBeInTheDocument()
  })

  it('never renders a token, whatever the answer carries', async () => {
    GET.mockResolvedValue(ok(CONNECTED))
    const { container } = renderPanel()
    await screen.findByText('Acme Test')
    expect(container.textContent).not.toMatch(/xoxb|token_/i)
  })
})
