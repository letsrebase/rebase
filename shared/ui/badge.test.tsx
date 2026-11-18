import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Badge } from './badge'

/**
 * The status pill of the reference screenshots: a rounded-full Paper chip with a
 * coloured dot before the label. Asserted on the class list rather than on computed
 * styles because jsdom loads no Tailwind stylesheet — the classes *are* the contract
 * the rest of the app (StatusPill, the table's status column) builds on.
 */
describe('Badge', () => {
  it('keeps the variants that were already in use', () => {
    render(<Badge>Bozza</Badge>)
    const badge = screen.getByText('Bozza')
    expect(badge).toHaveAttribute('data-variant', 'default')
    expect(badge.className).toContain('bg-primary')
  })

  it('renders the pill variant fully round, on Paper, in the ink', () => {
    render(<Badge variant="pill">Emessa</Badge>)
    const badge = screen.getByText('Emessa')
    expect(badge).toHaveAttribute('data-variant', 'pill')
    expect(badge.className).toContain('rounded-full')
    // 12px / 500, padding 2px 10px.
    expect(badge.className).toContain('text-xs')
    expect(badge.className).toContain('font-medium')
    expect(badge.className).toContain('px-2.5')
    expect(badge.className).toContain('py-0.5')
    expect(badge.className).toContain('bg-paper')
    expect(badge.className).toContain('text-foreground')
  })

  it('draws no dot unless one is asked for', () => {
    const { container } = render(<Badge variant="pill">Emessa</Badge>)
    expect(container.querySelector('[data-slot="badge-dot"]')).toBeNull()
  })

  it.each([
    ['ink', 'bg-foreground'],
    ['muted', 'bg-muted-foreground'],
    ['accent', 'bg-accent'],
    ['danger', 'bg-watermelon'],
    ['gold', 'bg-royal-gold'],
  ] as const)('draws a 6px %s dot before the label', (dot, expected) => {
    const { container } = render(
      <Badge variant="pill" dot={dot}>
        Emessa
      </Badge>,
    )
    const mark = container.querySelector('[data-slot="badge-dot"]')
    expect(mark).not.toBeNull()
    // Decoration: the label beside it carries the meaning, so it is hidden from AT.
    expect(mark).toHaveAttribute('aria-hidden', 'true')
    expect(mark!.className).toContain('size-1.5')
    expect(mark!.className).toContain('rounded-full')
    expect(mark!.className).toContain(expected)
    // Before the text, not after.
    expect(container.querySelector('[data-slot="badge"]')!.firstElementChild).toBe(mark)
  })

  it('takes a dot on any variant, not only on the pill', () => {
    const { container } = render(<Badge variant="outline" dot="danger">In ritardo</Badge>)
    expect(container.querySelector('[data-slot="badge-dot"]')).not.toBeNull()
  })

  it('keeps the destructive chip on the 10% ceiling and marks its link hover with an underline', () => {
    // REB-309: the hover used to deepen the fill to 20%, which put the chip's own
    // text below AA over Paper. The class list is the contract (jsdom loads no
    // Tailwind), so the ceiling and the hover treatment are both asserted here;
    // `[a]:hover:` is what the variant applies when the badge is an anchor. Any
    // hover fill is rejected, not just the deep ones: the decision was that the
    // hover stops touching the fill at all.
    render(<Badge variant="destructive">Elimina</Badge>)
    const badge = screen.getByText('Elimina')
    expect(badge.className).toContain('bg-destructive/10')
    expect(badge.className).toContain('[a]:hover:underline')
    expect(badge.className).not.toMatch(/hover:bg-destructive/)
  })

  describe('the meter variant', () => {
    it('is a bordered ink-on-white badge whose gradients read the meter tokens and the strong line', () => {
      render(<Badge variant="meter">Python</Badge>)
      const badge = screen.getByText('Python')
      expect(badge).toHaveAttribute('data-variant', 'meter')
      expect(badge).not.toHaveAttribute('data-fill')
      expect(badge.className).toContain('border-border')
      expect(badge.className).toContain('text-foreground')
      expect(badge.className).toContain('bg-(--meter-ground)')
      // The ink rail along the bottom and the fill behind the label, both through tokens: no
      // hex and no px.
      expect(badge.className).toContain('var(--meter-fill)')
      expect(badge.className).toContain('var(--foreground)')
      expect(badge.className).toContain('var(--meter-ground)')
      expect(badge.className).toContain('bg-size-[100%_var(--line-strong),100%_100%]')
      expect(badge.className).not.toMatch(/#[0-9a-f]{3,8}\b/i)
      const paint = badge.className.split(' ').filter((name) => name.startsWith('bg-'))
      expect(paint.join(' ')).not.toMatch(/\d(px|rem)/)
    })

    it.each([
      ['third', '--meter-third'],
      ['two-thirds', '--meter-two-thirds'],
      ['full', '--meter-full'],
    ] as const)('fills %s of the way from the left through %s', (fill, token) => {
      render(
        <Badge variant="meter" fill={fill}>
          Python
        </Badge>,
      )
      const badge = screen.getByText('Python')
      expect(badge).toHaveAttribute('data-fill', fill)
      expect(badge.className).toContain(`[--meter-level:var(${token})]`)
    })

    it('has exactly three fills, so no finer level can be asked for', () => {
      const fills = ['third', 'two-thirds', 'full'] as const
      for (const fill of fills) {
        const { container, unmount } = render(<Badge variant="meter" fill={fill} />)
        const levels = container.querySelector('[data-slot="badge"]')!.className.match(/\[--meter-level:/g)
        expect(levels).toHaveLength(1)
        unmount()
      }
    })

    it('puts no fill on a badge that does not ask for one', () => {
      render(<Badge variant="outline">Bozza</Badge>)
      expect(screen.getByText('Bozza').className).not.toContain('--meter-level')
    })
  })
})
