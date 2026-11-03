import { Building2 } from 'lucide-react'
import { cn } from '@rebase/ui/cn'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useLegalEntity } from '@/lib/legalEntity'
import { FOCUS } from './sidebarStyles'

/** The selector's value for «Tutte le aziende»: Radix refuses an empty string, and no
 *  azienda id is this word. */
const EVERY_LEGAL_ENTITY = 'all'

/**
 * The legal entity selector (REB-625): drawn from the second legal entity on, between the search
 * and the navigation, so the lists below it read as «of this azienda».
 */
export function LegalEntitySelect({ rail }: { rail: boolean }) {
  const legal_entity = useLegalEntity()
  if (legal_entity.pinned) {
    // A scoped person with one azienda (REB-635): its name where the selector
    // would be, so the lists below still read as «of this azienda», and no
    // control, since there is nothing to choose between.
    return (
      <div className="px-3 pb-3">
        <p
          role="note"
          aria-label="Azienda"
          className={cn(
            'flex h-9 items-center gap-2 border border-sidebar-border bg-sidebar-accent/50 px-3 text-sm text-sidebar-foreground/70',
            rail && 'justify-center px-0',
          )}
        >
          <Building2 className="size-4 shrink-0" aria-hidden="true" />
          <span className={cn('flex-1 truncate text-left', rail && 'sr-only')}>
            {legal_entity.legal_entities[0]?.name}
          </span>
        </p>
      </div>
    )
  }
  if (!legal_entity.several) return null
  return (
    <div className="px-3 pb-3">
      <Select
        value={legal_entity.selected ?? EVERY_LEGAL_ENTITY}
        onValueChange={(value) => legal_entity.select(value === EVERY_LEGAL_ENTITY ? null : value)}
      >
        <SelectTrigger
          aria-label="Azienda"
          className={cn(
            'w-full rounded-none border-sidebar-border bg-sidebar-accent/50 text-sidebar-foreground/70 shadow-none hover:bg-sidebar-accent hover:text-sidebar-foreground [&_svg:not([class*=text-])]:text-sidebar-foreground/70',
            FOCUS,
            rail && 'justify-center px-0 [&>svg:last-child]:hidden',
          )}
        >
          <Building2 className="size-4 shrink-0" aria-hidden="true" />
          <span className={cn('flex-1 truncate text-left', rail && 'sr-only')}>
            <SelectValue />
          </span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={EVERY_LEGAL_ENTITY}>Tutte le aziende</SelectItem>
          {legal_entity.legal_entities.map((item) => (
            <SelectItem key={item.id} value={item.id}>
              {item.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
