import { useDeferredValue, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { moneyIn } from '../money'
import { itemTitle, movementPill, REASON_LABELS } from './labels'
import {
  useConfirmMatch,
  useMatchPreview,
  useMovementDetail,
  useOpenItems,
  useUnmatch,
  type MatchItem,
  type MatchReason,
  type Movement,
  type MovementMatch,
} from './queries'

/** A choice the panel offers: the live proposal or one of the server's candidates. */
interface Option {
  key: string
  items: readonly MatchItem[]
  reasons: readonly MatchReason[]
}

function idsKey(items: readonly { due_item_id: string }[]): string {
  return items
    .map((item) => item.due_item_id)
    .sort()
    .join(',')
}

/** The movement's size without its sign: the items are magnitudes. */
function magnitude(amount: string): string {
  return amount.startsWith('-') ? amount.slice(1) : amount
}

export function ReasonChips({ reasons }: { reasons: readonly MatchReason[] }) {
  if (reasons.length === 0) return null
  return (
    <ul aria-label="Perché è stata proposta" className="flex flex-wrap gap-1.5">
      {reasons.map((reason) => (
        <li key={reason}>
          <Badge variant="outline">{REASON_LABELS[reason]}</Badge>
        </li>
      ))}
    </ul>
  )
}

function ItemText({ item, currency, showAllocated }: { item: MatchItem; currency: string; showAllocated?: boolean }) {
  return (
    <span className="flex min-w-0 flex-1 items-start justify-between gap-3">
      <span className="min-w-0">
        <span className="block break-words font-medium">{itemTitle(item)}</span>
        <span className="block break-words text-xs text-muted-foreground">
          {item.counterparty_name !== null && item.document_number !== null ? `${item.counterparty_name} · ` : ''}
          {item.due_date !== null ? `scade il ${formatIsoDateItalian(item.due_date)}` : 'senza scadenza'}
        </span>
      </span>
      <span className="shrink-0 tabular-nums">{moneyIn(showAllocated ? item.allocated : item.amount, currency)}</span>
    </span>
  )
}

function ItemRow({
  item,
  checked,
  currency,
  onToggle,
}: {
  item: MatchItem
  checked: boolean
  currency: string
  onToggle: () => void
}) {
  return (
    <li>
      <label className="flex min-h-11 cursor-pointer items-start gap-3 border bg-background px-3 py-2 text-sm">
        <Checkbox
          checked={checked}
          onCheckedChange={onToggle}
          aria-label={`Scegli ${itemTitle(item)}`}
          className="mt-0.5"
        />
        <ItemText item={item} currency={currency} />
      </label>
    </li>
  )
}

/** The small confirmation before an undo, with an optional reason. */
export function UndoDialog({ movementId, onClose }: { movementId: string | null; onClose: () => void }) {
  const unmatch = useUnmatch()
  const [reason, setReason] = useState('')
  const open = movementId !== null

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setReason('')
      unmatch.reset()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Annulla l’abbinamento</DialogTitle>
          <DialogDescription>
            Le voci tornano aperte e il movimento torna da abbinare. I pagamenti scritti dall’abbinamento vengono stornati.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="unmatch-reason">Motivo (facoltativo)</Label>
          <Input id="unmatch-reason" value={reason} onChange={(event) => setReason(event.target.value)} />
        </div>
        {unmatch.isError && (
          <p role="alert" className="text-sm text-destructive">
            {toProblem(unmatch.error).detail}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Non annullare
          </Button>
          <Button
            disabled={unmatch.isPending || movementId === null}
            onClick={() => {
              if (movementId === null) return
              const trimmed = reason.trim()
              unmatch.mutate(
                { movementId, ...(trimmed === '' ? {} : { reason: trimmed }) },
                { onSuccess: onClose },
              )
            }}
          >
            {unmatch.isPending ? 'Annullo…' : 'Annulla l’abbinamento'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MovementHeader({ movement }: { movement: Movement }) {
  const pill = movementPill(movement)
  return (
    <section aria-label="Il movimento" className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-2xl font-semibold tabular-nums">{moneyIn(movement.amount, movement.currency)}</p>
        <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
      </div>
      <p className="text-sm tabular-nums text-muted-foreground">{formatIsoDateItalian(movement.booking_date)}</p>
      {movement.counterparty_name !== null && <p className="break-words text-sm font-medium">{movement.counterparty_name}</p>}
      {movement.description !== null && <p className="break-words text-sm">{movement.description}</p>}
      {movement.end_to_end_id !== null && (
        <p className="break-all text-xs text-muted-foreground">ID end-to-end: {movement.end_to_end_id}</p>
      )}
    </section>
  )
}

function MatchedBlock({
  match,
  currency,
  canUndo,
  onUndo,
}: {
  match: MovementMatch
  currency: string
  canUndo: boolean
  onUndo: () => void
}) {
  return (
    <section aria-label="L’abbinamento" className="space-y-3">
      <h3 className="text-sm font-semibold">{match.items.length === 1 ? 'Ha saldato' : `Ha saldato ${match.items.length} voci`}</h3>
      <ul className="space-y-1.5">
        {match.items.map((item) => (
          <li key={item.due_item_id} className="border bg-background px-3 py-2 text-sm">
            <ItemText item={item} currency={currency} showAllocated />
          </li>
        ))}
      </ul>
      <ReasonChips reasons={match.reasons} />
      {match.note !== null && <p className="break-words text-xs text-muted-foreground">{match.note}</p>}
      {canUndo && (
        <Button variant="outline" onClick={onUndo}>
          Annulla l’abbinamento
        </Button>
      )}
    </section>
  )
}

function Search({
  movementId,
  currency,
  selected,
  elsewhere,
  onToggle,
}: {
  movementId: string
  currency: string
  selected: readonly MatchItem[]
  /** The ids already drawn in the proposals above, which the search does not repeat as kept picks. */
  elsewhere: ReadonlySet<string>
  onToggle: (item: MatchItem) => void
}) {
  const [text, setText] = useState('')
  const deferred = useDeferredValue(text.trim())
  const found = useOpenItems(movementId, deferred, true)
  const chosen = new Set(selected.map((item) => item.due_item_id))
  const results = found.data ?? []
  const listed = new Set(results.map((item) => item.due_item_id))
  // A pick the current query no longer returns stays in the list, so it can still be unticked.
  const kept = selected.filter((item) => !listed.has(item.due_item_id) && !elsewhere.has(item.due_item_id))
  const rows = [...kept, ...results]
  return (
    <section aria-label="Cerca un’altra voce" className="space-y-2">
      <Label htmlFor="open-items-search">Cerca tra le voci aperte</Label>
      <Input
        id="open-items-search"
        type="search"
        placeholder="Numero, cliente o fornitore"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {found.isLoading ? (
        <LoadingState label="Cerco le voci aperte…" />
      ) : found.isError ? (
        <QueryErrorBanner error={found.error} onRetry={() => void found.refetch()} />
      ) : rows.length === 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          Nessuna voce trovata.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((item) => (
            <ItemRow
              key={item.due_item_id}
              item={item}
              checked={chosen.has(item.due_item_id)}
              currency={currency}
              onToggle={() => onToggle(item)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * The matching panel's content for one unmatched movement: the options the server ranked (the
 * live proposal first), what is ticked, the server's sums for it, «Abbina» and the search. The
 * parent remounts it per movement; every figure is a server string.
 */
function Matching({
  movement,
  options,
  phone,
  canConfirm,
}: {
  movement: Movement
  options: readonly Option[]
  phone: boolean
  canConfirm: boolean
}) {
  const [picked, setPicked] = useState<readonly MatchItem[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [asked, setAsked] = useState(false)
  const items = picked ?? options[0]?.items ?? []
  const ids = items.map((item) => item.due_item_id)
  const preview = useMatchPreview(movement.id, ids)
  const confirm = useConfirmMatch(movement.id)
  const currency = movement.currency

  function select(next: readonly MatchItem[]) {
    setPicked(next)
    setAsked(false)
    confirm.reset()
  }

  function isChosen(item: MatchItem): boolean {
    return ids.includes(item.due_item_id)
  }

  /** Unticks a ticked item; ticks an item of the block already in use, or replaces the selection with another block. */
  function toggleIn(option: Option, item: MatchItem) {
    if (isChosen(item)) return select(items.filter((chosen) => chosen.due_item_id !== item.due_item_id))
    const sameBlock = option.items.some(isChosen)
    select(items.length === 0 || sameBlock ? [...items, item] : option.items)
  }

  /** A search pick joins whatever is ticked: a split is often a proposal plus one more item. */
  function toggleSearch(item: MatchItem) {
    select(isChosen(item) ? items.filter((chosen) => chosen.due_item_id !== item.due_item_id) : [...items, item])
  }

  const shown = new Set(options.flatMap((option) => option.items.map((item) => item.due_item_id)))

  const result = preview.data
  const ready = canConfirm && result !== undefined && result.can_confirm && !confirm.isPending
  const partial = result?.partial === true
  const partialItem = result?.items.find((item) => item.allocated !== item.remaining)
  const total = moneyIn(magnitude(movement.amount), currency)

  function onConfirm() {
    if (result === undefined) return
    if (partial && !asked) {
      setAsked(true)
      return
    }
    confirm.mutate({ due_item_ids: ids, accept_partial: partial })
  }

  const primaryLabel = partial && asked ? 'Conferma il pagamento parziale' : 'Abbina'

  const sum =
    ids.length === 0 ? (
      <p role="status" className="text-sm text-muted-foreground">
        Nessuna voce selezionata.
      </p>
    ) : preview.isError ? (
      <QueryErrorBanner error={preview.error} onRetry={() => void preview.refetch()} />
    ) : result === undefined ? (
      <LoadingState label="Calcolo la differenza…" />
    ) : (
      <p role="status" className="text-sm font-medium tabular-nums">
        Selezionato {moneyIn(result.allocated, currency)} di {total}
        {result.can_confirm ? ': nessuna differenza' : `: differenza ${moneyIn(result.difference, currency)}`}
      </p>
    )

  return (
    <>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <MovementHeader movement={movement} />

        {options.length === 0 && (
          <p role="status" className="text-sm text-muted-foreground">
            Nessuna voce aperta corrisponde.
          </p>
        )}

        {options.map((option, index) => {
          const heading =
            index === 0
              ? option.items.length > 1
                ? `La proposta migliore: ${option.items.length} voci`
                : 'La proposta migliore'
              : option.items.length > 1
                ? `Un’altra proposta: ${option.items.length} voci`
                : 'Un’altra proposta'
          return (
            <section key={option.key} aria-label={heading} className="space-y-2">
              <h3 className="text-sm font-semibold">{heading}</h3>
              <ul className="space-y-1.5">
                {option.items.map((item) => (
                  <ItemRow
                    key={item.due_item_id}
                    item={item}
                    checked={isChosen(item)}
                    currency={currency}
                    onToggle={() => toggleIn(option, item)}
                  />
                ))}
              </ul>
              <ReasonChips reasons={option.reasons} />
            </section>
          )
        })}

        {searching ? (
          <Search
            movementId={movement.id}
            currency={currency}
            selected={items}
            elsewhere={shown}
            onToggle={toggleSearch}
          />
        ) : (
          !phone && (
            <Button variant="outline" onClick={() => setSearching(true)}>
              Cerca un’altra voce
            </Button>
          )
        )}

        <div className="space-y-2 border-t pt-3">
          {sum}
          {partial && (
            <p role="note" className="border bg-muted/40 px-3 py-2 text-sm">
              Salda solo una parte di una voce
              {partialItem === undefined
                ? '.'
                : `: ne paga ${moneyIn(partialItem.allocated, currency)} su ${moneyIn(partialItem.remaining, currency)} ancora dovuti.`}
            </p>
          )}
          {confirm.isError && (
            <p role="alert" className="text-sm text-destructive">
              {toProblem(confirm.error).detail}
            </p>
          )}
        </div>
      </div>

      {canConfirm && (
        <div className="flex flex-wrap gap-2 border-t bg-card p-3">
          <Button className={phone ? 'flex-1' : 'w-full'} disabled={!ready} onClick={onConfirm}>
            {confirm.isPending ? 'Abbino…' : primaryLabel}
          </Button>
          {phone && (
            <Button variant="outline" className="flex-1" onClick={() => setSearching(true)}>
              Scegli altre voci
            </Button>
          )}
          {asked && !confirm.isPending && (
            <Button variant="ghost" className="w-full" onClick={() => setAsked(false)}>
              Indietro
            </Button>
          )}
        </div>
      )}
    </>
  )
}

/**
 * The panel of one movement (REB-1042, brief «The matching panel»): beside the table on a wide
 * screen and inside a full-screen sheet on a phone. It reads the movement's detail; while that
 * loads it says so, on an error it shows the banner alone and offers no action.
 */
export function MatchPanelBody({
  movement,
  phone,
  onUndo,
}: {
  movement: Movement
  phone: boolean
  onUndo: (movementId: string) => void
}) {
  const detail = useMovementDetail(movement.id)
  const canConfirm = useCan('confirm_movement_match')
  const canUndo = useCan('undo_movement_match')

  if (detail.isError) {
    return (
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <MovementHeader movement={movement} />
        <QueryErrorBanner error={detail.error} onRetry={() => void detail.refetch()} />
      </div>
    )
  }
  if (detail.data === undefined) {
    return (
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <MovementHeader movement={movement} />
        <LoadingState label="Cerco le voci aperte…" />
      </div>
    )
  }

  const current = detail.data.movement
  if (current.state === 'matched' && current.match !== null) {
    return (
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <MovementHeader movement={current} />
        <MatchedBlock
          match={current.match}
          currency={current.currency}
          canUndo={canUndo}
          onUndo={() => onUndo(current.id)}
        />
      </div>
    )
  }
  if (current.state !== 'unmatched') {
    return (
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <MovementHeader movement={current} />
      </div>
    )
  }

  const options: Option[] = []
  const proposal = current.match
  if (proposal !== null && proposal.state === 'proposed' && proposal.is_current && proposal.items.length > 0) {
    options.push({ key: `proposal-${proposal.id}`, items: proposal.items, reasons: proposal.reasons })
  }
  for (const [index, candidate] of detail.data.candidates.entries()) {
    if (options.some((option) => idsKey(option.items) === idsKey(candidate.items))) continue
    options.push({ key: `candidate-${index}`, items: candidate.items, reasons: candidate.reasons })
  }
  return (
    <Matching
      key={`${current.id}-${options.map((option) => idsKey(option.items)).join('|')}`}
      movement={current}
      options={options}
      phone={phone}
      canConfirm={canConfirm}
    />
  )
}
