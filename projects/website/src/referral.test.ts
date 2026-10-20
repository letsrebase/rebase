import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * referral.js turns the two rates `GET /api/hub/referral-rates` answers into the
 * landing band's figures (REB-711). The arithmetic is the member area's
 * (`projects/hub/apps/web/src/lib/referral.ts`), so these are the same cases its own
 * test holds, plus what the page does when a rate cannot be read.
 */
const source = readFileSync(join(__dirname, 'referral.js'), 'utf-8')
const indexHtml = readFileSync(join(__dirname, 'index.html'), 'utf-8')

interface Rates {
  rate_freelancer: string | null
  rate_company: string | null
}

interface Referral {
  formatRate: (rate: unknown) => string | null
  rateWithArticle: (rate: unknown) => string | null
  roundedShare: (cents: number, units: number) => number
  referralExample: (rates: Rates) => { margin: number; freelancer: number | null; company: number | null }
  exampleText: (rates: Rates) => string | null
  readRates: (fetchImpl: unknown) => Promise<Rates>
  render: (root: ParentNode, rates: Rates) => number
  URL_RATES: string
}

function load(): Referral {
  new Function(source)()
  const api = (window as Window & { __referral?: Referral }).__referral
  if (!api) throw new Error('referral.js did not expose window.__referral')
  return api
}

/** The band as the page ships it, cut out of index.html so the test cannot drift from it. */
function band(): string {
  const html = indexHtml.match(/<section class="band referral"[\s\S]*?<\/section>/)?.[0]
  if (!html) throw new Error('index.html has no referral band')
  return html
}

const plain = (text: string | null | undefined) => (text ?? '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim()
const answer = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body })

let referral: Referral

beforeEach(() => {
  document.body.innerHTML = ''
  referral = load()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('formatRate and rateWithArticle', () => {
  it('writes a four-place fraction the Italian way', () => {
    expect(referral.formatRate('0.1000')).toBe('10%')
    expect(referral.formatRate('0.3000')).toBe('30%')
    expect(referral.formatRate('0.1250')).toBe('12,5%')
    expect(referral.formatRate('0.0005')).toBe('0,05%')
    expect(referral.formatRate('1.0000')).toBe('100%')
  })

  it('takes the article its reading takes: il 10%, l\'8%, l\'11%, l\'80%, l\'1%, lo 0,5%', () => {
    expect(referral.rateWithArticle('0.1000')).toBe('il 10%')
    expect(referral.rateWithArticle('0.0800')).toBe("l'8%")
    expect(referral.rateWithArticle('0.1100')).toBe("l'11%")
    expect(referral.rateWithArticle('0.8000')).toBe("l'80%")
    expect(referral.rateWithArticle('0.0100')).toBe("l'1%")
    expect(referral.rateWithArticle('0.0050')).toBe('lo 0,5%')
  })

  it('says nothing for a value that is not a fraction of one', () => {
    for (const bad of [null, undefined, '', 'abc', '-0.1000', '1.5000', '0,1', 0.1, {}]) {
      expect(referral.formatRate(bad), String(bad)).toBeNull()
      expect(referral.rateWithArticle(bad), String(bad)).toBeNull()
    }
  })
})

describe('the worked example', () => {
  it('is 500 euro a day, 400 to the freelance, 20 days: a 2.000,00 margin, 200,00 and 600,00 today', () => {
    const example = referral.referralExample({ rate_freelancer: '0.1000', rate_company: '0.3000' })
    expect(example).toEqual({ margin: 200000, freelancer: 20000, company: 60000 })
    expect(plain(referral.exampleText({ rate_freelancer: '0.1000', rate_company: '0.3000' }))).toBe(
      "Un incarico da 500 € al giorno, 400 € al freelance, 20 giorni: il margine di rebase è 2.000,00 €. Se l'hai portato tu, ricevi 200,00 € per un talento, 600,00 € per un'azienda.",
    )
  })

  it('follows the rates, to the cent, rounding half to even', () => {
    expect(referral.referralExample({ rate_freelancer: '0.1250', rate_company: '0.3333' })).toMatchObject({
      freelancer: 25000,
      company: 66660,
    })
    expect(referral.roundedShare(1, 5000)).toBe(0) // 0.5 cents goes to the even neighbour
    expect(referral.roundedShare(3, 5000)).toBe(2) // 1.5 cents goes to 2
    expect(referral.roundedShare(5, 5000)).toBe(2) // 2.5 cents goes to 2
  })

  it('names only the reward whose rate reads, and nothing when neither does', () => {
    const only = plain(referral.exampleText({ rate_freelancer: '0.1000', rate_company: null }))
    expect(only).toContain('200,00 € per un talento')
    expect(only).not.toContain("per un'azienda")
    const none = { rate_freelancer: null, rate_company: null }
    expect(referral.exampleText(none)).toBeNull()
  })
})

describe('readRates', () => {
  it('reads both rates from the same-origin endpoint, without credentials', async () => {
    const fetchImpl = answer(200, { rate_freelancer: '0.1000', rate_company: '0.3000' })
    await expect(referral.readRates(fetchImpl)).resolves.toEqual({ rate_freelancer: '0.1000', rate_company: '0.3000' })
    expect(fetchImpl).toHaveBeenCalledWith('/api/hub/referral-rates', { credentials: 'omit' })
  })

  it('drops everything on a non-200, a network error, a body that is not JSON or no fetch at all', async () => {
    const none = { rate_freelancer: null, rate_company: null }
    await expect(referral.readRates(answer(503, { detail: 'no' }))).resolves.toEqual(none)
    await expect(referral.readRates(vi.fn().mockRejectedValue(new TypeError('offline')))).resolves.toEqual(none)
    const broken = vi.fn().mockResolvedValue({ ok: true, json: async () => Promise.reject(new SyntaxError('x')) })
    await expect(referral.readRates(broken)).resolves.toEqual(none)
    await expect(referral.readRates(answer(200, null))).resolves.toEqual(none)
    await expect(referral.readRates(undefined)).resolves.toEqual(none)
  })

  it('drops only the rate that does not read', async () => {
    const fetchImpl = answer(200, { rate_freelancer: 'ten', rate_company: '0.3000' })
    await expect(referral.readRates(fetchImpl)).resolves.toEqual({ rate_freelancer: null, rate_company: '0.3000' })
  })
})

describe('the band, rendered', () => {
  const row = (name: string) => document.querySelector(`[data-rate="${name}"]`) as HTMLElement
  const part = (name: string, selector: string) => row(name).querySelector(selector) as HTMLElement

  beforeEach(() => {
    document.body.innerHTML = band()
  })

  it('ships with no figure, no bar and no example in the markup, and the «una quota» sentence', () => {
    expect(band()).not.toMatch(/\d+\s*%/)
    expect(band()).not.toMatch(/\d[\d.]*,\d\d\s*€/)
    for (const name of ['rate_freelancer', 'rate_company']) {
      expect(part(name, '[data-figure]').hidden).toBe(true)
      expect(part(name, '[data-bar]').hidden).toBe(true)
      expect(plain(part(name, '[data-fallback]').textContent)).toContain('una quota del margine di rebase')
    }
    expect((document.querySelector('[data-example]') as HTMLElement).hidden).toBe(true)
  })

  it('shows each figure, its bar at the rate\'s share, and the example, when both read', () => {
    expect(referral.render(document, { rate_freelancer: '0.1000', rate_company: '0.3000' })).toBe(2)
    expect(plain(part('rate_freelancer', '[data-figure]').textContent)).toBe('10%')
    expect(plain(part('rate_company', '[data-figure]').textContent)).toBe('30%')
    expect(part('rate_freelancer', '[data-bar]').style.getPropertyValue('--share')).toBe('10%')
    expect(part('rate_company', '[data-bar]').style.getPropertyValue('--share')).toBe('30%')
    expect(part('rate_company', '[data-bar]').getAttribute('aria-label')).toBe(
      'Ricevi il 30% del margine di rebase sul primo incarico',
    )
    expect(part('rate_company', '[data-fallback]').hidden).toBe(true)
    const example = document.querySelector('[data-example]') as HTMLElement
    expect(example.hidden).toBe(false)
    expect(plain(example.textContent)).toContain('Esempio con numeri inventati')
    expect(plain(example.textContent)).toContain('200,00 € per un talento, 600,00 € per un\'azienda')
  })

  it('drops the figure of a rate that does not read and keeps the other', () => {
    expect(referral.render(document, { rate_freelancer: null, rate_company: '0.3000' })).toBe(1)
    expect(part('rate_freelancer', '[data-figure]').hidden).toBe(true)
    expect(part('rate_freelancer', '[data-bar]').hidden).toBe(true)
    expect(part('rate_freelancer', '[data-fallback]').hidden).toBe(false)
    expect(part('rate_company', '[data-figure]').hidden).toBe(false)
    expect(plain(document.querySelector('[data-example-text]')?.textContent)).not.toContain('per un talento')
  })

  it('draws the bar for any rate: no fill at 0, a wrapped caption from 60%, 12,5% as 12.5% wide', () => {
    referral.render(document, { rate_freelancer: '0.0000', rate_company: '0.8500' })
    expect((part('rate_freelancer', '.split-fill') as HTMLElement).hidden).toBe(true)
    expect(part('rate_freelancer', '[data-bar]').hasAttribute('data-wide')).toBe(false)
    expect(part('rate_company', '[data-bar]').hasAttribute('data-wide')).toBe(true)
    expect(part('rate_company', '[data-bar]').style.getPropertyValue('--share')).toBe('85%')
    expect(plain(part('rate_company', '[data-figure]').textContent)).toBe('85%')
    document.body.innerHTML = band()
    referral.render(document, { rate_freelancer: '0.1250', rate_company: '1.0000' })
    expect(part('rate_freelancer', '[data-bar]').style.getPropertyValue('--share')).toBe('12.5%')
    expect(plain(part('rate_freelancer', '[data-figure]').textContent)).toBe('12,5%')
    expect(part('rate_company', '[data-bar]').style.getPropertyValue('--share')).toBe('100%')
  })

  it('treats a rate out of range as unreadable: no figure, no bar label, the sentence stays', () => {
    expect(referral.render(document, { rate_freelancer: '1.5000', rate_company: null })).toBe(0)
    expect(part('rate_freelancer', '[data-bar]').hasAttribute('aria-label')).toBe(false)
    expect(part('rate_freelancer', '[data-fallback]').hidden).toBe(false)
  })

  it('leaves the band as shipped, example hidden too, when neither rate reads', () => {
    expect(referral.render(document, { rate_freelancer: null, rate_company: null })).toBe(0)
    expect((document.querySelector('[data-example]') as HTMLElement).hidden).toBe(true)
    expect(part('rate_freelancer', '[data-fallback]').hidden).toBe(false)
  })
})

describe('the band as shipped', () => {
  it('has the two doors, the first-login line and no rate typed in', () => {
    const html = band()
    expect(html).toContain('href="/hub/freelance"')
    expect(html).toContain('href="/hub/login"')
    expect(html).toContain('Conta quando la persona che porti ha fatto il suo primo accesso.')
    expect(html).toContain('rebase conferma il')
    expect(html).not.toMatch(/style="[^"]*#/)
  })
})
