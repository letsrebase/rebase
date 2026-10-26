/**
 * The weekly digest's opt-out link (`/app/settings/profile?da=digest`, spec 2026-09-16
 * §3.6) used to land on the digest switch. The switch moved to Notifiche (REB-735, rule R4 of
 * REB-771), so the link is sent there with its whole query string, and a plain visit to
 * Profilo is left where it is.
 */
import { isRedirect } from '@tanstack/react-router'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, createFileRoute: () => (options: object) => ({ options }) }
})

import { Route } from './profile'

type Options = {
  beforeLoad: (context: { search: { da?: string } }) => void
  validateSearch: (search: Record<string, unknown>) => { da?: string }
}
const options = (Route as unknown as { options: Options }).options

function redirected(search: { da?: string }) {
  try {
    options.beforeLoad({ search })
  } catch (thrown) {
    return isRedirect(thrown) ? thrown.options : thrown
  }
  return null
}

describe('the Profilo route', () => {
  it("sends the digest's opt-out link to Notifiche, keeping ?da=digest", () => {
    expect(redirected({ da: 'digest' })).toMatchObject({
      to: '/app/settings/notifications',
      search: { da: 'digest' },
    })
  })

  it('leaves a plain visit, and any other origin, on Profilo', () => {
    expect(redirected({})).toBeNull()
    expect(redirected({ da: 'altro' })).toBeNull()
  })

  it('reads da as a string and drops what is not one', () => {
    expect(options.validateSearch({ da: 'digest', a: 1 })).toEqual({ da: 'digest' })
    expect(options.validateSearch({ da: 5 })).toEqual({ da: undefined })
  })
})
