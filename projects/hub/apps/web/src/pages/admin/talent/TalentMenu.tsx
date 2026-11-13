import { Link } from '@tanstack/react-router'
import { MoreHorizontal } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { VETTED_MARK_LABEL, VETTED_UNMARK_LABEL } from '@/lib/format'

/** The row's own actions (REB-387), on a card only: a bare sign-up has no card to pair
 *  with a company. The name keeps linking to the card. REB-518 adds the vetted
 *  flag, on or off, last. */
export function TalentMenu({
  id,
  label,
  vetted,
  vetting,
  onVet,
}: {
  id: string
  label: string
  vetted: boolean
  vetting: boolean
  onVet: (vetted: boolean) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`Azioni per ${label}`}>
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem disabled={vetting} onSelect={() => onVet(!vetted)}>
          {vetted ? VETTED_UNMARK_LABEL : VETTED_MARK_LABEL}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
