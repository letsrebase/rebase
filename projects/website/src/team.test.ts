import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * team.js is the team builder on the landing's first screen (REB-676): the hub's own
 * form cut to a static page. These drive `mount` on the markup index.html carries, with
 * a fetch of their own, and pin what the visitor reads: the examples fill the box, a
 * short description never leaves the page, the minimal result is the summary, one line
 * per role with the band, the team's band and the door into /hub/team by the proposal's
 * id, decorated by utm.js like every other hub link, and a refusal is the API's own
 * sentence.
 */
const source = readFileSync(join(__dirname, 'team.js'), 'utf-8')
const utmSource = readFileSync(join(__dirname, 'utm.js'), 'utf-8')
const indexHtml = readFileSync(join(__dirname, 'index.html'), 'utf-8')

interface Band {
  min: number
  max: number | null
}

interface TeamBuilder {
  mount: (form: HTMLFormElement, fetchImpl?: typeof fetch) => boolean
  render: (result: HTMLElement, proposal: unknown) => void
  bandLabel: (band: Band) => string
  sentence: (status: number, body: unknown) => string
}

const NBSP = ' '
const DASH = '–'
const DESCRIZIONE =
  'Ci serve una web app per i clienti: pagamenti, dashboard dei conti, API di open banking.'

const PROPOSAL = {
  id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd',
  descrizione: DESCRIZIONE,
  persone: null,
  riassunto: 'Una web app per i clienti di una fintech: chi fa il backend e chi il frontend.',
  luogo: { locale: false, dove: null },
  team: [
    {
      posizione: 1,
      ruolo: 'Backend developer',
      motivazione: 'Ha costruito le API di pagamento di due banche.',
      giorni_settimana: 5,
      scheda: { ruolo: 'Sviluppatore backend', seniority: 'senior', anni: 9, competenze: ['Python'], settori: [], lingue: [], luogo: null, sintesi: 'Nove anni.' },
      modalita: 'remoto',
      fascia: { min: 400, max: 500 },
    },
    {
      posizione: 2,
      ruolo: 'Frontend developer',
      motivazione: 'Porta React.',
      giorni_settimana: 3,
      scheda: { ruolo: 'Sviluppatrice frontend', seniority: 'mid', anni: 1, competenze: ['React'], settori: [], lingue: [], luogo: null, sintesi: 'Interfacce.' },
      modalita: 'ibrido',
      fascia: null,
    },
  ],
  economia: { giorno: null, mese: null, giorni_mese: 22 },
  previous_id: null,
  origine: 'pubblico',
  created_at: '2026-10-07T08:00:00Z',
}

function load(): TeamBuilder {
  new Function(utmSource)()
  new Function(source)()
  const api = (window as Window & { __teamBuilder?: TeamBuilder }).__teamBuilder
  if (!api) throw new Error('team.js did not expose window.__teamBuilder')
  return api
}

/** The hero's own markup, the form and the result, as index.html writes them. */
function fixture() {
  const box = indexHtml.match(/<div class="box team">[\s\S]*?<div class="box community">/)?.[0] ?? ''
  document.body.innerHTML = box.replace('<div class="box community">', '')
  const form = document.querySelector('form[data-team]') as HTMLFormElement
  const result = document.getElementById('team-result') as HTMLElement
  const textarea = form.querySelector('textarea') as HTMLTextAreaElement
  const button = form.querySelector('button[type="submit"]') as HTMLButtonElement
  const error = form.querySelector('.team-error') as HTMLElement
  return { form, result, textarea, button, error }
}

function answer(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const submit = (form: HTMLFormElement) => form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => window.sessionStorage.clear())
afterEach(() => {
  document.body.innerHTML = ''
  window.sessionStorage.clear()
  vi.restoreAllMocks()
})

it('stays small: the first page a visitor loads carries it', () => {
  expect(Buffer.byteLength(source, 'utf-8')).toBeLessThan(12 * 1024)
})

describe('bandLabel', () => {
  it('writes the bands as the hub does, non-breaking space and en dash included', () => {
    const { bandLabel } = load()
    expect(bandLabel({ min: 400, max: 500 })).toBe(`400${DASH}500${NBSP}€ al giorno`)
    expect(bandLabel({ min: 800, max: null })).toBe(`oltre 800${NBSP}€ al giorno`)
    expect(bandLabel({ min: 0, max: 300 })).toBe(`fino a 300${NBSP}€ al giorno`)
    expect(bandLabel({ min: 1000, max: 1150 })).toBe(`1.000${DASH}1.150${NBSP}€ al giorno`)
    // A band that is not one reads as no band, never as «undefined–NaN».
    for (const odd of [null, undefined, {}, { min: '400', max: 500 }, { min: 400 }]) {
      expect(bandLabel(odd as unknown as Band)).toBe('Tariffa da definire')
    }
  })
})

describe('sentence', () => {
  it('is the API’s own, the hub’s for a 429, ours when nothing answered', () => {
    const { sentence } = load()
    expect(sentence(503, { detail: 'Il team builder è spento.' })).toBe('Il team builder è spento.')
    expect(sentence(422, { detail: [{ loc: ['body', 'descrizione'], msg: 'troppo corta', type: 'value_error' }] })).toBe('troppo corta')
    expect(sentence(429, null)).toBe('Troppe richieste da qui. Riprova tra un minuto.')
    expect(sentence(502, null)).toBe('Non siamo riusciti a proporre un team. Riprova.')
    expect(sentence(500, { detail: '' })).toBe('Non siamo riusciti a proporre un team. Riprova.')
  })
})

describe('mount', () => {
  it('takes the markup as it is, and turns the browser’s own validation off for its own sentence', () => {
    const api = load()
    const { form } = fixture()
    expect(form.noValidate).toBe(false)
    expect(api.mount(form)).toBe(true)
    expect(form.noValidate).toBe(true)
  })

  it('refuses a form without its parts rather than failing later', () => {
    const api = load()
    document.body.innerHTML = '<form data-team data-result="#nowhere"><textarea name="descrizione"></textarea></form>'
    expect(api.mount(document.querySelector('form') as HTMLFormElement)).toBe(false)
  })

  it('fills the box from each example, and clears the sentence and the box’s invalid state', async () => {
    const api = load()
    const { form, textarea, error } = fixture()
    api.mount(form)
    error.textContent = 'una frase'
    textarea.setAttribute('aria-invalid', 'true')
    textarea.setAttribute('aria-describedby', 'team-error')
    const chips = [...form.querySelectorAll<HTMLButtonElement>('button[data-example]')]
    expect(chips).toHaveLength(4)
    for (const chip of chips) {
      chip.click()
      expect(textarea.value).toBe(chip.getAttribute('data-example'))
      expect(textarea.value.length).toBeGreaterThanOrEqual(40)
    }
    expect(error.textContent).toBe('')
    expect(textarea.hasAttribute('aria-invalid')).toBe(false)
    expect(textarea.hasAttribute('aria-describedby')).toBe(false)
    expect(document.activeElement).toBe(textarea)
  })

  it('keeps a short description on the page, with the hub’s sentence, and asks nothing', async () => {
    const api = load()
    const fetchSpy = vi.fn()
    const { form, textarea, error } = fixture()
    api.mount(form, fetchSpy as unknown as typeof fetch)
    textarea.value = '  Un sito.  '
    submit(form)
    await flush()
    expect(error.textContent).toBe('Raccontaci qualcosa in più: servono almeno 40 caratteri.')
    // The sentence is about the box, so the box says so to a screen reader, as the hub's does.
    expect(textarea.getAttribute('aria-invalid')).toBe('true')
    expect(textarea.getAttribute('aria-describedby')).toBe('team-error')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it.each([
    ['an empty object', () => answer(200, {})],
    ['no JSON at all', () => new Response('<html>', { status: 200, headers: { 'Content-Type': 'text/html' } })],
  ])('treats a 200 that is not a proposal (%s) as no answer, never a door to nowhere', async (_, body) => {
    const api = load()
    const fetchSpy = vi.fn(async () => body())
    const { form, textarea, error, result } = fixture()
    api.mount(form, fetchSpy as unknown as typeof fetch)
    textarea.value = DESCRIZIONE
    submit(form)
    await flush()
    await flush()
    expect(error.textContent).toBe('Non siamo riusciti a proporre un team. Riprova.')
    expect(textarea.hasAttribute('aria-invalid')).toBe(false)
    expect(result.hidden).toBe(true)
    expect(result.querySelector('a')).toBeNull()
  })

  it('asks the hub for the description alone, says it is reading, and shows the minimal result with the door', async () => {
    const api = load()
    window.history.replaceState({}, '', '/?utm_source=linkedin&utm_campaign=orbita')
    let resolveCall: (response: Response) => void = () => {}
    const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => (resolveCall = resolve)))
    const { form, textarea, button, error, result } = fixture()
    api.mount(form, fetchSpy as unknown as typeof fetch)
    textarea.value = DESCRIZIONE
    submit(form)
    expect(button.disabled).toBe(true)
    expect(button.textContent).toBe('Sto leggendo i profili…')
    expect(form.getAttribute('aria-busy')).toBe('true')
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/hub/team/proposals')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ descrizione: DESCRIZIONE })
    // A second submit while one runs does nothing.
    submit(form)
    expect(fetchSpy).toHaveBeenCalledTimes(1)

    resolveCall(answer(200, PROPOSAL))
    await flush()
    await flush()
    expect(button.disabled).toBe(false)
    expect(button.textContent).toBe('Proponi il team')
    expect(form.hasAttribute('aria-busy')).toBe(false)
    expect(error.textContent).toBe('')
    expect(result.hidden).toBe(false)
    expect(result.querySelector('h2')?.textContent).toBe('La nostra proposta')
    expect(document.activeElement).toBe(result.querySelector('h2'))
    expect(result.querySelector('.team-summary')?.textContent).toBe(PROPOSAL.riassunto)
    const members = [...result.querySelectorAll('.team-member')].map((item) =>
      [...item.children].map((child) => child.textContent),
    )
    expect(members).toEqual([
      ['Backend developer', 'Senior, 9 anni di esperienza', `400${DASH}500${NBSP}€ al giorno`],
      ['Frontend developer', 'Mid, 1 anno di esperienza', 'Tariffa da definire'],
    ])
    // Somebody has no band, so the team has none: never a sum that leaves a person out.
    expect(result.querySelector('.team-total')?.textContent).toBe('Tutto il team: tariffa da definire')
    const door = result.querySelector('a.cta') as HTMLAnchorElement
    expect(door.textContent).toBe('Vedi il team completo')
    expect(door.getAttribute('href')).toBe(
      `/hub/team?proposta=${PROPOSAL.id}&utm_source=linkedin&utm_campaign=orbita&da=home`,
    )
    window.history.replaceState({}, '', '/')
  })

  it('sums the team’s band when everyone has one, and renders again on a second proposal', async () => {
    const api = load()
    const { result } = fixture()
    const whole = {
      ...PROPOSAL,
      team: [PROPOSAL.team[0], { ...PROPOSAL.team[1], fascia: { min: 300, max: 400 } }],
      economia: { giorno: { min: 700, max: 900 }, mese: { min: 15400, max: 19800 }, giorni_mese: 22 },
    }
    api.render(result, whole)
    expect(result.querySelector('.team-total')?.textContent).toBe(`Tutto il team: 700${DASH}900${NBSP}€ al giorno`)
    api.render(result, { ...whole, id: 'beef', riassunto: 'Un altro team.' })
    expect(result.querySelectorAll('h2')).toHaveLength(1)
    expect(result.querySelector('.team-summary')?.textContent).toBe('Un altro team.')
    expect(result.querySelector('a.cta')?.getAttribute('href')).toBe('/hub/team?proposta=beef&da=home')
  })

  it('shows the summary and a door back to the builder when nobody fits', () => {
    const api = load()
    const { result } = fixture()
    api.render(result, { ...PROPOSAL, team: [], riassunto: 'Al momento nessun profilo corrisponde alla richiesta.' })
    expect(result.querySelector('.team-summary')?.textContent).toBe('Al momento nessun profilo corrisponde alla richiesta.')
    expect(result.querySelector('.team-roles')).toBeNull()
    expect(result.querySelector('.team-total')).toBeNull()
    expect(result.querySelector('a.cta')?.textContent).toBe('Riprova nel team builder')
  })

  it.each([
    [503, { detail: 'Il team builder è spento.' }, 'Il team builder è spento.'],
    [503, { detail: 'Troppe richieste in questo momento: riprova tra un minuto.' }, 'Troppe richieste in questo momento: riprova tra un minuto.'],
    [502, { detail: 'Non riesco a proporre un team adesso: riprova tra poco.' }, 'Non riesco a proporre un team adesso: riprova tra poco.'],
    [429, null, 'Troppe richieste da qui. Riprova tra un minuto.'],
  ])('shows the %i the API answered in its own words, and lets the visitor try again', async (status, body, expected) => {
    const api = load()
    const fetchSpy = vi.fn(async () => (body === null ? new Response('', { status }) : answer(status, body)))
    const { form, textarea, button, error, result } = fixture()
    api.mount(form, fetchSpy as unknown as typeof fetch)
    textarea.value = DESCRIZIONE
    submit(form)
    await flush()
    await flush()
    expect(error.textContent).toBe(expected)
    expect(result.hidden).toBe(true)
    expect(button.disabled).toBe(false)
    expect(button.textContent).toBe('Proponi il team')
  })

  it('says so when the network answered nothing at all', async () => {
    const api = load()
    const fetchSpy = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { form, textarea, button, error } = fixture()
    api.mount(form, fetchSpy as unknown as typeof fetch)
    textarea.value = DESCRIZIONE
    submit(form)
    await flush()
    await flush()
    expect(error.textContent).toBe('Non siamo riusciti a proporre un team. Riprova.')
    expect(button.disabled).toBe(false)
  })
})
