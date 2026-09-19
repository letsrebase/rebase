import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/**
 * What only a browser can answer about the system in `tokens.css`.
 *
 * `tokens.test.ts` reads the stylesheet and proves what it declares: every colour
 * resolves through the brand palette, the radius scale is zero, the shadows are
 * offsets with no blur, the chart mixes clear their thresholds. None of that is what a
 * visitor sees, because between a declaration and a pixel sit Tailwind's utilities, the
 * cascade and the browser's own colour maths. This suite asks Chromium instead, on the
 * gallery, which renders every primitive in every variant and state.
 *
 * Deliberately not a second contrast implementation: axe measures the pairs. Until
 * REB-301 three test files each carried their own WCAG formula (the maths now lives in
 * `@rebase/brand/contrast`), and re-deriving the cascade in jsdom on top of that would
 * have been a fourth thing to keep true. The browser already knows.
 */

const OVERLAYS = ['dialog', 'sheet', 'menu', 'popover', 'select'] as const

/**
 * The violations this page is known to carry, asserted exactly rather than filtered
 * away: a new one fails, and so does fixing one of these without deleting its line,
 * which is what makes somebody read the reason.
 *
 * - `color-contrast`: the watermelon as *text*. It measures 4.17:1 on the Paper ground
 *   and 3.57:1 on its own 10% tint, against the 4.5:1 AA needs, so the destructive
 *   badge and the destructive button fail as text while white-on-watermelon (4.67:1)
 *   passes. That is the palette's, not this page's: the ramp has one darkened step
 *   (`--color-watermelon-strong`, added for white text on a fill) and no step dark
 *   enough to be read as text on a light ground. REB-307.
 * - `aria-hidden-focus`: with a menu or a select open, Radix marks the rest of the
 *   document `aria-hidden` while its trigger stays in the tab order, which axe reads as
 *   a focusable element inside a hidden subtree. It is radix-ui's own focus-scope
 *   behaviour, identical in both products. REB-308.
 */
const KNOWN: Record<string, string[]> = {
  '/': ['color-contrast'],
  '/?open=dialog': ['color-contrast'],
  '/?open=sheet': [],
  '/?open=menu': ['aria-hidden-focus'],
  '/?open=popover': ['color-contrast'],
  '/?open=select': ['aria-hidden-focus'],
}

async function axeIds(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  return [
    ...new Set(
      violations
        .filter((v) => v.impact === 'critical' || v.impact === 'serious' || v.impact === 'moderate')
        .map((v) => v.id),
    ),
  ].sort()
}

async function computedOf(page: Page, selector: string, property: string): Promise<string> {
  return page.evaluate(
    ([sel, prop]) => {
      const el = document.querySelector(sel as string)
      if (!el) throw new Error(`${sel} is not on the page`)
      return getComputedStyle(el).getPropertyValue(prop as string)
    },
    [selector, property],
  )
}

test.describe('the gallery renders the system', () => {
  test('carries exactly the accessibility findings that are written down', async ({ page }) => {
    await page.goto('/')
    expect(await axeIds(page), 'axe on the gallery').toEqual(KNOWN['/'])
  })

  for (const overlay of OVERLAYS) {
    test(`carries exactly the written-down findings with the ${overlay} open`, async ({ page }) => {
      const path = `/?open=${overlay}`
      await page.goto(path)
      // The surface is portalled and animated: wait for the element, not a timeout.
      await expect(page.locator('[data-slot$="-content"]').first()).toBeVisible()
      expect(await axeIds(page), `axe with ${overlay} open`).toEqual(KNOWN[path])
    })
  }

  test('measures the watermelon text pairs the palette cannot yet clear', async ({ page }) => {
    // The numbers behind REB-307, read off the render rather than recomputed: if a
    // palette change fixes them, this fails and the exclusion above goes with it.
    await page.goto('/')
    const pairs = await page.evaluate(() => {
      const badge = document.querySelector('[data-slot="badge"][data-variant="destructive"]')
      const onCard = document.querySelector('[data-slot="card"] [data-slot="button"][data-variant="link"]')
      const cs = (el: Element | null) => (el ? getComputedStyle(el) : null)
      return {
        destructiveText: cs(badge)?.color ?? null,
        linkOnCardText: cs(onCard)?.color ?? null,
        cardBackground: cs(document.querySelector('[data-slot="card"]'))?.backgroundColor ?? null,
      }
    })
    expect(pairs.destructiveText).toBe('rgb(229, 19, 62)')
    expect(pairs.linkOnCardText).toBe('rgb(229, 19, 62)')
    // White, which is the only ground this colour clears AA on (4.67:1).
    expect(pairs.cardBackground).toBe('rgb(255, 255, 255)')
  })

  test('computes no radius anywhere, other than the pill that asks for one', async ({ page }) => {
    await page.goto('/')
    const radii = await page.evaluate(() => {
      const found = new Map<string, string>()
      for (const el of document.querySelectorAll('*')) {
        const radius = getComputedStyle(el).borderTopLeftRadius
        if (radius !== '0px') found.set(radius, (el as HTMLElement).className.toString().slice(0, 80))
      }
      return [...found.entries()]
    })
    // `rounded-full` is the status pill and the dot, which the record keeps round on
    // purpose; a browser reports it as a huge pixel value, never as a scale step.
    const unexpected = radii.filter(([radius]) => Number.parseFloat(radius) < 1000)
    expect(unexpected, 'elements with a radius the scale does not give them').toEqual([])
  })

  test('casts one ink step and no blur, on the surfaces that float and nowhere else', async ({ page }) => {
    await page.goto('/?open=dialog')
    await expect(page.locator('[data-slot="dialog-content"]')).toBeVisible()
    const shadows = await page.evaluate(() => {
      const out: { shadow: string; slot: string }[] = []
      for (const el of document.querySelectorAll('*')) {
        const shadow = getComputedStyle(el).boxShadow
        if (shadow === 'none') continue
        out.push({ shadow, slot: (el as HTMLElement).dataset.slot ?? (el as HTMLElement).tagName.toLowerCase() })
      }
      return out
    })
    expect(shadows.length, 'something should be floating').toBeGreaterThan(0)
    for (const { shadow, slot } of shadows) {
      // Every layer a browser reports is `<colour> <x> <y> <blur> <spread>`; the ones
      // that carry a colour and no offsets are the ring, which is a border drawn as a
      // shadow. What may not appear is a blur radius.
      for (const layer of shadow.split(/,(?![^(]*\))/)) {
        const lengths = layer.match(/(-?[\d.]+)px/g) ?? []
        const blur = lengths[2]
        expect(blur ?? '0px', `${slot}: ${layer.trim()}`).toBe('0px')
      }
    }
  })

  test('draws every line as the ink itself, at one of the two widths', async ({ page }) => {
    await page.goto('/')
    const ink = await computedOf(page, ':root', '--color-prussian-blue')
    expect(ink.trim()).toBe('#011936')
    const lines = await page.evaluate(() => {
      const out = new Set<string>()
      // Two deliberate exceptions: a field the caller marks invalid draws the
      // destructive line, and a checked checkbox draws the primary one, which is the
      // fill it is about to carry.
      const resting = ':not([aria-invalid]):not([data-state="checked"])'
      for (const el of document.querySelectorAll(
        ['card', 'input', 'textarea', 'checkbox', 'select-trigger']
          .map((slot) => `[data-slot="${slot}"]${resting}`)
          .join(', '),
      )) {
        const cs = getComputedStyle(el)
        out.add(`${cs.borderTopWidth} ${cs.borderTopColor}`)
      }
      return [...out]
    })
    expect(lines.length, 'no bordered primitive found').toBeGreaterThan(0)
    for (const line of lines) {
      expect(line, 'a line that is not 1px or 2px of ink').toMatch(/^(1px|2px) rgb\(1, 25, 54\)$/)
    }
    const invalid = await page.evaluate(() => {
      const el = document.querySelector('[data-slot="input"][aria-invalid]')
      return el ? getComputedStyle(el).borderTopColor : null
    })
    expect(invalid, 'an invalid field should draw the destructive line').toBe('rgb(229, 19, 62)')
  })

  test('ignores an operating system in dark mode, which is ORB-138', async ({ page }) => {
    await page.goto('/')
    const light = {
      body: await computedOf(page, 'body', 'background-color'),
      card: await computedOf(page, '[data-slot="card"]', 'background-color'),
      input: await computedOf(page, '[data-slot="input"]', 'background-color'),
      text: await computedOf(page, 'body', 'color'),
    }
    await page.emulateMedia({ colorScheme: 'dark' })
    const dark = {
      body: await computedOf(page, 'body', 'background-color'),
      card: await computedOf(page, '[data-slot="card"]', 'background-color'),
      input: await computedOf(page, '[data-slot="input"]', 'background-color'),
      text: await computedOf(page, 'body', 'color'),
    }
    expect(dark, 'the OS preference reached a utility').toEqual(light)
    expect(light.body).toBe('rgb(241, 242, 243)')
  })

  test('sits on the 16px grid, and paints it behind the page rather than on a wrapper', async ({ page }) => {
    await page.goto('/')
    const body = await page.evaluate(() => {
      const cs = getComputedStyle(document.body)
      return { size: cs.backgroundSize, image: cs.backgroundImage }
    })
    // Two layers, the vertical rules and the horizontal ones, so the browser reports
    // the size once per layer: every one of them is the 16px cell.
    expect(body.size.split(', ')).toEqual(['16px 16px', '16px 16px'])
    expect(body.image.match(/linear-gradient/g) ?? []).toHaveLength(2)
  })
})
