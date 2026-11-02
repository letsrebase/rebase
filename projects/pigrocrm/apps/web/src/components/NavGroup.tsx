import { Link } from '@tanstack/react-router'
import type { LucideIcon } from 'lucide-react'
import { ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'
import type { LinkTo } from './nav'
import { ACTIVE, FOCUS, ITEM, QUIET } from './sidebarStyles'

/**
 * A group header and, when it is open, its sub-items.
 *
 * `aria-expanded` on a real `<button>` rather than a styled `<div>`: the state of a
 * disclosure is something a screen reader has to be able to read and a keyboard has to be
 * able to change, and both come free this way.
 */
export function NavGroup({
  label,
  icon: Icon,
  open,
  onToggle,
  children,
}: {
  label: string
  icon: LucideIcon
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(ITEM, QUIET, FOCUS, 'w-full')}
      >
        <Icon className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown
          className={cn('size-4 shrink-0 transition-transform', !open && '-rotate-90')}
          aria-hidden="true"
        />
      </button>
      {open && (
        // The thin vertical line of the reference: one border on the list, so it runs the
        // height of the sub-items whatever their number.
        <ul className="mt-1 ml-6 space-y-0.5 border-l border-sidebar-border pl-3">{children}</ul>
      )}
    </div>
  )
}

/** A sub-item: label only. The indentation and the line say where it belongs. */
export function SubItem({ to, label, active }: { to: LinkTo; label: string; active?: boolean }) {
  return (
    <li>
      <Link
        to={to}
        // As on the leaves above: a list that puts its filter in the URL keeps its own
        // entry marked.
        activeOptions={{ includeSearch: false }}
        activeProps={{ 'aria-current': 'page' }}
        // A sub-item that stands for several routes (a settings group) says itself when
        // it is current: the link's own match covers only its first section.
        {...(active ? { 'aria-current': 'page' as const, 'data-status': 'active' } : {})}
        className={cn(
          'relative block truncate px-3 py-1.5 text-sm transition-colors before:absolute before:left-0 before:top-1/2 before:size-1.5 before:-translate-y-1/2 before:bg-transparent before:content-[""]',
          QUIET,
          ACTIVE,
          FOCUS,
        )}
      >
        {label}
      </Link>
    </li>
  )
}
