import { BadgeEuro, Pencil, Play, Square, Timer, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import type { Deal } from '@/features/deals/queries'
import { toProblem } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { cn } from '@rebase/ui/cn'
import { formatHoursValue } from './columns'
import { elapsedSince, formatElapsed } from './elapsed'
import {
  useDiscardTimer,
  useLogTime,
  useRunningTimer,
  useStartTimer,
  useStopTimer,
  useUpdateTimer,
  type RunningTimer,
} from './queries'
import { normaliseHours } from './week'

/** Radix `Select` refuses an empty string as a value, so "no deal yet" has a name. */
const NO_DEAL = '__nessuno__'

/** What an entry made without a description says: honest about where it came from, and
 *  replaceable from the entry itself. The timer's own default lives on the server. */
const MANUAL_DESCRIZIONE = 'Ore registrate a mano'


/** The live clock. Its own component so the one-second tick re-renders these digits and
 *  nothing else on the page. */
function Elapsed({ timer }: { timer: RunningTimer }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(handle)
  }, [])
  return (
    <span
      role="timer"
      aria-live="off"
      aria-label="Tempo trascorso"
      className="min-w-[6.5rem] text-center text-2xl font-semibold tabular-nums tracking-tight"
    >
      {formatElapsed(elapsedSince(timer.started_at, now))}
    </span>
  )
}

/**
 * Toggl's top bar, in this product's terms: what you are doing, on which deal, whether
 * it is billable, and one big button. Two modes -- a manual line with hours and a date,
 * which is the default, and the stopwatch -- because the thing Toggl gets right is that
 * both are the *same* row of controls, so nobody has to find a second screen to enter
 * yesterday or to start a clock.
 *
 * While the clock runs the row keeps editing the running timer (PATCH on change): the
 * deal and the description are decided when the person knows them, not when they press
 * Start. Stop needs a deal, and the button says so rather than failing.
 */
export function TimerBar({
  deals,
  userId,
  canWrite,
}: {
  deals: Deal[]
  userId: string
  canWrite: boolean
}) {
  const running = useRunningTimer({ enabled: canWrite })
  const start = useStartTimer()
  const update = useUpdateTimer()
  const stop = useStopTimer()
  const discard = useDiscardTimer()
  const log = useLogTime()

  // Manual first (Ivan, 2026-09-09): the common case is «ho fatto due ore», typed after
  // the fact; the stopwatch is the second mode, one click away, and a running clock
  // always shows regardless of the mode the bar was left in.
  const [mode, setMode] = useState<'timer' | 'manuale'>('manuale')
  const [descrizione, setDescrizione] = useState('')
  const [dealId, setDealId] = useState<string>(NO_DEAL)
  const [fatturabile, setFatturabile] = useState(true)
  const [ore, setOre] = useState('')
  const [data, setData] = useState(() => toIsoDate(new Date()))

  const timer = running.data ?? null
  // The running row is the source of truth while it runs: a clock started on another
  // device arrives with its own deal and description, and the form shows those.
  const [adopted, setAdopted] = useState<string | null>(null)
  if (timer && adopted !== timer.id) {
    setAdopted(timer.id)
    setDescrizione(timer.descrizione)
    setDealId(timer.deal_id ?? NO_DEAL)
    setFatturabile(timer.fatturabile)
  }
  if (!timer && adopted !== null) setAdopted(null)

  const failWith = (error: unknown) => toast.error(toProblem(error).detail)
  const chosenDeal = dealId === NO_DEAL ? null : dealId
  const busy = start.isPending || stop.isPending || discard.isPending || log.isPending

  function onStart() {
    start.mutate(
      { deal_id: chosenDeal, descrizione: descrizione.trim(), fatturabile },
      { onError: failWith },
    )
  }

  function onStop() {
    if (chosenDeal === null) {
      toast.error('Scegli il deal su cui registrare le ore prima di fermare il timer.')
      return
    }
    stop.mutate(
      // Today from the browser's own calendar, not the server's UTC day: the person
      // stopping at 00:30 is still on the day they see on their clock.
      { deal_id: chosenDeal, descrizione: descrizione.trim() || null, data: toIsoDate(new Date()) },
      {
        onSuccess: (entry) => {
          toast.success(`Registrate ${formatHoursValue(entry.ore)} ore`)
          setDescrizione('')
        },
        onError: failWith,
      },
    )
  }

  function onDiscard() {
    if (!window.confirm('Scartare il timer senza registrare le ore?')) return
    discard.mutate(undefined, { onError: failWith })
  }

  function onAdd() {
    if (chosenDeal === null) {
      toast.error('Scegli il deal su cui registrare le ore.')
      return
    }
    const value = normaliseHours(ore)
    if (value === '') {
      toast.error('Indica quante ore registrare.')
      return
    }
    log.mutate(
      {
        deal_id: chosenDeal,
        user_id: userId,
        data,
        ore: value,
        descrizione: descrizione.trim() || MANUAL_DESCRIZIONE,
        fatturabile,
      },
      {
        onSuccess: (entry) => {
          toast.success(`Registrate ${formatHoursValue(entry.ore)} ore`)
          setDescrizione('')
          setOre('')
        },
        onError: failWith,
      },
    )
  }

  /** Field edits reach the running timer as they are made; on a stopped bar they are
   *  just form state until Start or Aggiungi. */
  function patch(changes: { deal_id?: string | null; descrizione?: string; fatturabile?: boolean }) {
    if (!timer) return
    update.mutate(changes, { onError: failWith })
  }

  if (!canWrite) return null

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3',
        // A running timer is the one row on the screen that has to stand out, and
        // since 2026-09-18 the weight comes from the width: `--line-strong`, the
        // site's own 2px, where this used to fade the ink to 30% and now would read
        // lighter than the resting border rather than heavier.
        timer && 'border-(length:--line-strong)',
      )}
    >
      <Input
        aria-label="Descrizione"
        placeholder="Su cosa stai lavorando?"
        value={descrizione}
        className="min-w-[12rem] flex-1"
        onChange={(event) => setDescrizione(event.target.value)}
        onBlur={() => timer && descrizione !== timer.descrizione && patch({ descrizione })}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return
          if (timer) event.currentTarget.blur()
          else if (mode === 'timer') onStart()
          else onAdd()
        }}
      />

      <Select
        value={dealId}
        onValueChange={(value) => {
          setDealId(value)
          if (timer) patch({ deal_id: value === NO_DEAL ? null : value })
        }}
      >
        <SelectTrigger className="w-64" aria-label="Deal">
          <SelectValue placeholder="Deal" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_DEAL}>Nessun deal (scegli dopo)</SelectItem>
          {deals.map((deal) => (
            <SelectItem key={deal.id} value={deal.id}>
              {deal.nome}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="flex items-center gap-2">
        <Checkbox
          id="timer-fatturabile"
          checked={fatturabile}
          onCheckedChange={(checked) => {
            const next = checked === true
            setFatturabile(next)
            if (timer) patch({ fatturabile: next })
          }}
        />
        <Label htmlFor="timer-fatturabile" className="flex items-center gap-1 font-normal">
          <BadgeEuro className="size-4" aria-hidden="true" />
          Fatturabile
        </Label>
      </div>

      {timer ? (
        <div className="flex items-center gap-2">
          <Elapsed timer={timer} />
          <Button onClick={onStop} disabled={busy}>
            <Square className="mr-2 size-4" />
            Stop
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Scarta il timer"
            onClick={onDiscard}
            disabled={busy}
          >
            <X className="size-4" />
          </Button>
        </div>
      ) : mode === 'timer' ? (
        <div className="flex items-center gap-2">
          <Button onClick={onStart} disabled={busy}>
            <Play className="mr-2 size-4" />
            Avvia
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Inserisci a mano"
            title="Inserisci a mano"
            onClick={() => setMode('manuale')}
          >
            <Pencil className="size-4" />
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Input
            aria-label="Ore"
            inputMode="decimal"
            placeholder="Ore"
            value={ore}
            className="w-20 text-center tabular-nums"
            onChange={(event) => setOre(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && onAdd()}
          />
          <Input
            aria-label="Data"
            type="date"
            value={data}
            className="w-40"
            onChange={(event) => setData(event.target.value)}
          />
          <Button onClick={onAdd} disabled={busy}>
            Aggiungi
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Usa il timer"
            title="Usa il timer"
            onClick={() => setMode('timer')}
          >
            <Timer className="size-4" />
          </Button>
        </div>
      )}
    </div>
  )
}
