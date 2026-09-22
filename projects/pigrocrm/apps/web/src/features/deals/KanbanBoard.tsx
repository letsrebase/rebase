import { DndContext, PointerSensor, useDroppable, useSensor, useSensors } from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import { Badge } from '@rebase/ui/badge'
import { KanbanCard } from './KanbanCard'
import { sumValorePrevisto } from './columns'
import type { Deal, Stage } from './queries'

function Column({
  stage,
  deals,
  onOpen,
  canDrag,
}: {
  stage: Stage
  deals: Deal[]
  onOpen: (id: string) => void
  canDrag: boolean
}) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id })

  return (
    <div className="flex w-72 shrink-0 flex-col">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">{stage.nome}</h2>
          <Badge variant="secondary">{deals.length}</Badge>
        </div>
        <span className="text-xs text-muted-foreground">{sumValorePrevisto(deals)}</span>
      </div>

      <div
        ref={setNodeRef}
        className={`min-h-40 flex-1 space-y-2 border border-dashed p-2 transition-colors ${
          isOver ? 'border-[var(--color-watermelon)] bg-muted' : ''
        }`}
      >
        {deals.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Nessun deal</p>
        ) : (
          deals.map((deal) => (
            <KanbanCard key={deal.id} deal={deal} onOpen={onOpen} canDrag={canDrag} />
          ))
        )}
      </div>
    </div>
  )
}

/**
 * Pure, and exported alongside `KanbanBoard` (see this file's own eslint
 * override): decides what a drop means, or that it means nothing, with none of
 * dnd-kit's own pointer-sensor plumbing in the way. Kept out of `DndContext`'s
 * `onDragEnd` on purpose -- jsdom implements no real layout (`getBoundingClientRect`
 * always reports zeros), so a simulated pointer drag through `@dnd-kit/core`'s
 * sensors cannot be exercised meaningfully in this test file at all; this is the
 * one piece of the drag-end decision that is worth testing directly, in
 * isolation, rather than not testing.
 *
 * Returns the move to make, or `null` for "do nothing": dropped outside any
 * column (`overId` absent), dropped back on the deal's own current column (no
 * actual move, and no reason to spend a PATCH on it), or an `activeId` that no
 * longer matches a known deal (the list refetched out from under an in-flight
 * drag).
 */
export function resolveMove(
  deals: Deal[],
  activeId: string | number,
  overId: string | number | undefined,
): { dealId: string; stageId: string } | null {
  if (overId === undefined || typeof overId !== 'string') return null
  const deal = deals.find((item) => item.id === activeId)
  if (!deal || deal.pipeline_stage_id === overId) return null
  return { dealId: deal.id, stageId: overId }
}

interface Props {
  stages: Stage[]
  deals: Deal[]
  onMove: (dealId: string, stageId: string) => void
  onOpen: (dealId: string) => void
  /** See `KanbanCard`'s own doc. Defaults to `true` so the required test file
   *  (which never passes it) keeps exercising the normal, draggable board. */
  canDrag?: boolean
}

export function KanbanBoard({ stages, deals, onMove, onOpen, canDrag = true }: Props) {
  // A small activation distance keeps a click on a card (to open it) from also
  // registering as a drag.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  function handleDragEnd(event: DragEndEvent) {
    const move = resolveMove(deals, event.active.id, event.over?.id)
    if (move) onMove(move.dealId, move.stageId)
  }

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      {/* `items-start`, not flexbox's own stretch default: without it, every
          column's height (and therefore its droppable's -- `flex-1` inside a
          `flex-col` column) is forced to match the *tallest* one. One busy
          stage (naturally the common case: an early stage like "Lead" collects
          more open deals over a pipeline's life than a terminal one) then
          drags every other column's drop zone down the page with it -- found
          live, not in review: with one 57-deal column next to a two-deal one,
          every column measured exactly the same ~5974px tall, and the empty
          ones read as one giant mostly-blank box. */}
      <div className="flex items-start gap-4 overflow-x-auto pb-4">
        {[...stages]
          .sort((a, b) => a.posizione - b.posizione)
          .map((stage) => (
            <Column
              key={stage.id}
              stage={stage}
              deals={deals.filter((deal) => deal.pipeline_stage_id === stage.id)}
              onOpen={onOpen}
              canDrag={canDrag}
            />
          ))}
      </div>
    </DndContext>
  )
}
