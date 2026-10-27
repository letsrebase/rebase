import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SlackNotice } from './SlackNotice'
import { messaggioEsito, riprovaEsito } from './esito'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const GET = vi.mocked(api.GET)

function mine(data: object) {
  GET.mockResolvedValue({ data, response: new Response(null, { status: 200 }) } as never)
}

function renderNotice() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SlackNotice />
    </QueryClientProvider>,
  )
}

beforeEach(() => GET.mockReset())

describe('SlackNotice', () => {
  it('says nothing on an installation with no Slack app', async () => {
    mine({ configured: false, connected: false, team_name: null, matched: false })
    const { container } = renderNotice()
    await vi.waitFor(() => expect(GET).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says nothing while the read has not answered, and when it failed', async () => {
    GET.mockResolvedValue({ error: { detail: 'no' }, response: new Response(null, { status: 503 }) } as never)
    const { container } = renderNotice()
    await vi.waitFor(() => expect(GET).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('tells a person that the switch does nothing while no workspace is connected', async () => {
    mine({ configured: true, connected: false, team_name: null, matched: false })
    renderNotice()
    const note = await screen.findByRole('status')
    expect(note).toHaveTextContent('Slack non è collegato a questo spazio')
    expect(note).toHaveAttribute('data-slack-state', 'disconnected')
  })

  it('tells a person who is in the workspace where their reminders arrive', async () => {
    mine({ configured: true, connected: true, team_name: 'Acme Test', matched: true })
    renderNotice()
    const note = await screen.findByRole('status')
    expect(note).toHaveTextContent('collegato a Acme Test')
    expect(note).toHaveTextContent('messaggio diretto')
    expect(note).toHaveAttribute('data-slack-state', 'matched')
  })

  it('tells a person nobody matched that they receive nothing on Slack, and who fixes it', async () => {
    mine({ configured: true, connected: true, team_name: 'Acme Test', matched: false })
    renderNotice()
    const note = await screen.findByRole('status')
    expect(note).toHaveTextContent('su Slack non ricevi nulla')
    expect(note).toHaveTextContent('Chiedi a un amministratore')
    expect(note).toHaveAttribute('data-slack-state', 'unmatched')
  })
})

describe('the outcome of an install', () => {
  it('renders four fixed sentences and nothing a link carries', () => {
    expect(messaggioEsito('collegato')).toBe('Slack collegato.')
    expect(messaggioEsito('negato')).toMatch(/negata/)
    expect(messaggioEsito('errore')).toMatch(/non è andato a buon fine/)
    expect(messaggioEsito('sessione')).toMatch(/sessione è scaduta/)
    expect(messaggioEsito('<b>ciao</b>')).toBeNull()
    expect(messaggioEsito(undefined)).toBeNull()
  })

  it('offers «Riprova» only after a session that ran out', () => {
    expect(riprovaEsito('sessione')).toBe('/api/slack/oauth/start')
    expect(riprovaEsito('errore')).toBeNull()
    expect(riprovaEsito(undefined)).toBeNull()
  })
})
