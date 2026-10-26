import { describe, expect, it } from 'vitest'
import { isInstallablePage, pwaUrls } from './urls'

describe('pwaUrls', () => {
  it("puts a space's manifest, worker and scope under its own path", () => {
    expect(pwaUrls('/studio')).toEqual({
      scope: '/studio/app',
      manifest: '/studio/app/manifest.webmanifest',
      worker: '/studio/app/sw.js',
    })
  })

  it('puts the root installation under /app/', () => {
    expect(pwaUrls('')).toEqual({
      scope: '/app',
      manifest: '/app/manifest.webmanifest',
      worker: '/app/sw.js',
    })
  })
})

describe('isInstallablePage', () => {
  it('is true inside a space, its login included', () => {
    expect(isInstallablePage('/studio/app/', '/studio')).toBe(true)
    expect(isInstallablePage('/studio/app/login', '/studio')).toBe(true)
  })

  it('is true on the root installation when it is served without a slug', () => {
    expect(isInstallablePage('/app/customers', '')).toBe(true)
    expect(isInstallablePage('/app/', '')).toBe(true)
  })

  it('is false on the pages nobody owns yet: the bare login, signup and link pages', () => {
    for (const page of ['/app/login', '/app/login/', '/app/register', '/app/verify', '/app/invite']) {
      expect(isInstallablePage(page, ''), page).toBe(false)
    }
  })
})
