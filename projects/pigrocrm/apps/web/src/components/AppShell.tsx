import { useRouterState } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { CommandPalette } from '@/features/search/CommandPalette'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { Sidebar } from './Sidebar'

/** Below this the sidebar is a rail whose expanded form is an overlay, not a column. */
const DESKTOP = '(min-width: 1024px)'

/**
 * The shell of the app: a dark sidebar on the left, everything else the page, full
 * width from the sidebar to the edge of the window (REB-328).
 *
 * The global search is a field at the top of the sidebar rather than a control in a top
 * bar -- there is no top bar -- so a page's own `PageHeader` is the first thing on the page
 * and owns the title and the primary action. The page scrolls on its own, so the sidebar
 * never scrolls away with it.
 *
 * The navigation reads in the order of the brief Lorenzo approved on 2026-11-02 (sidebar A,
 * REB-873): Home; then three sections named by what they are for, drawn as captions that
 * are always open and are never controls, «Da fare» (Attività, Calendario, Avvisi,
 * Proposte), «Vendite» and «Amministrazione»; then, at the foot, «Agenti» and
 * «Impostazioni», the only entry that still opens onto sub-items. «Oggi» is no entry of
 * its own: it is the first view of the Attività page, whose overdue count it carries.
 * «Primi passi» is in the profile menu once the space has data.
 *
 * Home is the dashboard, `/app`, and it is where the economic reading of the books lives
 * (the «Analisi» section left on 2026-09-09; `GET /api/analytics/*` and the MCP tools are
 * untouched).
 *
 * Below `lg` the sidebar is the icon rail by default and its expanded form is an overlay
 * over the page, dismissed by a backdrop or by navigating: 272px of the 390px a phone has
 * would leave the content about 118px, which is not a layout. Above `lg` it is the column
 * beside the page, collapsible to the same rail as before; there the captions become thin
 * separators and every entry an icon.
 */
export function AppShell({
  children,
  go = (url) => window.location.assign(url),
}: {
  children: ReactNode
  go?: (url: string) => void
}) {
  const { location } = useRouterState()
  // Below `lg` a 272px sidebar leaves ~118px of page on a 390px phone, so there the
  // sidebar is the rail by default and its expanded form is an overlay over the content.
  // The breakpoint has to be read in JS and not only in CSS because the two are different
  // *structures*, not two widths of one.
  const isDesktop = useMediaQuery(DESKTOP)
  // Local, unpersisted UI state -- collapsing to an icon rail is a per-visit
  // convenience, not a setting worth a round trip or a storage key. Nothing about the
  // shape of the navigation is stored any more: the sections are always open
  // (REB-873), and «Impostazioni» follows the route until it is toggled.
  const [collapsed, setCollapsed] = useState(false)
  // The overlay, stored as *where* it was opened rather than as a boolean: navigating
  // closes it, with no effect to synchronise, because the pathname it was opened at is no
  // longer the current one. A menu that stays open over the page you just asked for is
  // the classic mobile-drawer bug.
  const [openedAt, setOpenedAt] = useState<string | null>(null)
  // The palette is mounted here, once, rather than inside the search field: it owns the
  // Cmd/Ctrl+K listener, so it has to be alive even while the field has never been
  // clicked.
  const [searchOpen, setSearchOpen] = useState(false)

  // What the sidebar is showing right now, and what the one toggle does to it.
  const overlay = !isDesktop && openedAt === location.pathname
  const expanded = isDesktop ? !collapsed : overlay
  const rail = !expanded
  const toggleSidebar = () =>
    isDesktop
      ? setCollapsed((value) => !value)
      : setOpenedAt(overlay ? null : location.pathname)

  return (
    <div className="flex h-full overflow-hidden">
      {overlay && (
        <>
          {/* A real button, not an `aria-hidden` div: dismissing an overlay is something a
              keyboard has to be able to do, and this is the control that does it. */}
          <button
            type="button"
            aria-label="Chiudi il menu"
            onClick={() => setOpenedAt(null)}
            className="fixed inset-0 z-30 bg-[var(--color-prussian-blue)]/50"
          />
          {/* The rail's own width, kept in the flow while the sidebar itself is out of it,
              so the page underneath does not shift as the overlay opens and closes. */}
          <div aria-hidden="true" className="w-[4.5rem] shrink-0" />
        </>
      )}
      <Sidebar
        rail={rail}
        overlay={overlay}
        onToggle={toggleSidebar}
        onSearch={() => setSearchOpen(true)}
        go={go}
      />

      {/* The page: white from the sidebar's edge to the window's, no inset frame and no
          border of its own. The 12px grid-ground margin and the `lg:border` were copied
          from the hub's panel and read as a card scrolling on its own inside the page
          (REB-328, Lorenzo 2026-09-22: «full width dalla sidebar fino alla fine della
          pagina e senza margini»). `<main>` is the one scroller of the application: the
          root's `overflow-hidden` clips anything in normal flow to it, and the pages
          carry their own padding (`PageHeader`'s `px-8`). Below `lg` nothing changes:
          the inset was already zero there.

          `relative` gives `<main>` its own containing block (REB-418): without one, a
          plain `position: absolute` descendant with no explicit `top` -- the `sr-only`
          "Fatto: "/"Da fare: " prefix `GetStartedPage` puts on every step of «Primi
          passi», and the next one nobody notices until a page is long enough to expose
          it -- is *not* in that normal flow, so the root's `overflow-hidden` does not
          reach it: it falls back to its static position against the *initial*
          containing block instead, escaping this `overflow-y-auto` box entirely and
          inflating `<html>`'s real height, which drags the sidebar into the document's
          own scroll. Confirmed live on the get-started page, whose four steps are long
          enough to expose it. */}
      <main className="relative min-w-0 flex-1 overflow-y-auto bg-card">{children}</main>

      <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  )
}
