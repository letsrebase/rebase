import { useDraggable } from '@dnd-kit/core'
import { Card, CardContent } from '@rebase/ui/card'
import { cn } from '@rebase/ui/cn'
import { formatMoney } from './columns'
import type { Deal } from './queries'

interface Props {
  deal: Deal
  onOpen: (dealId: string) => void
  /**
   * Mirrors every other write affordance in this product (the Nuovo/Modifica/
   * Archivia buttons on every list and detail screen, all gated on
   * `useCanWrite()`): a readonly actor can see the board but should not be
   * invited to move a card. This is a UI convenience layered on top of a real,
   * already-enforced server-side rule (`DealService.move_stage` ->
   * `actor.require_write("move_deal")`, confirmed live: `test_readonly_cannot_
   * move_a_deal` in packages/core/tests/test_deals.py), not a substitute for
   * it -- a disabled `useDraggable` here only stops the drag gesture from
   * starting; the 403 the server would return either way is what actually
   * makes it safe to omit this prop or get it wrong.
   */
  canDrag: boolean
}

export function KanbanCard({ deal, onOpen, canDrag }: Props) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: deal.id,
    disabled: !canDrag,
  })

  return (
    <Card
      ref={setNodeRef}
      {...(canDrag ? listeners : undefined)}
      {...attributes}
      onClick={() => onOpen(deal.id)}
      style={
        transform
          ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`, zIndex: 50 }
          : undefined
      }
      className={cn(
        'transition-shadow',
        canDrag && 'cursor-grab active:cursor-grabbing',
        isDragging && 'opacity-60 shadow-lg',
      )}
    >
      <CardContent className="space-y-1 p-3">
        <p className="text-sm font-medium">{deal.nome}</p>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{formatMoney(deal.valore_previsto)}</span>
          <span>{deal.probabilita}%</span>
        </div>
      </CardContent>
    </Card>
  )
}
