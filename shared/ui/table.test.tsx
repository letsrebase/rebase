import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ScrollRegion, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './table'

/**
 * The container of a table is a keyboard-reachable scroll region while the table
 * overflows it, and only then (REB-775). jsdom lays nothing out, so the two widths the
 * component reads and the observer that tells it they moved are stood in for.
 */
let widths = { scroll: 0, client: 0 }
let notify: () => void = () => {}

const observe = vi.fn()
const disconnect = vi.fn()

class FakeResizeObserver {
  constructor(callback: () => void) {
    notify = callback
  }
  observe = observe
  disconnect = disconnect
}

beforeEach(() => {
  widths = { scroll: 400, client: 200 }
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(() => widths.scroll)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => widths.client)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  observe.mockClear()
  disconnect.mockClear()
})

function renderTable(props: React.ComponentProps<typeof Table> = {}) {
  const view = render(
    <Table {...props}>
      <TableHeader>
        <TableRow>
          <TableHead>Cliente</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell>Acme</TableCell>
        </TableRow>
      </TableBody>
    </Table>,
  )
  return view.container.querySelector<HTMLElement>('[data-slot="table-container"]')!
}

describe('Table container', () => {
  it('takes a tab stop and a region name while the table overflows it', () => {
    const container = renderTable({ scrollLabel: 'Trattamento fiscale' })

    act(() => notify())

    expect(container).toHaveAttribute('tabindex', '0')
    expect(container).toHaveAttribute('role', 'region')
    expect(screen.getByRole('region', { name: 'Trattamento fiscale' })).toBe(container)
  })

  it('stays out of the tab order, and out of the landmarks, when the table fits', () => {
    widths = { scroll: 200, client: 200 }
    const container = renderTable({ scrollLabel: 'Trattamento fiscale' })

    act(() => notify())

    expect(container).not.toHaveAttribute('tabindex')
    expect(container).not.toHaveAttribute('role')
    expect(container).not.toHaveAttribute('aria-label')
  })

  it('follows the width: it gives the tab stop back when the card grows', () => {
    const container = renderTable({ scrollLabel: 'Trattamento fiscale' })
    act(() => notify())
    expect(container).toHaveAttribute('tabindex', '0')

    widths = { scroll: 200, client: 200 }
    act(() => notify())

    expect(container).not.toHaveAttribute('tabindex')
  })

  it('lends the table’s own aria-label when the caller sets no scroll label', () => {
    const container = renderTable({ 'aria-label': 'Fatture' })

    act(() => notify())

    expect(container).toHaveAttribute('aria-label', 'Fatture')
  })

  it('is focusable but not a nameless region when nobody names it', () => {
    const container = renderTable()

    act(() => notify())

    expect(container).toHaveAttribute('tabindex', '0')
    expect(container).not.toHaveAttribute('role')
  })

  it('watches the container and the table inside it, and stops when it unmounts', () => {
    const { unmount } = render(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell>Acme</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )
    const container = document.querySelector('[data-slot="table-container"]')!
    const table = container.querySelector('table')!

    expect(observe.mock.calls.map(([node]) => node)).toEqual([container, table])

    unmount()

    expect(disconnect).toHaveBeenCalledTimes(1)
  })

  it('does not hand the scroll label to the table element', () => {
    const container = renderTable({ scrollLabel: 'Trattamento fiscale' })

    expect(container.querySelector('table')).not.toHaveAttribute('scrolllabel')
  })

  it('renders without a ResizeObserver, as a plain container', () => {
    vi.unstubAllGlobals()
    vi.stubGlobal('ResizeObserver', undefined)

    const container = renderTable({ scrollLabel: 'Trattamento fiscale' })

    expect(container).not.toHaveAttribute('tabindex')
  })
})

describe('ScrollRegion', () => {
  it('names a hand-written table while it overflows, for the tables that cannot be `Table`', () => {
    render(
      <ScrollRegion label="Ore della settimana per deal">
        <table>
          <tbody>
            <tr>
              <td>Acme</td>
            </tr>
          </tbody>
        </table>
      </ScrollRegion>,
    )

    act(() => notify())

    const region = screen.getByRole("region", { name: "Ore della settimana per deal" })
    expect(region).toHaveAttribute("tabindex", "0")
    expect(region).toHaveAttribute("data-slot", "scroll-region")
  })

  it('stays out of the tab order when its content fits, and keeps the classes it is given', () => {
    widths = { scroll: 200, client: 200 }
    const { container } = render(
      <ScrollRegion label="Valori" className="border">
        <table />
      </ScrollRegion>,
    )

    act(() => notify())

    const region = container.firstElementChild!
    expect(region).not.toHaveAttribute("tabindex")
    expect(region).not.toHaveAttribute("role")
    expect(region.className).toContain("border")
    expect(region.className).toContain("overflow-x-auto")
  })

  it('watches every child of the region, so a table beside a chart is measured too', () => {
    const { container } = render(
      <ScrollRegion label="Andamento">
        <svg />
        <table />
      </ScrollRegion>,
    )
    const region = container.firstElementChild!

    expect(observe.mock.calls.map(([node]) => node)).toEqual([
      region,
      region.children[0],
      region.children[1],
    ])
  })
})
