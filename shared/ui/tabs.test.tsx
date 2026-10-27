import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs'

function renderTabs() {
  return render(
    <Tabs defaultValue="fatture">
      <TabsList>
        <TabsTrigger value="fatture">Fatture</TabsTrigger>
        <TabsTrigger value="solleciti">Solleciti</TabsTrigger>
      </TabsList>
      <TabsContent value="fatture">Elenco</TabsContent>
    </Tabs>,
  )
}

/**
 * Tabs are a thin underline under the active label, not a boxed list of pills
 * (design spec §4). Radix renders `data-state="active"` on the trigger, so the
 * active rule is written against that attribute.
 */
describe('Tabs', () => {
  it('draws the list as a rule under the row, with no background pill', () => {
    const { container } = renderTabs()
    const list = container.querySelector('[data-slot="tabs-list"]')!
    expect(list.className).toContain('border-b')
    expect(list.className).toContain('border-border')
    expect(list.className).not.toContain('bg-muted')
    expect(list.className).not.toContain('rounded-lg')
  })

  it('underlines the active trigger and leaves the others quiet', () => {
    renderTabs()
    const active = screen.getByRole('tab', { name: 'Fatture' })
    const inactive = screen.getByRole('tab', { name: 'Solleciti' })
    expect(active).toHaveAttribute('data-state', 'active')
    expect(inactive).toHaveAttribute('data-state', 'inactive')
    for (const trigger of [active, inactive]) {
      expect(trigger.className).toContain('data-[state=active]:border-b-2')
      expect(trigger.className).toContain('data-[state=active]:border-foreground')
      expect(trigger.className).toContain('data-[state=active]:font-medium')
      // The 2px rule sits *on* the list's 1px line, not below it.
      expect(trigger.className).toContain('-mb-px')
      // 15px, per the reference.
      expect(trigger.className).toContain('text-[15px]')
      // No box: neither a filled active state nor a card shadow.
      expect(trigger.className).not.toContain('data-active:bg-background')
      expect(trigger.className).not.toContain('shadow-sm')
    }
  })

  it('keeps the API: switching value swaps the panel', async () => {
    const { rerender } = render(
      <Tabs value="fatture" onValueChange={() => {}}>
        <TabsList>
          <TabsTrigger value="fatture">Fatture</TabsTrigger>
          <TabsTrigger value="solleciti">Solleciti</TabsTrigger>
        </TabsList>
        <TabsContent value="fatture">Elenco</TabsContent>
        <TabsContent value="solleciti">Promemoria</TabsContent>
      </Tabs>,
    )
    expect(screen.getByText('Elenco')).toBeInTheDocument()
    rerender(
      <Tabs value="solleciti" onValueChange={() => {}}>
        <TabsList>
          <TabsTrigger value="fatture">Fatture</TabsTrigger>
          <TabsTrigger value="solleciti">Solleciti</TabsTrigger>
        </TabsList>
        <TabsContent value="fatture">Elenco</TabsContent>
        <TabsContent value="solleciti">Promemoria</TabsContent>
      </Tabs>,
    )
    expect(screen.getByText('Promemoria')).toBeInTheDocument()
  })

  /** REB-789: Radix names the panel on every trigger and mounts only the active one,
   *  so the inactive trigger (and every trigger of a strip with no panels) pointed at
   *  an element that is not in the page. */
  it('points aria-controls only at panels that are in the page', () => {
    renderTabs()
    const active = screen.getByRole('tab', { name: 'Fatture' })
    const withoutPanel = screen.getByRole('tab', { name: 'Solleciti' })
    const id = active.getAttribute('aria-controls')!
    expect(document.getElementById(id)).toBe(screen.getByText('Elenco'))
    expect(withoutPanel).not.toHaveAttribute('aria-controls')
  })

  it('keeps aria-controls on a trigger whose panel is mounted hidden', () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Pannello A</TabsContent>
        <TabsContent value="b">Pannello B</TabsContent>
      </Tabs>,
    )
    const b = screen.getByRole('tab', { name: 'B' })
    expect(document.getElementById(b.getAttribute('aria-controls')!)).not.toBeNull()
  })

  it('drops aria-controls when a panel goes away and restores it when it returns', () => {
    const strip = (withPanel: boolean) => (
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        {withPanel && <TabsContent value="a">Pannello A</TabsContent>}
      </Tabs>
    )
    const { rerender } = render(strip(true))
    const a = screen.getByRole('tab', { name: 'A' })
    expect(a).toHaveAttribute('aria-controls')
    rerender(strip(false))
    expect(a).not.toHaveAttribute('aria-controls')
    rerender(strip(true))
    expect(document.getElementById(a.getAttribute('aria-controls')!)).not.toBeNull()
  })

  it('drops aria-controls on a link trigger (asChild) with no panel', () => {
    render(
      <Tabs value="a">
        <TabsList>
          <TabsTrigger value="a" asChild>
            <a href="/a">A</a>
          </TabsTrigger>
        </TabsList>
      </Tabs>,
    )
    expect(screen.getByRole('tab', { name: 'A' })).not.toHaveAttribute('aria-controls')
  })
})
