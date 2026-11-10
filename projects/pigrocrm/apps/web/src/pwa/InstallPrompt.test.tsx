import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InstallPrompt } from './InstallPrompt'

// The router is not mounted here: only the address it reports matters, and a test moves
// it and re-renders to stand for a navigation.
const router = vi.hoisted(() => ({ href: '/app/' }))
vi.mock('@tanstack/react-router', () => ({ useRouterState: () => router.href }))

function fireInstallPrompt(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: () => Promise<void>
    userChoice: Promise<{ outcome: string }>
  }
  event.prompt = vi.fn().mockResolvedValue(undefined)
  event.userChoice = Promise.resolve({ outcome })
  act(() => {
    window.dispatchEvent(event)
  })
  return event
}

function engage() {
  act(() => {
    window.dispatchEvent(new Event('pointerdown'))
  })
}

describe('InstallPrompt', () => {
  beforeEach(() => {
    vi.stubEnv('PROD', true)
    router.href = '/app/'
    window.localStorage.clear()
  })
  afterEach(() => {
    vi.unstubAllEnvs()
    Reflect.deleteProperty(navigator, 'standalone')
  })

  it('is a row of the page column, never a card floating over it (REB-791)', () => {
    render(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    const row = screen.getByRole('status')
    expect(row.className).not.toMatch(/\b(fixed|absolute|sticky)\b/)
    expect(row.className).toContain('flex-none')
  })

  it('offers the install only after the person has done something', () => {
    render(<InstallPrompt />)
    fireInstallPrompt()
    expect(screen.queryByRole('status')).toBeNull()
    engage()
    expect(screen.getByRole('status')).toHaveTextContent('Installa PigroCRM')
  })

  it("hands the browser's own prompt over when they accept, and says nothing more", async () => {
    render(<InstallPrompt />)
    const event = fireInstallPrompt()
    engage()
    await userEvent.click(screen.getByRole('button', { name: 'Installa' }))
    expect(event.prompt).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('does not ask again after a "Non ora", on this visit or the next', async () => {
    const { unmount } = render(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    await userEvent.click(screen.getByRole('button', { name: 'Non ora' }))
    expect(screen.queryByRole('status')).toBeNull()
    unmount()

    render(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('stays silent on the bare login and offers the install once the page moves on', () => {
    router.href = '/app/login'
    const { rerender } = render(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    expect(screen.queryByRole('status')).toBeNull()

    // Signed in without a new document: the address is now one that can be installed.
    router.href = '/app?tab=economica'
    rerender(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    expect(screen.getByRole('status')).toHaveTextContent('Installa PigroCRM')
    router.href = '/app/'
  })

  it('is silent in development, where there is no worker to install', () => {
    vi.stubEnv('PROD', false)
    render(<InstallPrompt />)
    fireInstallPrompt()
    engage()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('tells an iOS Safari visitor where the share button is, once', async () => {
    Object.defineProperty(navigator, 'standalone', { value: false, configurable: true })
    const { unmount } = render(<InstallPrompt />)
    engage()
    expect(screen.getByRole('status')).toHaveTextContent('Aggiungi alla schermata Home')
    await userEvent.click(screen.getByRole('button', { name: 'Ho capito' }))
    expect(screen.queryByRole('status')).toBeNull()
    unmount()

    render(<InstallPrompt />)
    engage()
    expect(screen.queryByRole('status')).toBeNull()
  })
})
