import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { technology } from '@/lib/tech-usage-test-kit'
import { USAGE_LEGEND } from '@/lib/techUsage'
import { UsageBadge, UsageBadges } from './TechUsage'

const THREE = [
  technology({ name: 'Playwright (E2E)', band: 'occasional', months: 2, first_month: '2026-04', last_month: '2026-05' }),
  technology({ name: 'Python' }),
  technology({ name: 'Zod', band: 'recurring', months: 4, first_month: '2026-01', last_month: '2026-05' }),
]

describe('a technology as a badge that fills from the left (REB-1130)', () => {
  it.each([
    ['occasional', 'third', 'occasionale'],
    ['recurring', 'two-thirds', 'ricorrente'],
    ['continuous', 'full', 'continua'],
  ] as const)('fills a %s badge %s of the way and says «%s» in its accessible name', (band, fill, word) => {
    render(
      <ul>
        <UsageBadge technology={technology({ band })} />
      </ul>,
    )

    const badge = screen.getByText('Python')
    expect(badge).toHaveAttribute('data-slot', 'badge')
    expect(badge).toHaveAttribute('data-variant', 'meter')
    expect(badge).toHaveAttribute('data-fill', fill)
    expect(badge).toHaveAttribute('data-band', band)
    // The text of the badge is the name; the rest is read out after it.
    expect(badge.firstChild?.textContent).toBe('Python')
    expect(badge).toHaveTextContent(
      `Python, presenza ${word}: 12 sessioni in 3 prodotti, 6 mesi, da gennaio 2026 a giugno 2026`,
    )
  })

  it('says the band and the counts on hover and on keyboard focus, from the same words', async () => {
    render(
      <ul>
        <UsageBadge technology={technology({ band: 'recurring' })} />
      </ul>,
    )
    const badge = screen.getByText('Python')
    // Focusable, so the tooltip is not for a mouse alone.
    expect(badge).toHaveAttribute('tabindex', '0')
    const tooltip = badge.parentElement!.querySelector('[aria-hidden="true"]')!
    expect(tooltip).toHaveTextContent('Presenza ricorrente')
    expect(tooltip).toHaveTextContent('12 sessioni in 3 prodotti, 6 mesi, da gennaio 2026 a giugno 2026')
    // Hidden until the pair hovers or focuses; the class is the contract, jsdom loads no CSS.
    expect(tooltip.className).toContain('hidden')
    expect(tooltip.className).toContain('group-hover/usage:block')
    expect(tooltip.className).toContain('group-focus-within/usage:block')

    await userEvent.tab()
    expect(badge).toHaveFocus()
  })

  it('without a tooltip says the same thing as a title, and is not a tab stop', () => {
    render(
      <ul>
        <UsageBadge technology={technology()} tooltip={false} />
      </ul>,
    )
    const badge = screen.getByText('Python')
    expect(badge).not.toHaveAttribute('tabindex')
    expect(badge).toHaveAttribute(
      'title',
      'Presenza continua: 12 sessioni in 3 prodotti, 6 mesi, da gennaio 2026 a giugno 2026',
    )
  })

  it('invents no level between the bands: the same counts at one band are the same badge', () => {
    // Two continuous technologies with very different counts fill alike: the counts are
    // in the words, never in the fill.
    render(
      <ul>
        <UsageBadge technology={technology({ name: 'A', months: 6, sessions: 7, products: 1 })} />
        <UsageBadge technology={technology({ name: 'B', months: 24, sessions: 900, products: 11 })} />
      </ul>,
    )
    expect(screen.getByText('A')).toHaveAttribute('data-fill', 'full')
    expect(screen.getByText('B')).toHaveAttribute('data-fill', 'full')
    expect(screen.getByText('A').className).toBe(screen.getByText('B').className)
  })
})

describe('a group of technologies', () => {
  it('lists them strongest band first with the one-line legend that describes the list', () => {
    render(<UsageBadges technologies={THREE} />)

    const list = screen.getByRole('list', { name: 'Tecnologie' })
    expect(within(list).getAllByRole('listitem').map((item) => item.querySelector('[data-slot="badge"]')?.firstChild?.textContent)).toEqual([
      'Python',
      'Zod',
      'Playwright (E2E)',
    ])
    const legend = screen.getByText(USAGE_LEGEND)
    expect(list).toHaveAttribute('aria-describedby', legend.id)
  })

  it('shows the first few and a «+N» in its compact form, with no legend and no tooltip', () => {
    render(<UsageBadges technologies={THREE} limit={2} />)

    expect(screen.queryByText(USAGE_LEGEND)).toBeNull()
    expect(screen.getByText('Python')).toHaveAttribute('title')
    expect(screen.queryByText('Playwright (E2E)')).toBeNull()
    expect(screen.getByText('+1')).toBeInTheDocument()
    expect(screen.getByText('e altre 1')).toHaveClass('sr-only')
  })

  it('shows no «+N» when everything fits', () => {
    render(<UsageBadges technologies={THREE} limit={3} />)
    expect(screen.queryByText(/^\+/)).toBeNull()
  })

  it('renders nothing for no technologies, so a caller needs no empty state', () => {
    const { container } = render(<UsageBadges technologies={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
