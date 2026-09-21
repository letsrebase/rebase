/**
 * The page header every screen under the shell renders, now that the shell itself has no
 * top bar (spec §4: the search moved into the sidebar, the title into the white panel).
 *
 * Asserted through roles and text rather than through classes: what matters is that a
 * page has exactly one <h1>, that the primary action sits in the header rather than
 * somewhere in the page body, and that the optional slots are really optional.
 */
import { render, screen } from '@testing-library/react'
import { LayoutDashboard } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import { PageHeader } from './PageHeader'

describe('PageHeader', () => {
  it('renders the title as the page heading', () => {
    render(<PageHeader icon={LayoutDashboard} title="Fatture" />)
    expect(screen.getByRole('heading', { level: 1, name: 'Fatture' })).toBeInTheDocument()
  })

  it('renders the description when there is one, and nothing when there is not', () => {
    const { unmount } = render(
      <PageHeader icon={LayoutDashboard} title="Fatture" description="Il registro del mese." />,
    )
    expect(screen.getByText('Il registro del mese.')).toBeInTheDocument()
    unmount()

    render(<PageHeader icon={LayoutDashboard} title="Fatture" />)
    expect(screen.queryByText('Il registro del mese.')).not.toBeInTheDocument()
  })

  it('renders the actions, which is where a page puts its primary button', () => {
    render(
      <PageHeader
        icon={LayoutDashboard}
        title="Fatture"
        actions={<button type="button">Crea fattura</button>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Crea fattura' })).toBeInTheDocument()
  })

  it('renders the tabs row and the children slot', () => {
    render(
      <PageHeader
        icon={LayoutDashboard}
        title="Fatture"
        tabs={<a href="/app/invoices">Tutte</a>}
      >
        <p>filtri</p>
      </PageHeader>,
    )
    expect(screen.getByRole('link', { name: 'Tutte' })).toBeInTheDocument()
    expect(screen.getByText('filtri')).toBeInTheDocument()
  })

  /* --- How it holds up narrow (visual pass of 2026-09-08, screenshots at 390) ---
     On a phone the icon, a 24px title and a primary button cannot share a row: the title
     came out as «F…» and «D…» and the description as one word per line, because the
     actions kept their width and the text block gave up all of its own. Below `md` the
     three stack. Asserted through classes, unlike everything above: jsdom loads no
     stylesheet and lays nothing out, and these classes are exactly what the Playwright
     check reads back. */
  it('stacks the title block and the actions below md, and keeps them on one row above it', () => {
    const { container } = render(
      <PageHeader
        icon={LayoutDashboard}
        title="Fatture"
        description="Una fattura si emette da un deal, da un cliente o da qui."
        actions={<button type="button">Nuova fattura</button>}
      />,
    )
    const row = container.querySelector('header > div')!
    expect(row.className).toContain('flex-col')
    expect(row.className).toContain('md:flex-row')

    const actions = screen.getByRole('button', { name: 'Nuova fattura' }).parentElement!
    // Wrapping, and only pinned against shrinking once there is a row to shrink in.
    expect(actions.className).toContain('flex-wrap')
    expect(actions.className.split(/\s+/)).not.toContain('shrink-0')
    expect(actions.className).toContain('md:shrink-0')
  })

  it('never truncates the title below md, where it has a line to itself', () => {
    render(<PageHeader icon={LayoutDashboard} title="Impostazioni" />)
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading.className).toContain('md:truncate')
    expect(heading.className.split(/\s+/)).not.toContain('truncate')
  })

  it('lets a long row of tabs scroll inside the panel instead of widening the page', () => {
    // Thirteen settings tabs are far wider than a phone: «Template» was cut off mid-word
    // and the page scrolled sideways to reach the rest (`impostazioni-390.png`).
    render(
      <PageHeader icon={LayoutDashboard} title="Impostazioni" tabs={<a href="/app">Campi</a>} />,
    )
    expect(screen.getByRole('link', { name: 'Campi' }).parentElement!.className).toContain(
      'overflow-x-auto',
    )
  })

  it('hides the decorative icon from a screen reader, so the heading is read once', () => {
    const { container } = render(<PageHeader icon={LayoutDashboard} title="Fatture" />)
    // The icon repeats what the title already says; announcing it adds nothing.
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})
