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
    expect(badge.className).toContain('bg-[var(--color-paper)]')
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
    ['danger', 'bg-[var(--color-watermelon)]'],
    ['gold', 'bg-[var(--color-royal-gold)]'],
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
})
