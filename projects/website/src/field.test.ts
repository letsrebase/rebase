import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'

const source = readFileSync(join(__dirname, 'field.js'), 'utf-8')

type FieldWindow = Window & {
  __pigroField?: {
    noise: (x: number, y: number) => number
    diagonal: (u: number, v: number) => number
    columns: (width: number, cell: number, origin: number) => number
    mount: (canvas: HTMLCanvasElement, options?: object) => void
  }
}

function load() {
  new Function(source)()
  const api = (window as FieldWindow).__pigroField
  if (!api) throw new Error('field.js did not expose window.__pigroField')
  return api
}

describe('field.js', () => {
  beforeEach(() => {
    delete (window as FieldWindow).__pigroField
  })

  it('stays small', () => {
    // Commented source; Vite ships it at about 2.5 KB. It is loaded by two pages.
    expect(Buffer.byteLength(source, 'utf-8')).toBeLessThan(6 * 1024)
  })

  it('makes no request and carries no colour of its own', () => {
    expect(source).not.toMatch(/fetch\(|XMLHttpRequest|import\s|require\(/)
    expect(source).not.toMatch(/localStorage|sessionStorage|document\.cookie|navigator\.sendBeacon/)
    expect(source).not.toMatch(/#[0-9a-fA-F]{6}\b/)
    expect(source).toMatch(/--color-prussian-blue/)
  })

  it('noise is deterministic and stays in [0, 1)', () => {
    const api = load()
    for (const [x, y] of [
      [0.2, 0.7],
      [3.4, 9.1],
      [100.5, 0.25],
      [-4.2, 7.9],
    ]) {
      const a = api.noise(x!, y!)
      expect(a).toBe(api.noise(x!, y!))
      expect(a).toBeGreaterThanOrEqual(0)
      expect(a).toBeLessThan(1)
    }
  })

  describe('the columns (ORB-18)', () => {
    // The three phone widths the issue asks to be rendered, and the two cells the
    // pages use. The origin is what landing.css computes: half the slack, floored.
    const origin = (width: number, cell: number) => Math.floor((width % cell) / 2)

    it.each([
      [360, 14],
      [390, 14],
      [430, 14],
      [375, 14],
      [1280, 16],
    ])('at %ipx with a %ipx cell, the last column is whole and inside the edge', (width, cell) => {
      const cols = load().columns(width, cell, origin(width, cell))
      const rightEdge = origin(width, cell) + cols * cell
      // Painted only up to the last whole cell, and not a whole cell short of the edge.
      expect(rightEdge).toBeLessThanOrEqual(width)
      expect(width - rightEdge).toBeLessThan(cell)
      // The strip left on the right is the strip left on the left, give or take one
      // pixel when the slack is odd.
      expect(Math.abs(width - rightEdge - origin(width, cell))).toBeLessThanOrEqual(1)
    })

    it('never rounds up: 390 wide is 27 columns of 14, not 28', () => {
      const api = load()
      // Math.ceil(390 / 14) is 28, and the 28th column was the one the viewport cut.
      expect(api.columns(390, 14, 0)).toBe(27)
      expect(api.columns(390, 14, 6)).toBe(27)
      expect(api.columns(392, 14, 0)).toBe(28)
    })

    it('is zero, not negative, on a canvas narrower than its origin', () => {
      expect(load().columns(3, 14, 6)).toBe(0)
    })
  })

  it('the default band is a diagonal that empties out at the corners', () => {
    const api = load()
    expect(api.diagonal(0.5, 0.5)).toBe(1)
    expect(api.diagonal(0, 0)).toBeLessThanOrEqual(0)
    expect(api.diagonal(1, 1)).toBeLessThanOrEqual(0)
  })

  describe('the drift', () => {
    type Stubs = { rafCalls: number; timerCalls: number; restore: () => void }

    function stub(reduced: boolean): Stubs {
      const originalMatch = window.matchMedia
      const originalRaf = window.requestAnimationFrame
      const originalTimeout = window.setTimeout
      const originalContext = HTMLCanvasElement.prototype.getContext
      const counters = { rafCalls: 0, timerCalls: 0 }
      window.matchMedia = ((query: string) => ({
        matches: reduced && query.includes('prefers-reduced-motion'),
        media: query,
      })) as typeof window.matchMedia
      window.requestAnimationFrame = (() => {
        counters.rafCalls += 1
        return 1
      }) as typeof window.requestAnimationFrame
      window.setTimeout = (() => {
        counters.timerCalls += 1
        // Counted, never scheduled for real: field.js's own `tick` reschedules
        // itself recursively, and letting even one real 125ms timer through outlives
        // this test, firing later against a torn-down jsdom window (measured: an
        // uncaught "window is not defined" once the suite grew past twelve files).
        return 0
      }) as unknown as typeof window.setTimeout
      // A minimal 2d context: the loop's own scheduling is the subject here, not the pixels.
      HTMLCanvasElement.prototype.getContext = (() => ({
        setTransform() {},
        clearRect() {},
        fillRect() {},
        fillStyle: '',
      })) as unknown as typeof HTMLCanvasElement.prototype.getContext
      return {
        get rafCalls() {
          return counters.rafCalls
        },
        get timerCalls() {
          return counters.timerCalls
        },
        restore() {
          window.matchMedia = originalMatch
          window.requestAnimationFrame = originalRaf
          window.setTimeout = originalTimeout
          HTMLCanvasElement.prototype.getContext = originalContext
        },
      }
    }

    it('is scheduled by a timer when asked to animate', () => {
      const stubs = stub(false)
      try {
        load().mount(document.createElement('canvas'), { cell: 16, animate: true })
        expect(stubs.timerCalls).toBeGreaterThan(0)
      } finally {
        stubs.restore()
      }
    })

    it('never starts when the reader asked for less motion, even if asked to animate', () => {
      // `reduced` is read once, when the script loads, so the stub has to be in place
      // before `load()` -- which is also the order a browser sees.
      const stubs = stub(true)
      try {
        load().mount(document.createElement('canvas'), { cell: 16, animate: true })
        expect(stubs.timerCalls).toBe(0)
      } finally {
        stubs.restore()
      }
    })

    it('never starts when not asked to animate', () => {
      const stubs = stub(false)
      try {
        load().mount(document.createElement('canvas'), { cell: 16 })
        expect(stubs.timerCalls).toBe(0)
      } finally {
        stubs.restore()
      }
    })
  })

  it('does nothing on a canvas with no 2d context, rather than throwing', () => {
    // jsdom has no canvas implementation, which is exactly the case to survive.
    const api = load()
    const canvas = document.createElement('canvas')
    expect(() => api.mount(canvas, { cell: 16 })).not.toThrow()
  })
})
