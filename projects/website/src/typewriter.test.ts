import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const source = readFileSync(join(__dirname, 'typewriter.js'), 'utf-8')
const pages = {
  'index.html': readFileSync(join(__dirname, 'index.html'), 'utf-8'),
}
const scripts = {
  'landing.js': readFileSync(join(__dirname, 'landing.js'), 'utf-8'),
}

type Typewriter = {
  mount: (
    el: HTMLElement,
    options?: { type?: number; erase?: number; hold?: number; pause?: number },
  ) => { stop: () => void }
}
type TypewriterWindow = Window & { __typewriter?: Typewriter }

// The roles Ivan can edit, in the order the title types them. The first is the one
// the page ships with, so the title reads "Developer, ma non da soli." before the
// script runs and when it never does.
const ROLES = ['Developer', 'AI engineer', 'CTO', 'Fractional CTO', 'Tech lead', 'Freelance']

// The whole first line, as both pages write it. The screen-reader span names three
// roles once, comma included, and is the accessible first line in every state; the
// visible word and its comma sit in one aria-hidden span, so the sentence a screen
// reader hears does not depend on whether the script ran, was stopped by reduced
// motion, or never loaded. The comma is in the hidden span with the word because the
// sr-only span is absolutely positioned, which the accessible name treats as a block
// and pads with a space: a comma left outside read as "CTO , ma".
const H1_LINE =
  '<span class="sr-only">Developer, AI engineer, CTO,</span><span aria-hidden="true">' +
  `<span class="role" data-roles="${ROLES.join('|')}">Developer</span>,</span><br />ma non da soli.</h1>`

function load() {
  new Function(source)()
  const api = (window as TypewriterWindow).__typewriter
  if (!api) throw new Error('typewriter.js did not expose window.__typewriter')
  return api
}

function reducedMotion(reduced: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduced && query.includes('prefers-reduced-motion'),
    media: query,
  })) as typeof window.matchMedia
}

function fixture(roles = ROLES) {
  const el = document.createElement('span')
  el.className = 'role'
  el.setAttribute('data-roles', roles.join('|'))
  el.textContent = roles[0]!
  return el
}

/** Every distinct text the element shows while `ms` pass, in order. */
function trace(el: HTMLElement, ms: number) {
  const seen = [el.textContent]
  for (let t = 0; t < ms; t += 5) {
    vi.advanceTimersByTime(5)
    if (el.textContent !== seen[seen.length - 1]) seen.push(el.textContent)
  }
  return seen
}

describe('typewriter.js', () => {
  const originalMatchMedia = window.matchMedia

  beforeEach(() => {
    delete (window as TypewriterWindow).__typewriter
    vi.useFakeTimers()
    reducedMotion(false)
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    window.matchMedia = originalMatchMedia
  })

  it('stays small', () => {
    // One title on two pages: it has to cost less than the words it types.
    expect(Buffer.byteLength(source, 'utf-8')).toBeLessThan(2 * 1024)
  })

  it('makes no request and carries no colour of its own', () => {
    expect(source).not.toMatch(/fetch\(|XMLHttpRequest|import\s|require\(/)
    expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie|navigator\.sendBeacon/)
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\.style\b|rgb\(/)
    // The cursor is CSS, switched on by a class; the script never draws it.
    expect(source).toMatch(/is-typing/)
  })

  it('starts from the word already in the element and holds it', () => {
    const el = fixture()
    load().mount(el)
    expect(el.textContent).toBe('Developer')
    vi.advanceTimersByTime(1799)
    expect(el.textContent).toBe('Developer')
  })

  it('hides the moving word from the accessibility tree and switches the cursor on', () => {
    const el = fixture()
    load().mount(el)
    expect(el.getAttribute('aria-hidden')).toBe('true')
    expect(el.classList.contains('is-typing')).toBe(true)
  })

  it('deletes it a letter at a time, 35 ms apart', () => {
    const el = fixture()
    load().mount(el)
    vi.advanceTimersByTime(1800)
    expect(el.textContent).toBe('Develope')
    vi.advanceTimersByTime(35)
    expect(el.textContent).toBe('Develop')
    vi.advanceTimersByTime(35 * 7)
    expect(el.textContent).toBe('')
  })

  it('pauses on the empty line, then types the next word a letter at a time, 60 ms apart', () => {
    const el = fixture()
    load().mount(el)
    vi.advanceTimersByTime(1800 + 35 * 8)
    expect(el.textContent).toBe('')
    vi.advanceTimersByTime(299)
    expect(el.textContent).toBe('')
    vi.advanceTimersByTime(1)
    expect(el.textContent).toBe('A')
    vi.advanceTimersByTime(60)
    expect(el.textContent).toBe('AI')
    vi.advanceTimersByTime(60 * 9)
    expect(el.textContent).toBe('AI engineer')
  })

  it('holds a full word for 1800 ms before deleting it', () => {
    const el = fixture()
    load().mount(el)
    vi.advanceTimersByTime(1800 + 35 * 8 + 300 + 60 * 10)
    expect(el.textContent).toBe('AI engineer')
    vi.advanceTimersByTime(1799)
    expect(el.textContent).toBe('AI engineer')
    vi.advanceTimersByTime(1)
    expect(el.textContent).toBe('AI enginee')
  })

  it('wraps around to the first word after the last', () => {
    const el = fixture(['Developer', 'CTO'])
    load().mount(el)
    const words = trace(el, 8_000).filter((text) => ['Developer', 'CTO', ''].includes(text!))
    expect(words).toEqual(['Developer', '', 'CTO', '', 'Developer', '', 'CTO'])
  })

  it('takes its timings from the options', () => {
    const el = fixture(['Developer', 'CTO'])
    load().mount(el, { hold: 100, erase: 10, pause: 20, type: 10 })
    vi.advanceTimersByTime(100)
    expect(el.textContent).toBe('Develope')
    vi.advanceTimersByTime(10 * 8)
    expect(el.textContent).toBe('')
    vi.advanceTimersByTime(20)
    expect(el.textContent).toBe('C')
    vi.advanceTimersByTime(10 * 2)
    expect(el.textContent).toBe('CTO')
  })

  it('does nothing when the reader asked for less motion: the static word stays', () => {
    reducedMotion(true)
    const el = fixture()
    const handle = load().mount(el)
    vi.advanceTimersByTime(20_000)
    expect(el.textContent).toBe('Developer')
    expect(el.hasAttribute('aria-hidden')).toBe(false)
    expect(el.classList.contains('is-typing')).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    expect(() => handle.stop()).not.toThrow()
  })

  it('does nothing on an element with no roles to type', () => {
    const el = document.createElement('span')
    el.textContent = 'Developer'
    load().mount(el)
    vi.advanceTimersByTime(20_000)
    expect(el.textContent).toBe('Developer')
    expect(el.classList.contains('is-typing')).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('stop() halts the timers where they are', () => {
    const el = fixture()
    const handle = load().mount(el)
    vi.advanceTimersByTime(1800)
    expect(el.textContent).toBe('Develope')
    handle.stop()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(20_000)
    expect(el.textContent).toBe('Develope')
  })

  describe.each(Object.keys(pages) as (keyof typeof pages)[])('%s', (name) => {
    const page = pages[name]

    it('carries the title with the roles to type and the line a screen reader hears', () => {
      // Without the script the title reads "Developer, ma non da soli." on the screen
      // and "Developer, AI engineer, CTO, ma non da soli." to a screen reader; with it
      // the screen changes and the screen reader hears the same sentence.
      expect(page.match(/<h1[^>]*>/g)).toHaveLength(1)
      expect(page).toContain(H1_LINE)
    })

    it('loads the script before field.js, like field.js before the page script', () => {
      expect(page).toMatch(
        /<script type="module" src="\.\/typewriter\.js"><\/script>\s*<script type="module" src="\.\/field\.js">/,
      )
      expect(page.match(/typewriter\.js/g)).toHaveLength(1)
    })
  })

  describe.each(Object.keys(scripts) as (keyof typeof scripts)[])('%s', (name) => {
    it('mounts the typewriter on the title, and survives its absence', () => {
      const js = scripts[name]
      expect(js).toMatch(/window\.__typewriter/)
      expect(js).toMatch(/querySelector\('h1 \.role'\)/)
    })
  })
})
