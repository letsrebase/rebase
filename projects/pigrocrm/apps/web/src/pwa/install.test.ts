import { describe, expect, it } from 'vitest'
import { shouldShowAndroidPrompt, shouldShowIosHint } from './install'

describe('shouldShowAndroidPrompt', () => {
  const ready = { engaged: true, hasDeferredPrompt: true, dismissed: false }

  it('offers the install once the person has done something and the browser allows it', () => {
    expect(shouldShowAndroidPrompt(ready)).toBe(true)
  })

  it.each([
    ['on first paint', { engaged: false }],
    ['without a browser prompt to show', { hasDeferredPrompt: false }],
    ['after they turned it down', { dismissed: true }],
  ])('does not %s', (_label, change) => {
    expect(shouldShowAndroidPrompt({ ...ready, ...change })).toBe(false)
  })
})

describe('shouldShowIosHint', () => {
  const ready = {
    engaged: true,
    isIos: true,
    isStandalone: false,
    seenBefore: false,
    hiddenThisVisit: false,
  }

  it('points an iOS Safari visitor at the share sheet, once', () => {
    expect(shouldShowIosHint(ready)).toBe(true)
  })

  it.each([
    ['on first paint', { engaged: false }],
    ['off iOS', { isIos: false }],
    ['inside the installed app', { isStandalone: true }],
    ['a second time', { seenBefore: true }],
    ['after it was closed', { hiddenThisVisit: true }],
  ])('does not show it %s', (_label, change) => {
    expect(shouldShowIosHint({ ...ready, ...change })).toBe(false)
  })
})
