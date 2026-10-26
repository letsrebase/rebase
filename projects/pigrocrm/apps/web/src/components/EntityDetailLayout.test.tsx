import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Building2 } from 'lucide-react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EntityDetailLayout } from './EntityDetailLayout'

// `Timeline` makes a real network call through `useQuery` (see Timeline.test.tsx
// for its own coverage) -- irrelevant to what this file is about, which is the
// tab shell around it, so it is stubbed out the same way the task brief itself
// specifies. The stub also records the props it was called with, so the "wiring"
// tests below can confirm the layout actually forwards `entityType`/`entityId`
// rather than only rendering *some* Timeline.
//
// Radix's `TabsContent` does not mount an inactive tab's children at all (not
// merely hide them), so the stub only actually renders -- and `timelineSpy` only
// actually records a call -- once the Timeline tab has been activated at least
// once. The wiring tests below click it first for exactly that reason; every
// other test here never touches the Timeline tab and so never triggers it.
const timelineSpy = vi.fn()
vi.mock('./Timeline', () => ({
  Timeline: (props: unknown) => {
    timelineSpy(props)
    return <div>timeline-stub</div>
  },
}))

beforeEach(() => {
  timelineSpy.mockClear()
})

function renderLayout(overrides: Partial<Parameters<typeof EntityDetailLayout>[0]> = {}) {
  return render(
    <EntityDetailLayout
      icon={Building2}
      title="ACME Srl"
      subtitle="Cliente"
      entityType="customer"
      entityId="abc"
      overview={<p>panoramica</p>}
      links={<p>collegamenti</p>}
      {...overrides}
    />,
  )
}

describe('EntityDetailLayout', () => {
  it('shows the title and subtitle', () => {
    renderLayout()
    expect(screen.getByText('ACME Srl')).toBeInTheDocument()
    expect(screen.getByText('Cliente')).toBeInTheDocument()
  })

  it('draws the title through PageHeader, as the page\u2019s one h1', () => {
    // Every detail page opens with the same intestazione every list page does (design
    // spec \u00a74), which is what makes the product predictable to learn -- and the
    // record's name is the only `<h1>` on the screen, since the shell has no top bar.
    renderLayout()
    expect(screen.getByRole('heading', { level: 1, name: 'ACME Srl' })).toBeInTheDocument()
  })

  it('exposes the same three tabs for every entity', () => {
    renderLayout()
    for (const tab of ['Panoramica', 'Timeline', 'Collegamenti']) {
      expect(screen.getByRole('tab', { name: tab })).toBeInTheDocument()
    }
  })

  it('opens on Panoramica', () => {
    renderLayout()
    expect(screen.getByText('panoramica')).toBeInTheDocument()
  })

  it('switches to Collegamenti when asked', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('tab', { name: 'Collegamenti' }))
    expect(screen.getByText('collegamenti')).toBeInTheDocument()
  })

  it('switches to Timeline when asked', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    expect(screen.getByText('timeline-stub')).toBeInTheDocument()
  })

  it('renders the actions passed in, next to the title', () => {
    renderLayout({ actions: <button type="button">Modifica</button> })
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeInTheDocument()
  })

  it('falls back to an honest "no links" message when links are not given', async () => {
    renderLayout({ links: undefined })
    await userEvent.click(screen.getByRole('tab', { name: 'Collegamenti' }))
    expect(screen.getByText('Nessun collegamento.')).toBeInTheDocument()
  })

  it('forwards entityType, entityId and the optional timelineLimit through to Timeline unchanged', async () => {
    renderLayout({ entityType: 'deal', entityId: 'deal-42', timelineLimit: 100 })
    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    expect(timelineSpy).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ entityType: 'deal', entityId: 'deal-42', limit: 100 }),
    )
  })

  it('does not require timelineLimit -- Timeline gets its own default', async () => {
    renderLayout()
    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    expect(timelineSpy).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ limit: undefined }))
  })

  it('has no Documenti tab when documents is not given -- Person has no documents', () => {
    renderLayout()
    expect(screen.queryByRole('tab', { name: 'Documenti' })).not.toBeInTheDocument()
  })

  it('shows a Documenti tab with its own contents when documents is given', async () => {
    renderLayout({ documents: <p>lista documenti</p> })
    const tab = screen.getByRole('tab', { name: 'Documenti' })
    expect(tab).toBeInTheDocument()
    await userEvent.click(tab)
    expect(screen.getByText('lista documenti')).toBeInTheDocument()
  })

  it('has no Ore tab when hours is not given -- only a Deal has hours', () => {
    renderLayout()
    expect(screen.queryByRole('tab', { name: 'Ore' })).not.toBeInTheDocument()
  })

  it('shows an Ore tab with its own contents when hours is given', async () => {
    renderLayout({ hours: <p>voci di ore</p> })
    const tab = screen.getByRole('tab', { name: 'Ore' })
    expect(tab).toBeInTheDocument()
    await userEvent.click(tab)
    expect(screen.getByText('voci di ore')).toBeInTheDocument()
  })

  // Nothing fills `economics` in slice 4A. The tab must therefore be absent, not
  // present and empty: a user reads "not yet" from a missing tab, and cannot tell a
  // real zero from a missing feature inside one that opens onto nothing.
  it('has no Economia tab when economics is not given', () => {
    renderLayout()
    expect(screen.queryByRole('tab', { name: 'Economia' })).not.toBeInTheDocument()
  })

  it('shows an Economia tab with its own contents when economics is given', async () => {
    renderLayout({ economics: <p>conto economico</p> })
    const tab = screen.getByRole('tab', { name: 'Economia' })
    expect(tab).toBeInTheDocument()
    await userEvent.click(tab)
    expect(screen.getByText('conto economico')).toBeInTheDocument()
  })

  // An installation with no Gmail connected passes nothing here, and a tab that opens
  // onto "nothing yet" for a feature that was never switched on is worse than no tab:
  // it invites the user to look for something that does not exist for them.
  it('has no Email tab when emails is not given', () => {
    renderLayout()
    expect(screen.queryByRole('tab', { name: 'Email' })).not.toBeInTheDocument()
  })

  it('shows an Email tab with its own contents when emails is given', async () => {
    renderLayout({ emails: <p>conversazioni</p> })
    const tab = screen.getByRole('tab', { name: 'Email' })
    expect(tab).toBeInTheDocument()
    await userEvent.click(tab)
    expect(screen.getByText('conversazioni')).toBeInTheDocument()
  })

  /**
   * Every optional slot at once, which is what a Deal actually passes. Guards the two
   * ways this component has been changed before: a new tab added to `TabsList` but not
   * to `TabsContent` (a tab that opens onto nothing), and an existing entry dropped
   * while adding the new one.
   */
  it('keeps every other tab when the Email tab is added', () => {
    renderLayout({
      documents: <p>lista documenti</p>,
      invoices: <p>fatture</p>,
      hours: <p>ore</p>,
      economics: <p>conto economico</p>,
      attivita: <p>cose da fare</p>,
      emails: <p>conversazioni</p>,
    })
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Panoramica',
      'Documenti',
      'Fatture',
      'Ore',
      'Economia',
      'Attività',
      'Email',
      'Timeline',
      'Collegamenti',
    ])
  })

  it('shows an Attività tab, apart from the Timeline, when attivita is given', async () => {
    renderLayout({ attivita: <p>cose da fare</p> })
    await userEvent.click(screen.getByRole('tab', { name: 'Attività' }))
    expect(screen.getByText('cose da fare')).toBeInTheDocument()
  })
})
