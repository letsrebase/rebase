import { useRouterState } from '@tanstack/react-router'
import { PanelLeftIcon, Search, Settings } from 'lucide-react'
import { useState } from 'react'
import { BrandMark } from '@rebase/ui/brand-mark'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { useOverdueCount } from '@/features/tasks/queries'
import { useUnreadCount } from '@/features/reminders/queries'
import { useAuth } from '@/lib/auth'
import { LegalEntitySelect } from './LegalEntitySelect'
import {
  Agents,
  HOME,
  SECTIONS,
  SETTINGS,
  SETTINGS_PATHS,
  isUnder,
  settingsSubItems,
} from './nav'
import { NavGroup, SubItem } from './NavGroup'
import { NavLeaf } from './NavLeaf'
import { ProfileMenu } from './ProfileMenu'
import { FOCUS } from './sidebarStyles'

// `metaKey` on Apple platforms, `ctrlKey` elsewhere. Read once at module scope from the
// platform hint rather than sniffing the user agent string: this only decides which glyph
// is drawn, and the palette's listener accepts either modifier regardless.
const IS_APPLE =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform ?? '')

/**
 * The dark column: brand and collapse toggle, the search field, the azienda selector, the
 * navigation (`nav.ts`) and the profile menu at the foot. `rail` is the icon-only form,
 * `overlay` the expanded form drawn over the page below `lg`.
 */
export function Sidebar({
  rail,
  overlay,
  onToggle,
  onSearch,
  go,
}: {
  rail: boolean
  overlay: boolean
  onToggle: () => void
  onSearch: () => void
  go: (url: string) => void
}) {
  const { user } = useAuth()
  const { location } = useRouterState()
  // The sidebar's unread count (REB-734): the reminders still open for this person. The
  // Avvisi page reads the same query, so a snooze there moves this number at once.
  const unread = useUnreadCount()
  // The one number of the product that asks for an action (slice 7 §5): how many of my
  // attività are past their deadline, drawn beside «Attività» (REB-873: it sat beside «Oggi»
  // while that was an entry) and only when it is above zero.
  const overdue = useOverdueCount()

  // The «Impostazioni» sub-items this reader may see (REB-294): the same
  // `canSeeSettingsTab` the settings page filters its own tabs with, so sidebar and
  // page read one table and cannot drift. The group itself is shown to every role.
  const settingsItems = user === null ? [] : settingsSubItems(user.role, location.pathname)
  // «Impostazioni» is the one entry that opens onto sub-items. It follows the route: the
  // page holding the current route is open by itself, since the alternative is a page whose
  // own entry is not on screen, and the alternative to *that* -- nailing it open -- is a
  // chevron that does nothing. A press settles it only while the route stays on the same
  // side of the settings: it is remembered with the side it was made on, so leaving or
  // entering the settings hands the choice back to the route. What one route did on your
  // behalf is not a preference worth storing, and neither is this.
  const onSettings = isUnder(location.pathname, SETTINGS.base)
  const [settingsToggled, setSettingsToggled] = useState<{
    open: boolean
    onSettings: boolean
  } | null>(null)
  const settingsOpen =
    settingsToggled !== null && settingsToggled.onSettings === onSettings
      ? settingsToggled.open
      : onSettings

  return (
    <aside
      className={cn(
        // As tall as the viewport, never as tall as the page: the profile at the bottom
        // is reachable without scrolling, and the navigation scrolls on its own if it
        // ever outgrows the window. `h-full` of the shell (itself `h-full` of #root),
        // not `100dvh`: see the html/body/#root rule in tokens.css.
        'flex h-full shrink-0 flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-linear',
        rail ? 'w-[4.5rem]' : 'w-[17rem]',
        // Below `lg` the expanded sidebar is an overlay over the content, not a column
        // beside it: 272px of the 390px a phone has is not a layout, it is a menu.
        overlay && 'fixed inset-y-0 left-0 z-40 shadow-2xl',
      )}
    >
      <div
        className={cn(
          'flex items-center gap-2 px-4 pt-5 pb-3',
          rail ? 'justify-center' : 'justify-between',
        )}
      >
        <span
          className={cn(
            'inline-flex items-center truncate text-lg font-medium tracking-tight',
            rail && 'sr-only',
          )}
        >
          <BrandMark className="mr-2.5" />
          {/* The wordmark's accent is the sidebar's own tint of the brand red, not the raw
              --color-watermelon: that one is 4.17:1 on this ground, under AA at this size. */}
          Pigro<span className="text-sidebar-wordmark-accent">CRM</span>
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          className={cn(
            'shrink-0 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground',
            FOCUS,
          )}
          onClick={onToggle}
          aria-label={rail ? 'Espandi il menu' : 'Comprimi il menu'}
        >
          <PanelLeftIcon className="size-4" />
        </Button>
      </div>

      {/* A button that looks like a field, not an <input>: the palette is a dialog, so a
          real text field here would take focus, accept typing, and then hand it over --
          two places to type the same query. One control, one place to type. */}
      {/* Named, for the same reason the nav below is: a landmark whose accessible name
          is only its role tells a screen-reader user nothing, and a page's own filter
          row is entitled to a search landmark of its own. */}
      <div role="search" aria-label="Ricerca globale" className="px-3 pb-3">
        <button
          type="button"
          onClick={onSearch}
          aria-label="Cerca in tutto il CRM"
          className={cn(
            'flex w-full items-center gap-2 border border-sidebar-border bg-sidebar-accent/50 px-3 py-2 text-sm text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground',
            FOCUS,
            rail && 'justify-center px-0',
          )}
        >
          <Search className="size-4 shrink-0" aria-hidden="true" />
          <span className={cn('flex-1 text-left', rail && 'sr-only')}>Cerca</span>
          <kbd
            className={cn(
              'border border-sidebar-border px-1.5 py-0.5 text-xs font-medium',
              rail && 'sr-only',
            )}
          >
            {IS_APPLE ? '⌘' : 'Ctrl'} K
          </kbd>
        </button>
      </div>

      <LegalEntitySelect rail={rail} />

      {/* Labelled because a page's own header may render a second <nav> landmark (a
          breadcrumb, a set of tabs), and two unlabelled ones are indistinguishable to a
          screen reader -- and to `getByRole('navigation')`. */}
      <nav
        aria-label="Navigazione principale"
        className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-3"
      >
        <div className="space-y-1">
          <NavLeaf {...HOME} rail={rail} />
          {SECTIONS.map((section) => (
            // A group named by its caption, not a control: nothing opens or closes, so the
            // caption is a plain line of text above its entries. In the rail it is a thin
            // separator, and the group keeps its name for a screen reader either way.
            <div key={section.id} role="group" aria-label={section.caption} className="space-y-1">
              {rail ? (
                <div aria-hidden="true" className="mx-2 mt-3 mb-2 border-t border-sidebar-border" />
              ) : (
                <p
                  aria-hidden="true"
                  className="px-3 pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-sidebar-foreground/70"
                >
                  {section.caption}
                </p>
              )}
              {section.items.map((item) => (
                <NavLeaf
                  key={item.to}
                  {...item}
                  rail={rail}
                  count={item.to === '/app/reminders' ? unread : 0}
                  overdue={overdue}
                />
              ))}
            </div>
          ))}
        </div>

        {/* The foot (REB-873): «Agenti» and «Impostazioni», the two entries that are about
            the account and the space rather than about the work. `mt-auto` keeps them at the
            bottom of the column while the lists above are short; when the window is not tall
            enough the whole navigation scrolls and they scroll with it, never under the
            profile. «Impostazioni» is the one entry that still opens onto sub-items, and in
            the rail it is one link to the first of them. */}
        <div className="mt-auto space-y-1 border-t border-sidebar-border pt-3">
          <NavLeaf {...Agents} rail={rail} />
          {rail ? (
            // The first section, under the group's own name: in the rail the label is the
            // accessible name, and «Profilo» would say nothing about where it goes.
            // Profilo is open to every role, so every role has this link.
            <NavLeaf
              to={SETTINGS_PATHS.profile}
              label={SETTINGS.label}
              icon={Settings}
              rail={rail}
            />
          ) : (
            <NavGroup
              label={SETTINGS.label}
              icon={SETTINGS.icon}
              open={settingsOpen}
              onToggle={() => setSettingsToggled({ open: !settingsOpen, onSettings })}
            >
              {settingsItems.map((item) => (
                <SubItem key={item.to} {...item} />
              ))}
            </NavGroup>
          )}
        </div>
      </nav>

      <ProfileMenu rail={rail} go={go} />
    </aside>
  )
}
