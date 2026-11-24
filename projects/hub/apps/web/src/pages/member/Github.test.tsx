import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { member, type Me } from '@/lib/api'
import { ME_KEY } from '@/lib/me'
import { MemberGithub } from './Github'

const LINKED: NonNullable<Me['github']> = { login: 'ada', scope: 'private', linked_at: '2026-11-20T10:00:00Z' }

function show(github: Me['github'], notice: string | null = null) {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemberGithub github={github} notice={notice} />
    </QueryClientProvider>,
  )
  return client
}

function stubAssign() {
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  return assign
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('«GitHub» in the member area (REB-1166)', () => {
  it('builds the link address from the scope', () => {
    expect(member.githubLinkUrl('public')).toBe('/api/hub/auth/oauth/github/link?scope=public')
    expect(member.githubLinkUrl('private')).toBe('/api/hub/auth/oauth/github/link?scope=private')
  })

  it('not linked: explains, defaults to public and navigates with the chosen scope', async () => {
    const assign = stubAssign()
    show(null)
    const section = screen.getByRole('region', { name: 'GitHub' })
    expect(within(section).getByText(/Leggiamo solo numeri, mai il codice/)).toBeInTheDocument()
    const pub = within(section).getByRole('radio', { name: 'Solo l’attività pubblica' })
    expect(pub).toBeChecked()
    await userEvent.click(within(section).getByRole('button', { name: 'Collega GitHub' }))
    expect(assign).toHaveBeenLastCalledWith('/api/hub/auth/oauth/github/link?scope=public')
    await userEvent.click(within(section).getByRole('radio', { name: /Anche i conteggi delle attività private/ }))
    await userEvent.click(within(section).getByRole('button', { name: 'Collega GitHub' }))
    expect(assign).toHaveBeenLastCalledWith('/api/hub/auth/oauth/github/link?scope=private')
  })

  it('linked: says who and what is shared, and unlinks behind a second click', async () => {
    const after = { github: null } as unknown as Me
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(after), { status: 200, headers: { 'content-type': 'application/json' } }),
    )
    const client = show(LINKED)
    expect(screen.getByText(/Collegato come/)).toHaveTextContent('Collegato come ada il 20 nov 2026')
    expect(screen.getByText('Condividi anche i conteggi delle attività private.')).toBeInTheDocument()
    expect(screen.queryByRole('radio')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Scollega' }))
    expect(screen.getByText(/cancelliamo subito quello che abbiamo letto/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sì, scollega' }))
    await waitFor(() => expect(client.getQueryData(ME_KEY)).toEqual(after))
    const request = spy.mock.calls[0]?.[0] as string | URL | Request
    expect(request instanceof Request ? request.method : spy.mock.calls[0]?.[1]?.method).toBe('DELETE')
    expect(String(request instanceof Request ? request.url : request)).toContain('/api/hub/me/github')
  })

  it('linked: «Collega di nuovo» shows the scope choice', async () => {
    show({ ...LINKED, scope: 'public' })
    expect(screen.getByText('Condividi solo l’attività pubblica.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Collega di nuovo' }))
    expect(screen.getAllByRole('radio')).toHaveLength(2)
  })

  it('says why a link failed, as an alert, and nothing for an unknown reason', () => {
    show(null, 'taken')
    expect(screen.getByRole('alert')).toHaveTextContent('Questo account GitHub è già collegato a un’altra scheda.')
  })

  it('confirms a link as a status, and stays silent on an unknown reason', () => {
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <MemberGithub github={LINKED} notice="linked" />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('status')).toHaveTextContent('Account GitHub collegato.')
    unmount()
    show(null, 'bogus')
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
