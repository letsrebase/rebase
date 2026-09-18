import { cn } from '@rebase/ui/cn'
import type { Deal, Stage } from './queries'

/**
 * The pipeline drawn as one row of segments, with the deal's own stage marked on it --
 * the element a Pipedrive deal page is recognised by, and the one thing a person wants
 * to know about a deal before anything else: where is it.
 *
 * Only the open stages are segments, in their pipeline order. «Vinto» and «Perso» are
 * not steps a deal passes through, they are how it ended, so a closed deal shows every
 * segment settled and says the outcome in a word beside the bar. Each segment is a
 * button: pressing one moves the deal there (the same `PATCH /stage` the Kanban's drag
 * performs), which is also how a closed deal is reopened -- no second control for it.
 * A reader without write access gets the same bar with nothing to press.
 *
 * Presentational on purpose: the move itself is `useMoveDeal`, whose optimistic update,
 * rollback and toast are already proven on the board. This component only says which
 * stage was chosen.
 */
export function DealStageBar({
  deal,
  stages,
  canMove,
  busy = false,
  onMove,
}: {
  deal: Deal
  stages: Stage[]
  canMove: boolean
  busy?: boolean
  onMove: (stageId: string) => void
}) {
  const open = stages
    .filter((stage) => stage.tipo === 'open')
    .sort((a, b) => a.posizione - b.posizione)
  const current = stages.find((stage) => stage.id === deal.pipeline_stage_id)
  const outcome = current?.tipo === 'won' || current?.tipo === 'lost' ? current.tipo : null
  const currentIndex = open.findIndex((stage) => stage.id === deal.pipeline_stage_id)

  return (
    <div role="group" aria-label="Fase del deal" className="space-y-2">
      <ol className="flex gap-1">
        {open.map((stage, index) => {
          const isCurrent = stage.id === deal.pipeline_stage_id
          // Everything up to the current stage is done; on a closed deal all of it is.
          const reached = outcome !== null || (currentIndex >= 0 && index <= currentIndex)
          return (
            <li key={stage.id} className="min-w-0 flex-1">
              <button
                type="button"
                title={stage.nome}
                aria-current={isCurrent ? 'step' : undefined}
                disabled={!canMove || busy || isCurrent}
                onClick={() => onMove(stage.id)}
                className={cn(
                  'flex h-9 w-full items-center justify-center truncate rounded-md px-2 text-xs font-medium transition-colors',
                  'disabled:cursor-default',
                  // Ink for what is done, the ink faded for what is not: a stage is a
                  // settled fact, not a call to action, so it does not wear the primary
                  // red -- a won deal drawn as four red blocks read as an alarm. Only a
                  // lost deal changes colour, and to the tone every «Perso» pill has.
                  outcome === 'lost'
                    ? 'bg-destructive/15 text-destructive'
                    : reached
                      ? 'bg-foreground text-background'
                      : 'bg-muted text-muted-foreground',
                  canMove && !isCurrent && !busy && 'hover:opacity-80',
                  isCurrent && 'ring-2 ring-foreground/30 ring-offset-1 ring-offset-card',
                )}
              >
                <span className="truncate">{stage.nome}</span>
              </button>
            </li>
          )
        })}
      </ol>
      <p className="text-xs text-muted-foreground">
        {outcome !== null ? (
          <>
            <span
              className={cn(
                'font-medium',
                outcome === 'won' ? 'text-foreground' : 'text-destructive',
              )}
            >
              {current?.nome}
            </span>
            {canMove ? ' · Per riaprirlo scegli una fase.' : ''}
          </>
        ) : current ? (
          <>
            Fase attuale: <span className="font-medium text-foreground">{current.nome}</span>
            {canMove ? ' · Clicca una fase per spostarlo.' : ''}
          </>
        ) : (
          'Fase non riconosciuta.'
        )}
      </p>
    </div>
  )
}
