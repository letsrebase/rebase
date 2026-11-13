import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { SEARCH_DEBOUNCE_MS, pageItems, useCursorList, useDebounce } from '@/lib/adminList'
import { admin, type Company, type FiscalData, type Regime } from '@/lib/api'
import {
  REGIME_OPTIONS,
  customerLine,
  fiscalLine,
  requestLine,
  type CustomerForm,
  type Failure,
  type FiscalDraft,
} from '@/lib/contracts'
import { formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { FiscalFields } from '../contracts/Fiscal'
import { LoadingState } from '../list-kit'
import { StepFooter } from './StepFooter'

const COMPANIES_PAGE = 50

/** «Modifica» and «Cambia richiesta» take themselves off the page when clicked: the
 *  focus goes to what replaced them, never back to the top of the page. */
function useFocusWhenOpened(open: boolean) {
  const fields = useRef<HTMLDivElement>(null)
  const opening = useRef(false)
  useEffect(() => {
    if (!open || !opening.current) return
    opening.current = false
    fields.current?.querySelector<HTMLElement>('input, textarea')?.focus()
  }, [open])
  return {
    fields,
    markOpening: () => {
      opening.current = true
    },
  }
}

export function CompanyList({
  selected,
  onSelect,
  searchRef,
}: {
  selected: Company | null
  onSelect: (company: Company) => void
  searchRef: RefObject<HTMLInputElement | null>
}) {
  const [q, setQ] = useState('')
  const term = useDebounce(q.trim(), SEARCH_DEBOUNCE_MS)
  // A page at a time, through the same cursor «Aziende» walks: an older request past the
  // first page stays one «Mostra altre» away (Greptile 4092036042).
  const { list: companies, items } = useCursorList({
    queryKey: keys.companiesForMatch(term),
    fetchPage: (cursor) => admin.companies({ q: term || undefined, limit: COMPANIES_PAGE, cursor }),
    itemsOf: pageItems,
  })
  let list: ReactNode
  if (companies.isError) list = <p className="text-sm text-destructive">Non riesco a leggere le richieste.</p>
  else if (companies.isPending) list = <LoadingState label="Caricamento…" className="justify-start p-0" />
  else if (items.length === 0) list = <p className="text-sm text-muted-foreground">Nessuna richiesta trovata.</p>
  else
    list = (
      <ul className="space-y-2">
        {items.map((item) => {
          const closed = item.status === 'closed'
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
                    {item.company_name}
                    {closed && ' · chiusa'}
                  </span>
                  <span className="text-xs">
                    {item.contact_person} · {item.requested_role} · dal {formatDate(item.period_from)}
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
        <Label htmlFor="match-company-q">Cerca una richiesta</Label>
        <Input
          ref={searchRef}
          id="match-company-q"
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
          {companies.isFetchingNextPage ? (
            <>
              <Loader className="mr-2 size-4" />
              Caricamento…
            </>
          ) : (
            'Mostra altre'
          )}
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

function ChosenRequest({
  company,
  onChange,
  buttonRef,
}: {
  company: Company
  onChange: () => void
  buttonRef: RefObject<HTMLButtonElement | null>
}) {
  return (
    <section aria-labelledby="match-request" className="space-y-2">
      <h2 id="match-request" className="text-sm font-medium">
        Richiesta
      </h2>
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm">{requestLine(company)}</p>
        <Button ref={buttonRef} type="button" variant="outline" size="sm" onClick={onChange}>
          Cambia richiesta
        </Button>
      </div>
    </section>
  )
}

function CustomerSection({
  form,
  onChange,
  editing,
  onEdit,
  failure,
}: {
  form: CustomerForm
  onChange: (form: CustomerForm) => void
  editing: boolean
  onEdit: () => void
  failure: Failure | null
}) {
  const { fields, markOpening } = useFocusWhenOpened(editing)
  const field = (name: keyof CustomerForm, label: string, maxLength: number) => (
    <div className="space-y-1.5">
      <Label htmlFor={`match-${name}`}>{label}</Label>
      <Input
        id={`match-${name}`}
        required
        maxLength={maxLength}
        value={form[name]}
        onChange={(event) => onChange({ ...form, [name]: event.target.value })}
        aria-invalid={failure?.fields.includes(name) || undefined}
      />
    </div>
  )
  return (
    <section aria-labelledby="match-customer" className="space-y-2">
      <h2 id="match-customer" className="text-sm font-medium">
        Cliente sulla lettera
      </h2>
      {editing ? (
        <div ref={fields} className="grid gap-3 sm:grid-cols-2">
          {field('customer_legal_name', 'Ragione sociale del cliente', 200)}
          {field('customer_vat_number', 'Partita IVA del cliente', 32)}
          {field('customer_registered_office', 'Sede del cliente', 300)}
        </div>
      ) : (
        <Summary
          line={customerLine(form)}
          edit="Modifica il cliente"
          onEdit={() => {
            markOpening()
            onEdit()
          }}
        />
      )}
    </section>
  )
}

function TaxSection({
  firstName,
  saved,
  draft,
  onChange,
  editing,
  onEdit,
  failure,
}: {
  firstName: string
  saved: FiscalData | null
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  editing: boolean
  onEdit: () => void
  failure: Failure | null
}) {
  const open = !saved || editing
  const { fields, markOpening } = useFocusWhenOpened(open)
  return (
    <section aria-labelledby="match-tax" className="space-y-2">
      <h2 id="match-tax" className="text-sm font-medium">
        {firstName ? `Dati fiscali di ${firstName}` : 'Dati fiscali del freelance'}
      </h2>
      {saved && !editing ? (
        <Summary
          line={fiscalLine(saved)}
          edit="Modifica i dati fiscali"
          onEdit={() => {
            markOpening()
            onEdit()
          }}
        />
      ) : (
        <div ref={fields} className="space-y-2">
          {!saved && <p className="text-sm text-muted-foreground">Mancano: servono per il contratto.</p>}
          <FiscalFields
            idPrefix="match"
            draft={draft}
            onChange={onChange}
            wrong={(field) => failure?.fields.includes(field) || undefined}
          />
        </div>
      )}
    </section>
  )
}

/** «Tipo di lettera» (REB-687), under the tax data: the letter's type drives which
 *  framework agreement the check looks for, step 3's sentences and the PDFs. A refusal
 *  on `regime` (an ordinary letter without a partita IVA) marks both options. */
function KindLetter({
  regime,
  onChange,
  failure,
}: {
  regime: Regime
  onChange: (regime: Regime) => void
  failure: Failure | null
}) {
  const wrong = failure?.fields.includes('regime') || undefined
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Tipo di lettera</legend>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {REGIME_OPTIONS.map((option) => (
          <Label key={option.regime} className="font-normal">
            <input
              type="radio"
              name="match-regime"
              value={option.regime}
              checked={regime === option.regime}
              onChange={() => onChange(option.regime)}
              aria-invalid={wrong}
              className="size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {option.label}
          </Label>
        ))}
      </div>
    </fieldset>
  )
}

/** Step 1: the request, folded into one line once picked, then the client the letter
 *  names and the freelancer's tax data, both read from the prefill and asked only when
 *  missing or opened, and the letter's type. */
export function WhoStep({
  firstName,
  selected,
  onSelect,
  loaded,
  pickAgain,
  customer,
  onCustomer,
  editCustomer,
  onEditCustomer,
  savedFiscal,
  fiscal,
  onFiscal,
  editFiscal,
  onEditFiscal,
  regime,
  onRegime,
  onNext,
  pending,
  failure,
}: {
  firstName: string
  selected: Company | null
  onSelect: (company: Company) => void
  /** The prefill of `selected` is in the forms below. */
  loaded: boolean
  /** The list comes back to pick a request again: the prefill of `selected` could not
   *  be read, or the check found the request closed meanwhile. */
  pickAgain: boolean
  customer: CustomerForm
  onCustomer: (form: CustomerForm) => void
  editCustomer: boolean
  onEditCustomer: () => void
  savedFiscal: FiscalData | null
  fiscal: FiscalDraft
  onFiscal: (draft: FiscalDraft) => void
  editFiscal: boolean
  onEditFiscal: () => void
  regime: Regime
  onRegime: (regime: Regime) => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  const [choosing, setChoosing] = useState(selected === null)
  const listOpen = choosing || selected === null || pickAgain
  const changeButton = useRef<HTMLButtonElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const focusNext = useRef<RefObject<HTMLElement | null> | null>(null)
  useEffect(() => {
    const target = focusNext.current ?? (listOpen && pickAgain ? search : null)
    focusNext.current = null
    target?.current?.focus()
  }, [listOpen, pickAgain])
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      {listOpen || !selected ? (
        <CompanyList
          selected={selected}
          searchRef={search}
          onSelect={(company) => {
            focusNext.current = changeButton
            setChoosing(false)
            onSelect(company)
          }}
        />
      ) : (
        <ChosenRequest
          company={selected}
          buttonRef={changeButton}
          onChange={() => {
            focusNext.current = search
            setChoosing(true)
          }}
        />
      )}
      {!listOpen && selected && loaded && (
        <div className="space-y-6 border-t pt-4">
          <CustomerSection
            form={customer}
            onChange={onCustomer}
            editing={editCustomer}
            onEdit={onEditCustomer}
            failure={failure}
          />
          <TaxSection
            firstName={firstName}
            saved={savedFiscal}
            draft={fiscal}
            onChange={onFiscal}
            editing={editFiscal}
            onEdit={onEditFiscal}
            failure={failure}
          />
          <KindLetter regime={regime} onChange={onRegime} failure={failure} />
        </div>
      )}
      {!listOpen && selected && !loaded && <p className="text-sm text-muted-foreground">Leggo la richiesta…</p>}
      <StepFooter next="Avanti" pending={pending} ready={!listOpen && loaded} failure={failure} />
    </form>
  )
}
