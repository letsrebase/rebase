import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import type { AudiencePreview, CampaignTemplate } from '@/lib/api'
import { ANY } from '@/lib/adminList'
import { COMPANY_STATES, FREELANCER_LIST_STATES, REMOTE_LABELS, STATE_LABELS } from '@/lib/format'
import { AmountFilter, FilterField } from '../list-kit'
import {
  NONE,
  hasOtherFilters,
  type AudienceCount,
  type CompaniesFiltersForm,
  type CampaignForm,
  type Source,
  type List,
  type TalentsFiltersForm,
} from './form'

const YES_NO: [string, string][] = [
  ['yes', 'Sì'],
  ['no', 'No'],
]

function SelectFilter({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string
  label: string
  value: string
  options: [string, string][]
  onChange: (value: string) => void
}) {
  return (
    <FilterField label={label} htmlFor={id}>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>Tutti</SelectItem>
          {options.map(([option, text]) => (
            <SelectItem key={option} value={option}>
              {text}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FilterField>
  )
}

/** A text or date filter. */
function InputFilter({
  id,
  label,
  value,
  onChange,
  kind = 'text',
  maxLength,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  kind?: 'text' | 'date'
  maxLength?: number
  placeholder?: string
}) {
  return (
    <FilterField label={label} htmlFor={id}>
      <Input
        id={id}
        type={kind === 'date' ? 'date' : undefined}
        maxLength={maxLength}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </FilterField>
  )
}

/** A day rate or a budget in euro, read the Italian way as on the lists (REB-485); the
 *  form keeps what was typed, `''` when empty. */
function EuroFilter({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return <AmountFilter id={id} label={label} value={value || undefined} onChange={(typed) => onChange(typed ?? '')} />
}

const STATE_OPTIONS = (states: readonly string[]): [string, string][] =>
  states.map((state) => [state, STATE_LABELS[state] ?? state])

function TalentsFilters({
  value,
  onChange,
  other,
}: {
  value: TalentsFiltersForm
  onChange: (patch: Partial<TalentsFiltersForm>) => void
  other: boolean
}) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectFilter
          id="campaign-talents-status"
          label="Stato"
          value={value.status}
          options={STATE_OPTIONS(FREELANCER_LIST_STATES)}
          onChange={(status) => onChange({ status })}
        />
        <InputFilter id="campaign-talents-q" label="Cerca" maxLength={200} value={value.q} onChange={(q) => onChange({ q })} />
        <SelectFilter id="campaign-talents-cv" label="Ha un CV" value={value.has_cv} options={YES_NO} onChange={(has_cv) => onChange({ has_cv })} />
        <SelectFilter
          id="campaign-talents-logins"
          label="Ha fatto accesso"
          value={value.with_logins}
          options={YES_NO}
          onChange={(with_logins) => onChange({ with_logins })}
        />
      </div>
      {other && (
        <div className="grid gap-4 sm:grid-cols-2">
          <InputFilter
            id="campaign-talents-job-title"
            label="Posizione"
            maxLength={160}
            value={value.job_title}
            onChange={(job_title) => onChange({ job_title })}
          />
          <SelectFilter
            id="campaign-talents-remote"
            label="Da remoto"
            value={value.remote}
            options={Object.entries(REMOTE_LABELS)}
            onChange={(remote) => onChange({ remote })}
          />
          <EuroFilter
            id="campaign-talents-min-rate"
            label="Tariffa min (€/giorno)"
            value={value.min_rate}
            onChange={(min_rate) => onChange({ min_rate })}
          />
          <EuroFilter
            id="campaign-talents-max-rate"
            label="Tariffa max (€/giorno)"
            value={value.max_rate}
            onChange={(max_rate) => onChange({ max_rate })}
          />
          <InputFilter
            id="campaign-talents-origin"
            label="Pagina di provenienza"
            maxLength={40}
            placeholder="home, pigrocrm…"
            value={value.origin}
            onChange={(origin) => onChange({ origin })}
          />
          <InputFilter
            id="campaign-talents-utm-source"
            label="UTM source"
            maxLength={200}
            value={value.utm_source}
            onChange={(utm_source) => onChange({ utm_source })}
          />
          <InputFilter
            id="campaign-talents-created-from"
            label="Creato dal"
            kind="date"
            value={value.created_from}
            onChange={(created_from) => onChange({ created_from })}
          />
          <InputFilter
            id="campaign-talents-created-to"
            label="Creato al"
            kind="date"
            value={value.created_to}
            onChange={(created_to) => onChange({ created_to })}
          />
        </div>
      )}
    </>
  )
}

function CompaniesFilters({
  value,
  onChange,
  other,
}: {
  value: CompaniesFiltersForm
  onChange: (patch: Partial<CompaniesFiltersForm>) => void
  other: boolean
}) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectFilter
          id="campaign-companies-status"
          label="Stato"
          value={value.status}
          options={STATE_OPTIONS(COMPANY_STATES)}
          onChange={(status) => onChange({ status })}
        />
        <InputFilter id="campaign-companies-q" label="Cerca" maxLength={200} value={value.q} onChange={(q) => onChange({ q })} />
      </div>
      {other && (
        <div className="grid gap-4 sm:grid-cols-2">
          <EuroFilter
            id="campaign-companies-budget-min"
            label="Budget min (€/giorno)"
            value={value.budget_min}
            onChange={(budget_min) => onChange({ budget_min })}
          />
          <EuroFilter
            id="campaign-companies-budget-max"
            label="Budget max (€/giorno)"
            value={value.budget_max}
            onChange={(budget_max) => onChange({ budget_max })}
          />
          <InputFilter
            id="campaign-companies-period-from"
            label="Periodo dal"
            kind="date"
            value={value.period_from}
            onChange={(period_from) => onChange({ period_from })}
          />
          <InputFilter
            id="campaign-companies-origin"
            label="Pagina di provenienza"
            maxLength={40}
            placeholder="home, pigrocrm…"
            value={value.origin}
            onChange={(origin) => onChange({ origin })}
          />
          <InputFilter
            id="campaign-companies-created-from"
            label="Creata dal"
            kind="date"
            value={value.created_from}
            onChange={(created_from) => onChange({ created_from })}
          />
          <InputFilter
            id="campaign-companies-created-to"
            label="Creata al"
            kind="date"
            value={value.created_to}
            onChange={(created_to) => onChange({ created_to })}
          />
        </div>
      )}
    </>
  )
}

function AudienceTable({
  audience,
  excluded,
  onToggle,
}: {
  audience: AudiencePreview
  excluded: readonly string[]
  onToggle: (email: string, included: boolean) => void
}) {
  return (
    <div className="border border-border bg-card">
      <Table scrollLabel="Destinatari della campagna">
        <TableHeader>
          <TableRow>
            <TableHead>Nome</TableHead>
            <TableHead>Indirizzo</TableHead>
            <TableHead>Includi</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {audience.lines.map((row) => {
            const forced = row.excluded !== null
            // A row the rules leave out is never «Includi»: unticked and disabled.
            const checked = !forced && !excluded.includes(row.email)
            return (
              <TableRow key={row.email}>
                <TableCell>{row.name ?? '—'}</TableCell>
                <TableCell>{row.email}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      aria-label={row.email}
                      checked={checked}
                      disabled={forced}
                      onCheckedChange={(value) => onToggle(row.email, value === true)}
                    />
                    {row.excluded && <span className="text-xs text-muted-foreground">{row.excluded}</span>}
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

/** The one line that says who gets the mail, net of what the rules and the admin left
 *  out; the table behind it opens on request. */
function AudienceSummary({
  audience,
  count,
  stale,
  excluded,
  onToggle,
}: {
  audience: AudiencePreview
  count: AudienceCount
  stale: boolean
  excluded: readonly string[]
  onToggle: (email: string, included: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const parts = [
    count.rules > 0 ? `${count.rules} escluse dalle regole` : null,
    count.removed > 0 ? `${count.removed} tolte da te` : null,
  ].filter(Boolean)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-live="polite">
        <p className="text-sm">
          <span className="text-lg font-semibold">{count.will_receive}</span>{' '}
          {count.will_receive === 1 ? 'riceverà la mail' : 'riceveranno la mail'}
          {parts.length > 0 && <span className="text-muted-foreground"> · {parts.join(' · ')}</span>}
          {stale && <span className="text-muted-foreground"> · aggiorno l’elenco…</span>}
        </p>
        {audience.lines.length > 0 && (
          <Button type="button" variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Nascondi l’elenco' : `Mostra l’elenco (${audience.lines.length})`}
          </Button>
        )}
      </div>
      {open && <AudienceTable audience={audience} excluded={excluded} onToggle={onToggle} />}
    </div>
  )
}

/** Destinatari (REB-526): a journey state or the lists' own filters, and under them
 *  the list those give, as it stands after the last save. */
export function Recipients({
  form,
  templates,
  onChange,
  onTemplate,
  audience,
  count,
  stale,
  audienceError,
  excluded,
  onToggle,
  segue,
  segueError,
}: {
  form: CampaignForm
  templates: CampaignTemplate[]
  onChange: (patch: Partial<CampaignForm>) => void
  onTemplate: (value: string) => void
  audience: AudiencePreview | undefined
  count: AudienceCount | null
  stale: boolean
  audienceError: string | null
  excluded: readonly string[]
  onToggle: (email: string, included: boolean) => void
  /** The campaign a `list` draft follows (Task 6), by name, for the sentence and the
   *  link to it; `null` for a `status`/`filters` form, and while its own name has not
   *  loaded yet. */
  segue: { id: string; name: string } | null
  /** Set when the parent's own GET failed (item 4): the editor cannot say whose list
   *  this is or whether the follow-up is still possible, so it says that plainly
   *  instead of leaving «…» in the sentence with no explanation. */
  segueError: string | null
}) {
  // Open by itself when a stored filter lives in there (the edit route), so no filter
  // that shapes the list is ever out of sight.
  const [other, setOther] = useState(() => hasOtherFilters(form))
  return (
    <section aria-labelledby="campaign-recipients" className="space-y-4">
      <h2 id="campaign-recipients" className="text-lg font-semibold">
        Destinatari
      </h2>
      {form.source === 'list' ? (
        <>
          <p className="text-sm">
            Chi ha ricevuto «{segue?.name ?? '…'}» e non ha ancora fatto l’azione.{' '}
            {segue && (
              <Link to="/admin/campaigns/$id" params={{ id: segue.id }} className="underline">
                Vedi la campagna
              </Link>
            )}
          </p>
          {segueError && (
            <p role="alert" className="text-sm text-destructive">
              Non riesco a leggere la campagna da cui viene.
            </p>
          )}
        </>
      ) : (
        <>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Da chi parte la campagna">
            {(
              [
                ['status', 'Uno stato del percorso'],
                ['filters', 'Filtri'],
              ] as [Source, string][]
            ).map(([source, text]) => (
              <Button
                key={source}
                type="button"
                variant={form.source === source ? 'default' : 'outline'}
                aria-pressed={form.source === source}
                onClick={() => onChange({ source })}
              >
                {text}
              </Button>
            ))}
          </div>
          {form.source === 'status' ? (
            <div className="max-w-sm space-y-1.5">
              <Label htmlFor="campaign-journey-status">Stato del percorso</Label>
              <Select value={form.journeyStatus ?? NONE} onValueChange={onTemplate}>
                <SelectTrigger id="campaign-journey-status" aria-label="Stato del percorso" className="w-full">
                  <SelectValue placeholder="Scegli uno stato" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((template) => (
                    <SelectItem key={template.journey_status} value={template.journey_status}>
                      {template.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="max-w-sm space-y-1.5">
                <Label htmlFor="campaign-list">Lista</Label>
                <Select value={form.list} onValueChange={(list) => onChange({ list: list as List })}>
                  <SelectTrigger id="campaign-list" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="talents">Talenti</SelectItem>
                    <SelectItem value="companies">Aziende</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {form.list === 'talents' ? (
                <TalentsFilters value={form.talents} other={other} onChange={(patch) => onChange({ talents: { ...form.talents, ...patch } })} />
              ) : (
                <CompaniesFilters value={form.companies} other={other} onChange={(patch) => onChange({ companies: { ...form.companies, ...patch } })} />
              )}
              <Button type="button" variant="ghost" size="sm" aria-expanded={other} onClick={() => setOther(!other)}>
                {other ? 'Meno filtri' : 'Altri filtri'}
              </Button>
            </div>
          )}
        </>
      )}
      {audienceError ? (
        <p role="alert" className="text-sm text-destructive">
          {audienceError}
        </p>
      ) : audience && count ? (
        <AudienceSummary audience={audience} count={count} stale={stale} excluded={excluded} onToggle={onToggle} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {form.source === 'status' && form.journeyStatus === null
            ? 'Scegli uno stato per vedere chi riceve la mail.'
            : 'Carico l’elenco…'}
        </p>
      )}
    </section>
  )
}
