import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { useDeals, type Deal } from '@/features/deals/queries'
import { fieldErrorFrom, toProblem } from '@/lib/api'
import { useUsers, type UserRecord } from './queries'
import { useSetDealRate, useSetUserRates } from './queries.timetracking'

/**
 * The one sentence both tables need, and the only thing that makes this screen safe to
 * use: a rate is copied onto an hours row when the row is created, so editing it here
 * is a decision about the future and never a silent rewrite of the past.
 */
function StandingNote() {
  return (
    <p className="text-sm text-muted-foreground">
      Cambiare una tariffa non modifica nessuna voce di ore già registrata: la tariffa viene
      copiata sulla riga quando la voce è creata, e nessun report la rilegge. Per riscrivere
      le tariffe di voci già esistenti serve il ricalcolo, che è un&apos;operazione esplicita
      sul singolo deal e rifiuta le voci già fatturate.
    </p>
  )
}

/** `""` means "no rate", and the API's own spelling for that is `null` -- not `0`, which
 *  is a real rate meaning the hour is worth nothing. */
function rateBody(value: string): string | null {
  return value.trim() === '' ? null : value.trim()
}

/** Nullable message, so a row can clear the page banner as well as raise it: the banner
 *  outlives any single row's mutation, and a stale one sits there contradicting the
 *  success toast printed for the press that fixed it. */
type OnProblem = (message: string | null) => void

/**
 * One row's own error handling.
 *
 * A rate row has two inputs and the page has one banner, at the top, above every other
 * row: a 422 naming `costo_orario_default` put there is true and useless, because it
 * never says which of the dozens of identical inputs on this screen it is about. So
 * anything the server attributed to a field stays on the row and marks that input;
 * anything it did not (a 403, a network failure) goes to the page banner, which is the
 * only thing wide enough to hold it. Both are cleared before each attempt.
 */
function useRowProblem(onProblem: OnProblem) {
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(null)

  function begin() {
    setFieldError(null)
    onProblem(null)
  }

  function fail(error: unknown) {
    const problem = toProblem(error)
    const attributed = fieldErrorFrom(problem)
    setFieldError(attributed)
    if (!attributed) onProblem(problem.detail)
  }

  return { fieldError, begin, fail }
}

function FieldMessage({ text }: { text: string }) {
  return <p className="mt-1 text-sm text-destructive">{text}</p>
}

/**
 * One row per user, its own component: `useSetUserRates(id)` is built from the row's id,
 * and calling it inside the parent's `.map` would be a variable number of hooks in a
 * variable order.
 */
function UserRateRow({ user, onProblem }: { user: UserRecord; onProblem: OnProblem }) {
  const [tariffa, setTariffa] = useState(user.tariffa_oraria_default ?? '')
  const [costo, setCosto] = useState(user.costo_orario_default ?? '')
  const save = useSetUserRates(user.id)
  const { fieldError, begin, fail } = useRowProblem(onProblem)

  return (
    <TableRow>
      <TableCell>{user.nome}</TableCell>
      <TableCell>
        <Input
          type="number"
          step="0.000001"
          aria-label={`Tariffa oraria di ${user.nome}`}
          aria-invalid={fieldError?.field === 'tariffa_oraria_default'}
          value={tariffa}
          onChange={(event) => setTariffa(event.target.value)}
          className="w-32"
        />
        {fieldError?.field === 'tariffa_oraria_default' && (
          <FieldMessage text={fieldError.message} />
        )}
      </TableCell>
      <TableCell>
        <Input
          type="number"
          step="0.000001"
          aria-label={`Costo orario di ${user.nome}`}
          aria-invalid={fieldError?.field === 'costo_orario_default'}
          value={costo}
          onChange={(event) => setCosto(event.target.value)}
          className="w-32"
        />
        {fieldError?.field === 'costo_orario_default' && (
          <FieldMessage text={fieldError.message} />
        )}
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          disabled={save.isPending}
          onClick={() => {
            begin()
            // Both values every time, because `UserRatesUpdate` is the whole pair: the
            // strings travel as typed, so a rate at the sixth decimal place -- which is
            // what Numeric(12,6) exists for -- survives the trip untouched. The server
            // reads it with `exclude_unset`, so an omitted key means "leave it alone"
            // and only a key present with `null` can clear a rate.
            save.mutate(
              {
                tariffa_oraria_default: rateBody(tariffa),
                costo_orario_default: rateBody(costo),
              },
              { onSuccess: () => toast.success('Tariffe aggiornate'), onError: fail },
            )
          }}
        >
          Salva
        </Button>
      </TableCell>
    </TableRow>
  )
}

function DealRateRow({ deal, onProblem }: { deal: Deal; onProblem: OnProblem }) {
  const [tariffa, setTariffa] = useState(deal.tariffa_oraria ?? '')
  const save = useSetDealRate(deal.id)
  const { fieldError, begin, fail } = useRowProblem(onProblem)

  return (
    <TableRow>
      <TableCell>{deal.nome}</TableCell>
      <TableCell>
        <Input
          type="number"
          step="0.000001"
          aria-label={`Tariffa oraria del deal ${deal.nome}`}
          aria-invalid={fieldError?.field === 'tariffa_oraria'}
          value={tariffa}
          onChange={(event) => setTariffa(event.target.value)}
          className="w-32"
        />
        {fieldError?.field === 'tariffa_oraria' && <FieldMessage text={fieldError.message} />}
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          disabled={save.isPending}
          onClick={() => {
            begin()
            // The deal's own rate, on the deal's own endpoint: it overrides the
            // person's rate for hours on this deal and never writes to the person.
            save.mutate(
              { tariffa_oraria: rateBody(tariffa) },
              { onSuccess: () => toast.success('Tariffa del deal aggiornata'), onError: fail },
            )
          }}
        >
          Salva
        </Button>
      </TableCell>
    </TableRow>
  )
}

export function RatesPanel() {
  const users = useUsers()
  const deals = useDeals()
  const [problem, setProblem] = useState<string | null>(null)
  const [nuovoDeal, setNuovoDeal] = useState('')
  const [nuovaTariffa, setNuovaTariffa] = useState('')
  const addRate = useSetDealRate(nuovoDeal)

  if (users.isError) return <QueryErrorBanner error={users.error} />
  if (deals.isError) return <QueryErrorBanner error={deals.error} />

  const allDeals = deals.data?.items ?? []
  // Split on the stored value, not on a truthiness test: `"0.000000"` is a real deal
  // rate meaning "these hours are not billed", and it belongs in the table, not in the
  // "no rate yet" picker.
  const withRate = allDeals.filter((deal) => deal.tariffa_oraria !== null)
  const withoutRate = allDeals.filter((deal) => deal.tariffa_oraria === null)

  return (
    <div className="space-y-8">
      {problem && (
        <p
          role="alert"
          className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {problem}
        </p>
      )}

      <section className="space-y-3">
        <h2 className="font-semibold">Tariffe delle persone</h2>
        <StandingNote />
        <p className="text-sm text-muted-foreground">
          La tariffa è quello che si fattura; il costo è quello che l&apos;ora costa
          all&apos;azienda, e serve solo al margine. Lasciare un campo vuoto significa
          «nessuna tariffa», che non è la stessa cosa di zero.
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Persona</TableHead>
              <TableHead>Tariffa oraria (€/h)</TableHead>
              <TableHead>Costo orario (€/h)</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users.data ?? []).map((user) => (
              // Keyed on the id so a rename never remounts the row and discards a
              // half-typed rate.
              <UserRateRow key={user.id} user={user} onProblem={setProblem} />
            ))}
          </TableBody>
        </Table>
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">Tariffe dei deal</h2>
        <StandingNote />
        <p className="text-sm text-muted-foreground">
          La tariffa del deal vince su quella della persona: è la regola che permette di
          concordare un prezzo con un cliente senza toccare le tariffe di nessuno.
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Deal</TableHead>
              <TableHead>Tariffa oraria (€/h)</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {withRate.map((deal) => (
              <DealRateRow key={deal.id} deal={deal} onProblem={setProblem} />
            ))}
          </TableBody>
        </Table>
        {withRate.length === 0 && !deals.isLoading && (
          <p className="text-sm text-muted-foreground">
            Nessun deal ha una tariffa propria: si usa quella della persona.
          </p>
        )}

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="nuovo-deal-tariffa">Aggiungi una tariffa a un deal</Label>
            <Select value={nuovoDeal} onValueChange={setNuovoDeal}>
              <SelectTrigger id="nuovo-deal-tariffa" className="w-72">
                <SelectValue placeholder="Seleziona un deal…" />
              </SelectTrigger>
              <SelectContent>
                {withoutRate.map((deal) => (
                  <SelectItem key={deal.id} value={deal.id}>
                    {deal.nome}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="nuova-tariffa">Tariffa (€/h)</Label>
            <Input
              id="nuova-tariffa"
              type="number"
              step="0.000001"
              value={nuovaTariffa}
              onChange={(event) => setNuovaTariffa(event.target.value)}
              className="w-32"
            />
          </div>
          <Button
            disabled={nuovoDeal === '' || nuovaTariffa.trim() === '' || addRate.isPending}
            onClick={() => {
              setProblem(null)
              addRate.mutate(
                { tariffa_oraria: rateBody(nuovaTariffa) },
                {
                  onSuccess: () => {
                    toast.success('Tariffa del deal impostata')
                    setNuovoDeal('')
                    setNuovaTariffa('')
                  },
                  onError: (error) => setProblem(toProblem(error).detail),
                },
              )
            }}
          >
            Imposta
          </Button>
        </div>
      </section>
    </div>
  )
}
