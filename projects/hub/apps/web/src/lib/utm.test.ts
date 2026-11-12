import { describe, expect, it, vi } from 'vitest'
import { afterEach, beforeEach } from 'vitest'
import { LOGIN_ATTRIBUTION_KEY, ORIGIN_STORAGE_KEY, PREVIOUS_ORIGIN_STORAGE_KEY, PREVIOUS_REFERRAL_STORAGE_KEY, REFERRAL_STORAGE_KEY, UTM_STORAGE_KEY, loginAttribution, readOrigin, readReferral, readUtm, recallUtm, rememberLoginAttribution, rememberUtm, resolveAttribution, resolveOrigin, resolveReferral, resolveUtm } from './utm'

describe('readUtm', () => {
  it('keeps the six UTM keys and nothing else', () => {
    expect(
      readUtm('?utm_source=linkedin&utm_medium=paid&utm_id=123&gclid=x&utm_term=%20'),
    ).toEqual({ utm_source: 'linkedin', utm_medium: 'paid', utm_id: '123' })
  })

  it('stores an unexpanded macro as the literal it was', () => {
    expect(readUtm('?utm_campaign=%7B%7Bcampaign%7D%7D')).toEqual({ utm_campaign: '{{campaign}}' })
  })

  it('is empty for an empty search', () => {
    expect(readUtm('')).toEqual({})
  })
})

describe('resolveUtm, with what the tab remembers (ORB-166)', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => window.sessionStorage.clear())

  it('prefers the URL and remembers it for the tab', () => {
    expect(resolveUtm('?utm_source=linkedin&utm_id=42')).toEqual({ utm_source: 'linkedin', utm_id: '42' })
    expect(window.sessionStorage.getItem(UTM_STORAGE_KEY)).toBe('utm_source=linkedin&utm_id=42')
  })

  it('falls back to what the landing left in the tab when the URL has nothing', () => {
    window.sessionStorage.setItem(UTM_STORAGE_KEY, 'utm_source=linkedin&utm_campaign=orbita&gclid=x')
    expect(resolveUtm('')).toEqual({ utm_source: 'linkedin', utm_campaign: 'orbita' })
    expect(resolveUtm('?perk=guida')).toEqual({ utm_source: 'linkedin', utm_campaign: 'orbita' })
  })

  it('is empty when neither the URL nor the tab knows a campaign', () => {
    expect(resolveUtm('?perk=guida')).toEqual({})
    expect(recallUtm()).toEqual({})
  })

  it('remembers nothing for an empty attribution', () => {
    rememberUtm({})
    expect(window.sessionStorage.getItem(UTM_STORAGE_KEY)).toBeNull()
  })
})

describe('the page the person started from (ORB-167)', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => window.sessionStorage.clear())

  it('reads `from=` when it is a slug, and nothing else', () => {
    expect(readOrigin('?from=pigrocrm&utm_source=linkedin')).toBe('pigrocrm')
    expect(readOrigin('?from=home')).toBe('home')
    expect(readOrigin('?from=%3Cscript%3E')).toBeNull()
    expect(readOrigin('?from=')).toBeNull()
    expect(readOrigin('')).toBeNull()
  })

  it('prefers the URL, remembers it for the tab, and falls back to the tab', () => {
    expect(resolveOrigin('?from=pigrocrm')).toBe('pigrocrm')
    expect(window.sessionStorage.getItem(ORIGIN_STORAGE_KEY)).toBe('pigrocrm')
    expect(resolveOrigin('?perk=guida')).toBe('pigrocrm')
    window.sessionStorage.setItem(ORIGIN_STORAGE_KEY, 'not a slug!')
    expect(resolveOrigin('')).toBeNull()
  })

  it('folds the page into the attribution an application sends', () => {
    expect(resolveAttribution('?utm_source=linkedin&from=home')).toEqual({ utm_source: 'linkedin', origin: 'home' })
    window.sessionStorage.clear()
    expect(resolveAttribution('')).toEqual({})
  })
})

describe('a member’s own link (P-REB-44)', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => window.sessionStorage.clear())

  it('reads `ref=` when it looks like a code, and nothing else', () => {
    expect(readReferral('?ref=ABC123&utm_source=linkedin')).toBe('ABC123')
    expect(readReferral('?ref=%3Cscript%3E')).toBeNull()
    expect(readReferral('?ref=')).toBeNull()
    expect(readReferral('')).toBeNull()
  })

  it('prefers the URL, remembers it for the tab, and falls back to the tab', () => {
    expect(resolveReferral('?ref=ABC123')).toBe('ABC123')
    expect(window.sessionStorage.getItem(REFERRAL_STORAGE_KEY)).toBe('ABC123')
    expect(resolveReferral('?perk=guida')).toBe('ABC123')
    window.sessionStorage.setItem(REFERRAL_STORAGE_KEY, 'not a code!')
    expect(resolveReferral('')).toBeNull()
  })

  it('never rides inside the UTM attribution an application sends', () => {
    expect(resolveAttribution('?ref=ABC123&utm_source=linkedin')).toEqual({ utm_source: 'linkedin' })
  })
})

describe('what a login sends, remembered for the tab by the login page only (REB-455)', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => {
    window.sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('sends and remembers the campaign of its own URL, the page included', () => {
    expect(loginAttribution('?utm_campaign=outreach-2026-09-r2&utm_term=11425b70&from=home')).toEqual({
      utm_campaign: 'outreach-2026-09-r2',
      utm_term: '11425b70',
      origin: 'home',
    })
    expect(loginAttribution('')).toEqual({
      utm_campaign: 'outreach-2026-09-r2',
      utm_term: '11425b70',
      origin: 'home',
    })
  })

  it('lets a visit with its own campaign replace the one remembered', () => {
    rememberLoginAttribution('?utm_campaign=first')
    expect(loginAttribution('?utm_campaign=second')).toEqual({ utm_campaign: 'second' })
    expect(loginAttribution('')).toEqual({ utm_campaign: 'second' })
  })

  it('keeps the campaign when a later visit brings only the page', () => {
    rememberLoginAttribution('?utm_campaign=outreach&utm_term=11425b70')
    expect(loginAttribution('?from=home')).toEqual({
      utm_campaign: 'outreach',
      utm_term: '11425b70',
      origin: 'home',
    })
    expect(loginAttribution('')).toEqual({
      utm_campaign: 'outreach',
      utm_term: '11425b70',
      origin: 'home',
    })
  })

  it('takes the page alone when the tab remembers no campaign', () => {
    expect(loginAttribution('?from=pigrocrm')).toEqual({ origin: 'pigrocrm' })
  })

  it('drops the page of an older campaign when a new campaign arrives without one', () => {
    rememberLoginAttribution('?utm_campaign=first&from=home')
    expect(loginAttribution('?utm_campaign=second')).toEqual({ utm_campaign: 'second' })
    expect(loginAttribution('')).toEqual({ utm_campaign: 'second' })
  })

  it('keeps what an earlier visit remembered when a later one has no campaign', () => {
    rememberLoginAttribution('?utm_campaign=outreach')
    rememberLoginAttribution('')
    rememberLoginAttribution('?perk=guida')
    expect(loginAttribution('')).toEqual({ utm_campaign: 'outreach' })
  })

  it('never reads the campaign or the page the landing and the wizards leave in the tab', () => {
    window.sessionStorage.setItem(UTM_STORAGE_KEY, 'utm_source=linkedin&utm_campaign=ads')
    window.sessionStorage.setItem(ORIGIN_STORAGE_KEY, 'home')
    expect(loginAttribution('')).toEqual({})
    expect(window.sessionStorage.getItem(LOGIN_ATTRIBUTION_KEY)).toBeNull()
  })

  it('reads a remembered value back through the same checks as a URL', () => {
    window.sessionStorage.setItem(LOGIN_ATTRIBUTION_KEY, 'utm_campaign=ok&gclid=x&from=%3Cscript%3E')
    expect(loginAttribution('')).toEqual({ utm_campaign: 'ok' })
  })

  it('answers from the URL alone when the storage refuses to be written or read', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    expect(loginAttribution('?utm_campaign=outreach')).toEqual({ utm_campaign: 'outreach' })
    expect(loginAttribution('')).toEqual({})
  })
})

describe('the names of the 2026-11 English rename (links and keys already out in the world)', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => {
    window.sessionStorage.clear()
    vi.useRealTimers()
  })

  it('still accepts `da=` (the page) and `rif=` (the referral code), and the new names win', () => {
    expect(readOrigin('?da=pigrocrm')).toBe('pigrocrm')
    expect(readReferral('?rif=ABC123')).toBe('ABC123')
    expect(readOrigin('?da=home&from=pigrocrm')).toBe('pigrocrm')
    expect(readReferral('?rif=OLD1&ref=NEW1')).toBe('NEW1')
    expect(resolveAttribution('?utm_source=linkedin&da=home&rif=ABC123')).toEqual({ utm_source: 'linkedin', origin: 'home' })
    expect(window.sessionStorage.getItem(ORIGIN_STORAGE_KEY)).toBe('home')
    expect(resolveReferral('?rif=ABC123')).toBe('ABC123')
    expect(window.sessionStorage.getItem(REFERRAL_STORAGE_KEY)).toBe('ABC123')
  })

  it('writes the new names', () => {
    expect([ORIGIN_STORAGE_KEY, REFERRAL_STORAGE_KEY]).toEqual(['rebase.from', 'rebase.ref'])
    expect([PREVIOUS_ORIGIN_STORAGE_KEY, PREVIOUS_REFERRAL_STORAGE_KEY]).toEqual(['rebase.da', 'rebase.rif'])
    loginAttribution('?utm_source=mail&da=home')
    expect(window.sessionStorage.getItem(LOGIN_ATTRIBUTION_KEY)).toBe('utm_source=mail&from=home')
  })

  it('reads a login attribution stored under the old `da` name', () => {
    window.sessionStorage.setItem(LOGIN_ATTRIBUTION_KEY, 'utm_source=mail&da=home')
    expect(loginAttribution('')).toEqual({ utm_source: 'mail', origin: 'home' })
  })

  it('reads rebase.da and rebase.rif once, moves them to rebase.from and rebase.ref, and drops the old ones', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-20T10:00:00Z'))
    window.sessionStorage.setItem(PREVIOUS_ORIGIN_STORAGE_KEY, 'pigrocrm')
    window.sessionStorage.setItem(PREVIOUS_REFERRAL_STORAGE_KEY, 'ABC123')
    expect(resolveOrigin('')).toBe('pigrocrm')
    expect(resolveReferral('')).toBe('ABC123')
    expect(window.sessionStorage.getItem(ORIGIN_STORAGE_KEY)).toBe('pigrocrm')
    expect(window.sessionStorage.getItem(REFERRAL_STORAGE_KEY)).toBe('ABC123')
    expect(window.sessionStorage.getItem(PREVIOUS_ORIGIN_STORAGE_KEY)).toBeNull()
    expect(window.sessionStorage.getItem(PREVIOUS_REFERRAL_STORAGE_KEY)).toBeNull()
  })

  it('prefers the new key when both exist, and still checks the shape of the old one', () => {
    window.sessionStorage.setItem(ORIGIN_STORAGE_KEY, 'home')
    window.sessionStorage.setItem(PREVIOUS_ORIGIN_STORAGE_KEY, 'pigrocrm')
    expect(resolveOrigin('')).toBe('home')
    window.sessionStorage.clear()
    window.sessionStorage.setItem(PREVIOUS_REFERRAL_STORAGE_KEY, 'not a code!')
    expect(resolveReferral('')).toBeNull()
  })

  it('writes the new key only, dropping the old one a tab still held', () => {
    window.sessionStorage.setItem(PREVIOUS_ORIGIN_STORAGE_KEY, 'home')
    window.sessionStorage.setItem(PREVIOUS_REFERRAL_STORAGE_KEY, 'OLD1')
    resolveOrigin('?from=pigrocrm')
    resolveReferral('?ref=NEW1')
    expect(window.sessionStorage.getItem(PREVIOUS_ORIGIN_STORAGE_KEY)).toBeNull()
    expect(window.sessionStorage.getItem(PREVIOUS_REFERRAL_STORAGE_KEY)).toBeNull()
  })

  it('stops reading the old keys after 30 days (2026-12-03) and drops them', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-12-03T00:00:01Z'))
    window.sessionStorage.setItem(PREVIOUS_ORIGIN_STORAGE_KEY, 'pigrocrm')
    window.sessionStorage.setItem(PREVIOUS_REFERRAL_STORAGE_KEY, 'ABC123')
    expect(resolveOrigin('')).toBeNull()
    expect(resolveReferral('')).toBeNull()
    expect(window.sessionStorage.getItem(PREVIOUS_ORIGIN_STORAGE_KEY)).toBeNull()
    expect(window.sessionStorage.getItem(PREVIOUS_REFERRAL_STORAGE_KEY)).toBeNull()
    expect(window.sessionStorage.getItem(ORIGIN_STORAGE_KEY)).toBeNull()
  })
})
