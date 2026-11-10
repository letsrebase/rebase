import { useState, type ReactNode } from 'react'
import { useMediaQuery } from '@rebase/ui/use-media-query'
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
 * row: a 422 naming `default_hourly_cost` put there is true and useless, because it
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

/** The frame of either rates table: the table with its header, or a column of cards on a phone. */
function RateTable({
  phone,
  label,
  heads,
  children,
}: {
  phone: boolean
  label: string
  heads: string[]
  children: ReactNode
}) {
  if (phone) {
    return (
      <ul aria-label={label} className="grid grid-cols-1 gap-3">
        {children}
      </ul>
    )
  }
  return (
    <Table scrollLabel={label}>
      <TableHeader>
        <TableRow>
          {heads.map((head) => (
            <TableHead key={head}>{head}</TableHead>
          ))}
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>{children}</TableBody>
    </Table>
  )
}

/**
 * One row of either rates table: a table row on a wide screen and, on a phone, a card with
 * each input under its label and «Salva» under them, because four columns of inputs scrolled
 * sideways off the screen and took «Salva» with them (REB-1057).
 */
function RateRow({
  phone,
  name,
  fields,
  action,
}: {
  phone: boolean
  name: string
  fields: { label: string; node: ReactNode }[]
  action: ReactNode
}) {
  if (phone) {
    return (
      <li className="space-y-3 border border-border bg-card p-4">
        <p className="break-words font-medium">{name}</p>
        {fields.map((field) => (
          <label key={field.label} className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">{field.label}</span>
            {field.node}
          </label>
        ))}
        {action}
      </li>
    )
  }
  return (
    <TableRow>
      <TableCell>{name}</TableCell>
      {fields.map((field) => (
        <TableCell key={field.label}>{field.node}</TableCell>
      ))}
      <TableCell>{action}</TableCell>
    </TableRow>
  )
}

/**
 * One row per user, its own component: `useSetUserRates(id)` is built from the row's id,
 * and calling it inside the parent's `.map` would be a variable number of hooks in a
 * variable order.
 */
function UserRateRow({
  user,
  onProblem,
  phone,
}: {
  user: UserRecord
  onProblem: OnProblem
  phone: boolean
}) {
  const [rate, setRate] = useState(user.default_hourly_rate ?? '')
  const [cost, setCost] = useState(user.default_hourly_cost ?? '')
  const save = useSetUserRates(user.id)
  const { fieldError, begin, fail } = useRowProblem(onProblem)

  return (
    <RateRow
      phone={phone}
      name={user.name}
      fields={[
        {
          label: 'Tariffa oraria (€/h)',
          node: (
            <>
              <Input
                type="number"
                step="0.000001"
                aria-label={`Tariffa oraria di ${user.name}`}
                aria-invalid={fieldError?.field === 'default_hourly_rate'}
                value={rate}
                onChange={(event) => setRate(event.target.value)}
                className="w-full sm:w-32"
              />
              {fieldError?.field === 'default_hourly_rate' && (
                <FieldMessage text={fieldError.message} />
              )}
            </>
          ),
        },
        {
          label: 'Costo orario (€/h)',
          node: (
            <>
              <Input
                type="number"
                step="0.000001"
                aria-label={`Costo orario di ${user.name}`}
                aria-invalid={fieldError?.field === 'default_hourly_cost'}
                value={cost}
                onChange={(event) => setCost(event.target.value)}
                className="w-full sm:w-32"
              />
              {fieldError?.field === 'default_hourly_cost' && (
                <FieldMessage text={fieldError.message} />
              )}
            </>
          ),
        },
      ]}
      action={
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
                default_hourly_rate: rateBody(rate),
                default_hourly_cost: rateBody(cost),
              },
              { onSuccess: () => toast.success('Tariffe aggiornate'), onError: fail },
            )
          }}
        >
          Salva
        </Button>
      }
    />
  )
}

function DealRateRow({
  deal,
  onProblem,
  phone,
}: {
  deal: Deal
  onProblem: OnProblem
  phone: boolean
}) {
  const [rate, setRate] = useState(deal.hourly_rate ?? '')
  const save = useSetDealRate(deal.id)
  const { fieldError, begin, fail } = useRowProblem(onProblem)

  return (
    <RateRow
      phone={phone}
      name={deal.name}
      fields={[
        {
          label: 'Tariffa oraria (€/h)',
          node: (
            <>
              <Input
                type="number"
                step="0.000001"
                aria-label={`Tariffa oraria del deal ${deal.name}`}
                aria-invalid={fieldError?.field === 'hourly_rate'}
                value={rate}
                onChange={(event) => setRate(event.target.value)}
                className="w-full sm:w-32"
              />
              {fieldError?.field === 'hourly_rate' && <FieldMessage text={fieldError.message} />}
            </>
          ),
        },
      ]}
      action={
        <Button
          size="sm"
          disabled={save.isPending}
          onClick={() => {
            begin()
            // The deal's own rate, on the deal's own endpoint: it overrides the
            // person's rate for hours on this deal and never writes to the person.
            save.mutate(
              { hourly_rate: rateBody(rate) },
              { onSuccess: () => toast.success('Tariffa del deal aggiornata'), onError: fail },
            )
          }}
        >
          Salva
        </Button>
      }
    />
  )
}

export function RatesPanel() {
  const users = useUsers()
  const deals = useDeals()
  const phone = useMediaQuery('(max-width: 767px)')
  const [problem, setProblem] = useState<string | null>(null)
  const [newDeal, setNewDeal] = useState('')
  const [newRate, setNewRate] = useState('')
  const addRate = useSetDealRate(newDeal)

  if (users.isError) return <QueryErrorBanner error={users.error} />
  if (deals.isError) return <QueryErrorBanner error={deals.error} />

  const allDeals = deals.data?.items ?? []
  // Split on the stored value, not on a truthiness test: `"0.000000"` is a real deal
  // rate meaning "these hours are not billed", and it belongs in the table, not in the
  // "no rate yet" picker.
  const withRate = allDeals.filter((deal) => deal.hourly_rate !== null)
  const withoutRate = allDeals.filter((deal) => deal.hourly_rate === null)

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
        <RateTable
          phone={phone}
          label="Tariffe delle persone"
          heads={['Persona', 'Tariffa oraria (€/h)', 'Costo orario (€/h)']}
        >
          {(users.data ?? []).map((user) => (
            // Keyed on the id so a rename never remounts the row and discards a
            // half-typed rate.
            <UserRateRow key={user.id} user={user} onProblem={setProblem} phone={phone} />
          ))}
        </RateTable>
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">Tariffe dei deal</h2>
        <StandingNote />
        <p className="text-sm text-muted-foreground">
          La tariffa del deal vince su quella della persona: è la regola che permette di
          concordare un prezzo con un cliente senza toccare le tariffe di nessuno.
        </p>
        <RateTable phone={phone} label="Tariffe dei deal" heads={['Deal', 'Tariffa oraria (€/h)']}>
          {withRate.map((deal) => (
            <DealRateRow key={deal.id} deal={deal} onProblem={setProblem} phone={phone} />
          ))}
        </RateTable>
        {withRate.length === 0 && !deals.isLoading && (
          <p className="text-sm text-muted-foreground">
            Nessun deal ha una tariffa propria: si usa quella della persona.
          </p>
        )}

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="new-deal-rate">Aggiungi una tariffa a un deal</Label>
            <Select value={newDeal} onValueChange={setNewDeal}>
              <SelectTrigger id="new-deal-rate" className="w-72">
                <SelectValue placeholder="Seleziona un deal…" />
              </SelectTrigger>
              <SelectContent>
                {withoutRate.map((deal) => (
                  <SelectItem key={deal.id} value={deal.id}>
                    {deal.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-rate">Tariffa (€/h)</Label>
            <Input
              id="new-rate"
              type="number"
              step="0.000001"
              value={newRate}
              onChange={(event) => setNewRate(event.target.value)}
              className="w-32"
            />
          </div>
          <Button
            disabled={newDeal === '' || newRate.trim() === '' || addRate.isPending}
            onClick={() => {
              setProblem(null)
              addRate.mutate(
                { hourly_rate: rateBody(newRate) },
                {
                  onSuccess: () => {
                    toast.success('Tariffa del deal impostata')
                    setNewDeal('')
                    setNewRate('')
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
