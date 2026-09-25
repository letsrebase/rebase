import { useInfiniteQuery } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { admin, type Company, type FiscalData } from '@/lib/api'
import {
  clienteLine,
  fiscalLine,
  type ClienteForm,
  type Failure,
  type FiscalDraft,
} from '@/lib/contracts'
import { formatDate } from '@/lib/format'
import { FiscalFields } from '../Contratti'
import { StepFooter } from './StepFooter'

const SEARCH_DEBOUNCE_MS = 300
const COMPANIES_PAGE = 50

function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function CompanyList({ selected, onSelect }: { selected: Company | null; onSelect: (company: Company) => void }) {
  const [q, setQ] = useState('')
  const term = useDebounce(q.trim(), SEARCH_DEBOUNCE_MS)
  // A page at a time, through the same cursor «Aziende» walks: an older request past the
  // first page stays one «Mostra altre» away (Greptile 4092036042).
  const companies = useInfiniteQuery({
    queryKey: ['companies', 'match', term],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.companies({ q: term || undefined, limit: COMPANIES_PAGE, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = companies.data?.pages.flatMap((page) => page.items) ?? []
  let list: ReactNode
  if (companies.isError) list = <p className="text-sm text-destructive">Non riesco a leggere le richieste.</p>
  else if (companies.isPending) list = <p className="text-sm text-muted-foreground">Caricamento…</p>
  else if (items.length === 0) list = <p className="text-sm text-muted-foreground">Nessuna richiesta trovata.</p>
  else
    list = (
      <ul className="space-y-2">
        {items.map((item) => {
          const closed = item.stato === 'chiuso'
          const chosen = selected?.id === item.id
          return (
            <li key={item.id}>
              <Button
                type="button"
                variant={chosen ? 'default' : 'outline'}
                aria-pressed={chosen}
                disabled={closed}
                onClick={() => onSelect(item)}
                className={cn('h-auto w-full justify-start whitespace-normal py-2 text-left', closed && 'opacity-50')}
              >
                <span className="flex flex-col items-start gap-0.5">
                  <span className="font-medium">
                    {item.nome_azienda}
                    {closed && ' · chiusa'}
                  </span>
                  <span className="text-xs">
                    {item.referente} · {item.figura_richiesta} · dal {formatDate(item.periodo_da)}
                  </span>
                </span>
              </Button>
            </li>
          )
        })}
      </ul>
    )
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="match-azienda-q">Cerca una richiesta</Label>
        <Input
          id="match-azienda-q"
          type="search"
          maxLength={200}
          placeholder="Azienda, referente, email…"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>
      {list}
      {companies.hasNextPage && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void companies.fetchNextPage()}
          disabled={companies.isFetchingNextPage}
        >
          {companies.isFetchingNextPage ? 'Caricamento…' : 'Mostra altre'}
        </Button>
      )}
    </>
  )
}

/** One line and «Modifica», or the fields: what the hub already knows is shown, not asked. */
function Summary({ line, edit, onEdit }: { line: string; edit: string; onEdit: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="text-sm">{line}</p>
      <Button type="button" variant="outline" size="sm" aria-label={edit} onClick={onEdit}>
        Modifica
      </Button>
    </div>
  )
}

function ClienteSection({
  form,
  onChange,
  editing,
  onEdit,
}: {
  form: ClienteForm
  onChange: (form: ClienteForm) => void
  editing: boolean
  onEdit: () => void
}) {
  const field = (name: keyof ClienteForm, label: string, maxLength: number) => (
    <div className="space-y-1.5">
      <Label htmlFor={`match-${name}`}>{label}</Label>
      <Input
        id={`match-${name}`}
        required
        maxLength={maxLength}
        value={form[name]}
        onChange={(event) => onChange({ ...form, [name]: event.target.value })}
      />
    </div>
  )
  return (
    <section aria-labelledby="match-cliente" className="space-y-2">
      <h2 id="match-cliente" className="text-sm font-medium">
        Cliente sulla lettera
      </h2>
      {editing ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {field('cliente_ragione_sociale', 'Ragione sociale del cliente', 200)}
          {field('cliente_piva', 'Partita IVA del cliente', 32)}
          {field('cliente_sede', 'Sede del cliente', 300)}
        </div>
      ) : (
        <Summary line={clienteLine(form)} edit="Modifica il cliente" onEdit={onEdit} />
      )}
    </section>
  )
}

function FiscaleSection({
  nome,
  saved,
  draft,
  onChange,
  editing,
  onEdit,
  failure,
}: {
  nome: string
  saved: FiscalData | null
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  editing: boolean
  onEdit: () => void
  failure: Failure | null
}) {
  return (
    <section aria-labelledby="match-fiscale" className="space-y-2">
      <h2 id="match-fiscale" className="text-sm font-medium">
        {nome ? `Dati fiscali di ${nome}` : 'Dati fiscali del freelance'}
      </h2>
      {saved && !editing ? (
        <Summary line={fiscalLine(saved)} edit="Modifica i dati fiscali" onEdit={onEdit} />
      ) : (
        <>
          {!saved && <p className="text-sm text-muted-foreground">Mancano: servono per il contratto.</p>}
          <FiscalFields
            idPrefix="match"
            draft={draft}
            onChange={onChange}
            wrong={(field) => failure?.fields.includes(field) || undefined}
          />
        </>
      )}
    </section>
  )
}

/** Step 1: the request, then the client the letter names and the freelancer's tax data,
 *  both read from the prefill and asked only when missing or opened. */
export function ChiStep({
  nome,
  selected,
  onSelect,
  loaded,
  cliente,
  onCliente,
  editCliente,
  onEditCliente,
  savedFiscal,
  fiscal,
  onFiscal,
  editFiscal,
  onEditFiscal,
  onNext,
  pending,
  failure,
}: {
  nome: string
  selected: Company | null
  onSelect: (company: Company) => void
  /** The prefill of `selected` is in the forms below. */
  loaded: boolean
  cliente: ClienteForm
  onCliente: (form: ClienteForm) => void
  editCliente: boolean
  onEditCliente: () => void
  savedFiscal: FiscalData | null
  fiscal: FiscalDraft
  onFiscal: (draft: FiscalDraft) => void
  editFiscal: boolean
  onEditFiscal: () => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      <CompanyList selected={selected} onSelect={onSelect} />
      {selected && loaded && (
        <div className="space-y-6 border-t pt-4">
          <ClienteSection form={cliente} onChange={onCliente} editing={editCliente} onEdit={onEditCliente} />
          <FiscaleSection
            nome={nome}
            saved={savedFiscal}
            draft={fiscal}
            onChange={onFiscal}
            editing={editFiscal}
            onEdit={onEditFiscal}
            failure={failure}
          />
        </div>
      )}
      {selected && !loaded && !failure && <p className="text-sm text-muted-foreground">Leggo la richiesta…</p>}
      <StepFooter next="Avanti" pending={pending} ready={selected !== null && loaded} failure={failure} />
    </form>
  )
}
