import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Me } from '@/lib/api'
import { ME_KEY } from '@/lib/me'
import { MemberWorkProfile } from './WorkProfile'

type Profile = NonNullable<Me['work_profile']>

const BASE: Profile = {
  state: 'draft',
  submitted_at: '2026-11-12T10:00:00Z',
  expires_at: '2027-11-12T10:00:00Z',
  expired: false,
  decision_note: null,
  technologies: [],
  practices: [],
}

function show(profile: Me['work_profile'], vetted = true) {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <MemberWorkProfile profile={profile} vetted={vetted} />
    </QueryClientProvider>,
  )
  return client
}

afterEach(() => vi.restoreAllMocks())

describe('«Profilo di lavoro» in the member area (REB-1127)', () => {
  it('before any submission explains the tool and gives the three commands and the two links', () => {
    show(null)
    const section = screen.getByRole('region', { name: 'Profilo di lavoro' })
    expect(within(section).getByText(/fork di/)).toHaveTextContent('apply-new')
    expect(within(section).getByText(/Verificato da rebase/)).toBeInTheDocument()
    expect(within(section).getByText('git clone https://github.com/letsrebase/rebase-verify')).toBeInTheDocument()
    expect(within(section).getByText('cd rebase-verify')).toBeInTheDocument()
    expect(within(section).getByText('claude')).toBeInTheDocument()
    expect(within(section).getByText('/rebase-verify')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: /README/ })).toHaveAttribute(
      'href',
      'https://github.com/letsrebase/rebase-verify#readme',
    )
    expect(within(section).getByRole('link', { name: /privacy/ })).toHaveAttribute('href', 'https://letsrebase.com/privacy')
    expect(within(section).queryByRole('button', { name: 'Elimina il profilo' })).toBeNull()
  })

  it('says «In revisione» for a draft, with the date it was sent', () => {
    show(BASE)
    expect(screen.getByText('In revisione')).toBeInTheDocument()
    expect(screen.getByText(/Inviato il 12 nov 2026/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Elimina il profilo' })).toBeInTheDocument()
  })

  it('says until when an approved profile counts', () => {
    show({ ...BASE, state: 'approved' })
    expect(screen.getByText('Approvato fino al 12 nov 2027')).toBeInTheDocument()
  })

  it('says «Verificato» only for a person who is verified', () => {
    show({ ...BASE, state: 'approved' }, false)
    expect(screen.queryByText(/Sei «Verificato da rebase»/)).toBeNull()
    expect(screen.getByText(/aziende con cui lavoriamo possono vedere il riassunto/)).toBeInTheDocument()
  })

  it('shows the note of a rejected profile and how to send another', () => {
    show({ ...BASE, state: 'rejected', decision_note: 'Manca un progetto con un link.' })
    expect(screen.getByText('Non approvato')).toBeInTheDocument()
    expect(screen.getByText('Manca un progetto con un link.')).toBeInTheDocument()
    expect(screen.getByText('cd rebase-verify')).toBeInTheDocument()
  })

  it('shows the person their own technologies, filled by band, and says companies do not see them', () => {
    show({
      ...BASE,
      technologies: [
        { name: 'Zod', band: 'recurring', sessions: 9, products: 2, months: 4, first_month: '2026-01', last_month: '2026-05' },
        { name: 'Python', band: 'continuous', sessions: 40, products: 6, months: 6, first_month: '2026-01', last_month: '2026-06' },
      ],
    })
    const section = screen.getByRole('region', { name: 'Profilo di lavoro' })
    expect(within(section).getByRole('heading', { name: 'Le tecnologie nel tuo lavoro' })).toBeInTheDocument()
    expect(within(section).getByText(/Le aziende non le vedono/)).toBeInTheDocument()
    expect(within(section).getByText('Python')).toHaveAttribute('data-fill', 'full')
    expect(within(section).getByText('Zod')).toHaveAttribute('data-fill', 'two-thirds')
    expect(within(section).getByText(/non quanto la si conosce/)).toBeInTheDocument()
  })

  it('shows no technologies block when the profile has none', () => {
    show(BASE)
    expect(screen.queryByRole('heading', { name: 'Le tecnologie nel tuo lavoro' })).toBeNull()
  })

  it('says an expired profile no longer counts', () => {
    show({ ...BASE, state: 'approved', expired: true })
    expect(screen.getByText('Scaduto')).toBeInTheDocument()
    expect(screen.queryByText(/Approvato fino al/)).toBeNull()
  })

  it('deletes only after a second click and puts the answer in the cache', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ id: 'u1', work_profile: null }), { status: 200 }))
    const client = show(BASE)
    await userEvent.click(screen.getByRole('button', { name: 'Elimina il profilo' }))
    expect(spy).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Sì, elimina' }))
    await vi.waitFor(() => expect(client.getQueryData(ME_KEY)).toEqual({ id: 'u1', work_profile: null }))
    expect(spy).toHaveBeenCalledWith('/api/hub/me/work-profile', expect.objectContaining({ method: 'DELETE' }))
  })

  it('can be cancelled at the second click', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    show(BASE)
    await userEvent.click(screen.getByRole('button', { name: 'Elimina il profilo' }))
    await userEvent.click(screen.getByRole('button', { name: 'Annulla' }))
    expect(screen.getByRole('button', { name: 'Elimina il profilo' })).toBeInTheDocument()
    expect(spy).not.toHaveBeenCalled()
  })
})
