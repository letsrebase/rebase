import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Link } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useDeals } from '@/features/deals/queries'
import { useLogTime } from '@/features/time/queries'
import { toProblem } from '@/lib/api'
import { useAuth, useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { GIORNATA, MEZZA, normaliseHours } from './hours'
import {
  useCancelAttivita,
  useCompleteAttivita,
  useCreateAttivita,
  type CalendarDay,
} from './queries'

/**
 * One day, opened from a cell: what was worked, what falls due, and the two things that
 * can be added to it.
 *
 * The write paths are deliberately the ordinary ones. Hours go through `POST
 * /api/time-entries`, so the rate is frozen, a closed period refuses and the activity
 * row is written exactly as for any other hour -- this dialog is a way of filling that
 * form, not a second way of writing the table. Commitments go through `POST
 * /api/activities` with this day as `scadenza`.
 *
 * An invoice falling due is shown and linked, and cannot be changed from here: its due
 * date belongs to the invoice.
 */
export function DayPanel({
  giorno,
  data,
  onClose,
}: {
  giorno: string
  data: CalendarDay | undefined
  onClose: () => void
}) {
  const canWrite = useCanWrite()
  const [problem, setProblem] = useState<string | null>(null)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{formatIsoDateItalian(giorno)}</DialogTitle>
        </DialogHeader>

        <div className="space-y-5">
          <Worked data={data} />
          <Due data={data} onProblem={setProblem} canWrite={canWrite} />
          {canWrite && (
            <>
              <LogHours giorno={giorno} onProblem={setProblem} />
              <NewCommitment giorno={giorno} onProblem={setProblem} />
            </>
          )}
          {problem !== null && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Chiudi
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Worked({ data }: { data: CalendarDay | undefined }) {
  const righe = data?.per_deal ?? []
  if (righe.length === 0) return <p className="text-sm text-muted-foreground">Nessuna ora.</p>
  return (
    <section aria-labelledby="ore-del-giorno" className="space-y-1">
      <h3 id="ore-del-giorno" className="text-sm font-medium">
        Ore ({data?.ore})
      </h3>
      <ul className="space-y-1 text-sm">
        {righe.map((riga) => (
          <li key={riga.deal_id} className="flex justify-between gap-4">
            <span>
              {riga.cliente ? `${riga.cliente} · ` : ''}
              {riga.deal_nome}
            </span>
            <span className="tabular-nums">{riga.ore} h</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The day's deadlines: commitments, which can be closed from here, and invoices, which
 *  cannot be touched from here at all. */
function Due({
  data,
  onProblem,
  canWrite,
}: {
  data: CalendarDay | undefined
  onProblem: (message: string | null) => void
  canWrite: boolean
}) {
  const complete = useCompleteAttivita()
  const cancel = useCancelAttivita()
  const attivita = data?.attivita ?? []
  const fatture = data?.fatture ?? []
  if (attivita.length === 0 && fatture.length === 0) return null

  function close(mutation: typeof complete, id: string, done: string) {
    onProblem(null)
    mutation.mutate(id, {
      onSuccess: () => toast.success(done),
      onError: (error) => onProblem(toProblem(error).detail),
    })
  }

  return (
    <section aria-labelledby="scadenze-del-giorno" className="space-y-2">
      <h3 id="scadenze-del-giorno" className="text-sm font-medium">
        Scadenze
      </h3>
      <ul className="space-y-2 text-sm">
        {attivita.map((item) => (
          <li key={item.id} className="flex items-center justify-between gap-3">
            <span className={item.stato === 'aperta' ? undefined : 'line-through opacity-60'}>
              {item.titolo}
            </span>
            {canWrite && item.stato === 'aperta' && (
              <span className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => close(complete, item.id, 'Fatto')}
                >
                  Fatto
                </Button>
                {/* «Annulla» is not a delete and not «fatto»: it records that the
                    commitment stopped mattering, which is the answer to *why* six
                    months later. */}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => close(cancel, item.id, 'Annullata')}
                >
                  Annulla
                </Button>
              </span>
            )}
          </li>
        ))}
        {fatture.map((fattura) => (
          <li key={fattura.id} className="flex items-center justify-between gap-3">
            <Link
              to="/app/invoices/$invoiceId"
              params={{ invoiceId: fattura.id }}
              className="underline underline-offset-4"
            >
              Fattura {fattura.numero ?? '—'}
              {fattura.cliente ? ` · ${fattura.cliente}` : ''}
            </Link>
            <span className="tabular-nums">
              {fattura.totale} €{fattura.in_ritardo ? ' · scaduta' : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function LogHours({
  giorno,
  onProblem,
}: {
  giorno: string
  onProblem: (message: string | null) => void
}) {
  const { user } = useAuth()
  const deals = useDeals()
  const log = useLogTime()
  const [dealId, setDealId] = useState('')
  const [ore, setOre] = useState(GIORNATA)
  const [descrizione, setDescrizione] = useState('')

  const valide = normaliseHours(ore)

  /**
   * `quante` is passed in rather than read from state, and that is not a style choice:
   * `setOre(GIORNATA)` followed by `submit()` sent whatever was in the field *before*
   * the click, because a `useState` setter does not update the current render's value.
   * Pressing «Giornata» after typing 4 logged four hours and said «giornata».
   */
  function submit(quante: string) {
    const hours = normaliseHours(quante)
    if (!user || dealId === '' || hours === null) return
    onProblem(null)
    log.mutate(
      {
        deal_id: dealId,
        user_id: user.id,
        data: giorno,
        ore: hours,
        descrizione: descrizione.trim() === '' ? 'Ore' : descrizione.trim(),
      },
      {
        onSuccess: () => {
          toast.success('Ore registrate')
          setDescrizione('')
        },
        onError: (error) => onProblem(toProblem(error).detail),
      },
    )
  }

  return (
    <section aria-labelledby="registra-ore" className="space-y-2 border-t pt-4">
      <h3 id="registra-ore" className="text-sm font-medium">
        Registra ore
      </h3>
      <div className="grid gap-3 sm:grid-cols-[1fr_6rem]">
        <div className="space-y-1">
          <Label htmlFor="deal-del-giorno">Deal</Label>
          <Select value={dealId} onValueChange={setDealId}>
            <SelectTrigger id="deal-del-giorno">
              <SelectValue placeholder="Scegli il deal" />
            </SelectTrigger>
            <SelectContent>
              {(deals.data?.items ?? []).map((deal) => (
                <SelectItem key={deal.id} value={deal.id}>
                  {deal.nome}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          {/* A visible label, not a screen-reader-only one: this field is half the
              point of the panel -- «oltre alla giornata piena, dammi modo di mettere le
              ore» -- and a bare box next to a Deal picker reads as part of it. */}
          <Label htmlFor="ore-del-giorno-input">Ore</Label>
          <Input
            id="ore-del-giorno-input"
            inputMode="decimal"
            value={ore}
            onChange={(event) => setOre(event.target.value)}
            aria-invalid={ore.trim() !== '' && valide === null}
            aria-label="Ore"
          />
        </div>
      </div>
      <Input
        placeholder="Descrizione (facoltativa)"
        value={descrizione}
        onChange={(event) => setDescrizione(event.target.value)}
        aria-label="Descrizione"
      />
      <div className="flex flex-wrap items-center gap-2">
        {/* The typed value first, because it is the general case: whatever is in the
            field, registered as it is. The two presets after it are shortcuts that fill
            the field *and* send, so eight hours on a deal stays two clicks. */}
        <Button
          size="sm"
          disabled={dealId === '' || valide === null || log.isPending}
          onClick={() => submit(ore)}
        >
          Registra {valide ?? '—'} h
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={dealId === '' || log.isPending}
          onClick={() => {
            setOre(GIORNATA)
            submit(GIORNATA)
          }}
        >
          Giornata (8h)
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={dealId === '' || log.isPending}
          onClick={() => {
            setOre(MEZZA)
            submit(MEZZA)
          }}
        >
          Mezza (4h)
        </Button>
      </div>
      {ore.trim() !== '' && valide === null && (
        <p className="text-sm text-muted-foreground">
          Le ore vanno scritte come numero, da 0,25 a 24 — per esempio 7,5 oppure 3.
        </p>
      )}
    </section>
  )
}

function NewCommitment({
  giorno,
  onProblem,
}: {
  giorno: string
  onProblem: (message: string | null) => void
}) {
  const create = useCreateAttivita()
  const [titolo, setTitolo] = useState('')

  function submit() {
    const trimmed = titolo.trim()
    if (trimmed === '') return
    onProblem(null)
    create.mutate(
      // `custom_fields` is required by the generated schema (the field has a default
      // server-side, which openapi-typescript reads as «always present»), and an empty
      // object is what «this commitment has none» is.
      { titolo: trimmed, scadenza: giorno, custom_fields: {} },
      {
        onSuccess: () => {
          toast.success('Scadenza aggiunta')
          setTitolo('')
        },
        onError: (error) => onProblem(toProblem(error).detail),
      },
    )
  }

  return (
    <section aria-labelledby="nuova-scadenza" className="space-y-2 border-t pt-4">
      <h3 id="nuova-scadenza" className="text-sm font-medium">
        Nuova scadenza
      </h3>
      <div className="flex gap-2">
        <Input
          placeholder="Che cosa scade"
          value={titolo}
          onChange={(event) => setTitolo(event.target.value)}
          aria-label="Titolo della scadenza"
          onKeyDown={(event) => {
            if (event.key === 'Enter') submit()
          }}
        />
        <Button size="sm" disabled={titolo.trim() === '' || create.isPending} onClick={submit}>
          Aggiungi
        </Button>
      </div>
    </section>
  )
}
