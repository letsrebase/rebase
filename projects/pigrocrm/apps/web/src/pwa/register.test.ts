import { describe, expect, it, vi } from 'vitest'
import { startPwa, type PwaEnvironment } from './register'

function setup(overrides: Partial<PwaEnvironment> = {}) {
  const registration = { update: vi.fn().mockResolvedValue(undefined) }
  const register = vi.fn().mockResolvedValue(registration)
  const container = { register } as unknown as ServiceWorkerContainer
  document.head.innerHTML = ''
  const env: PwaEnvironment = {
    production: true,
    pathname: '/studio/app/',
    prefix: '/studio',
    document,
    serviceWorker: container,
    ...overrides,
  }
  return { env, register, registration }
}

describe('startPwa', () => {
  it("links the space's own manifest and registers the space's own worker, scoped to it", async () => {
    const { env, register } = setup()
    Object.defineProperty(document, 'readyState', { value: 'complete', configurable: true })
    startPwa(env)

    expect(document.head.querySelector('link[rel="manifest"]')).toHaveAttribute(
      'href',
      '/studio/app/manifest.webmanifest',
    )
    expect(register).toHaveBeenCalledWith('/studio/app/sw.js', {
      scope: '/studio/app',
      updateViaCache: 'none',
    })
  })

  it('waits for the page to load before it registers', () => {
    const { env, register } = setup()
    Object.defineProperty(document, 'readyState', { value: 'loading', configurable: true })
    startPwa(env)
    expect(register).not.toHaveBeenCalled()
    window.dispatchEvent(new Event('load'))
    expect(register).toHaveBeenCalledTimes(1)
  })

  it('asks for a new worker when the page comes back into view', async () => {
    const { env, registration } = setup()
    Object.defineProperty(document, 'readyState', { value: 'complete', configurable: true })
    startPwa(env)
    await vi.waitFor(() => expect(registration.update).not.toHaveBeenCalled())
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(registration.update).toHaveBeenCalledTimes(1))
  })

  it('sets the page up once it moves onto an installable one, and only once', () => {
    const { env, register } = setup({ pathname: '/app/login', prefix: '' })
    Object.defineProperty(document, 'readyState', { value: 'complete', configurable: true })
    startPwa(env)
    expect(register).not.toHaveBeenCalled()

    // Signed in: the same document, now on the application.
    startPwa({ ...env, pathname: '/app/' })
    startPwa({ ...env, pathname: '/app/customers' })
    expect(register).toHaveBeenCalledTimes(1)
    expect(document.head.querySelectorAll('link[rel="manifest"]')).toHaveLength(1)
  })

  it('does nothing in development: a worker that outlived the dev server would serve stale code', () => {
    const { env, register } = setup({ production: false })
    startPwa(env)
    expect(register).not.toHaveBeenCalled()
    expect(document.head.querySelector('link[rel="manifest"]')).toBeNull()
  })

  it('does nothing on the bare login, which is nobody\'s space', () => {
    const { env, register } = setup({ pathname: '/app/login', prefix: '' })
    startPwa(env)
    expect(register).not.toHaveBeenCalled()
    expect(document.head.querySelector('link[rel="manifest"]')).toBeNull()
  })

  it('still links the manifest where the browser has no service workers', () => {
    const { env } = setup({ serviceWorker: undefined })
    startPwa(env)
    expect(document.head.querySelector('link[rel="manifest"]')).not.toBeNull()
  })

  it('keeps a failed registration away from the application', async () => {
    const { env, register } = setup()
    Object.defineProperty(document, 'readyState', { value: 'complete', configurable: true })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    register.mockRejectedValue(new Error('redirect'))
    startPwa(env)
    await vi.waitFor(() => expect(warn).toHaveBeenCalled())
    warn.mockRestore()
  })
})
