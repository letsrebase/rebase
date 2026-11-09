import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { spaceModules } from '@/lib/modules.testing'
import { queryKeys } from '@/lib/query'

let pathname = '/app'
let user: { id: string; role: string; legal_entities?: string[] | null } | null
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useLocation: () => ({ pathname }) }
})
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user }),
  useIsAdmin: () => user?.role === 'admin',
}))
vi.mock('./Onboarding', () => ({ Onboarding: () => <p>onboarding</p> }))

import { OnboardingGate } from './OnboardingGate'

function renderGate(data: ReturnType<typeof spaceModules> | undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (data) client.setQueryData(queryKeys.modules, data)
  return render(
    <QueryClientProvider client={client}>
      <OnboardingGate>
        <p>shell</p>
      </OnboardingGate>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  pathname = '/app'
  user = { id: 'u-1', role: 'admin' }
})

describe('OnboardingGate', () => {
  it('covers the shell for an administrator while the onboarding is pending', () => {
    renderGate(spaceModules([], 'pending'))
    expect(screen.getByText('onboarding')).toBeInTheDocument()
    expect(screen.queryByText('shell')).not.toBeInTheDocument()
  })

  it.each(['skipped', 'done'] as const)('lets the shell through when it is %s', (state) => {
    renderGate(spaceModules([], state))
    expect(screen.getByText('shell')).toBeInTheDocument()
  })

  it('opens the screen again on /app/onboarding after a «Decido dopo», and only then', () => {
    pathname = '/app/onboarding/'
    renderGate(spaceModules([], 'skipped'))
    expect(screen.getByText('onboarding')).toBeInTheDocument()
  })

  it('does not reopen a finished onboarding from the address bar', () => {
    pathname = '/app/onboarding'
    renderGate(spaceModules([], 'done'))
    expect(screen.getByText('shell')).toBeInTheDocument()
  })

  it('never shows it to a member who is not an administrator', () => {
    user = { id: 'u-2', role: 'collaborator' }
    renderGate(spaceModules([], 'pending'))
    expect(screen.getByText('shell')).toBeInTheDocument()
  })

  it('never shows it to an administrator scoped to some aziende: the server would refuse every answer', () => {
    user = { id: 'u-3', role: 'admin', legal_entities: ['le-1'] }
    renderGate(spaceModules([], 'pending'))
    expect(screen.getByText('shell')).toBeInTheDocument()
  })

  it('treats a read that has not answered as not known, never as pending', () => {
    renderGate(undefined)
    expect(screen.queryByText('onboarding')).not.toBeInTheDocument()
  })
})
