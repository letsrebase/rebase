import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import type { AudiencePreview, CampaignTemplate } from '@/lib/api'
import { COMPANY_STATES, FREELANCER_LIST_STATES, REMOTO_LABELS, STATE_LABELS } from '@/lib/format'
import { AmountFilter, FilterField } from '../lists'
import {
  ANY,
  NONE,
  hasOtherFilters,
  type AudienceCount,
  type AziendeFiltriForm,
  type CampaignForm,
  type Fonte,
  type Lista,
  type TalentiFiltriForm,
} from './form'

const YES_NO: [string, string][] = [
  ['si', 'Sì'],
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

function TalentiFiltri({
  value,
  onChange,
  altri,
}: {
  value: TalentiFiltriForm
  onChange: (patch: Partial<TalentiFiltriForm>) => void
  altri: boolean
}) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectFilter
          id="campagna-talenti-stato"
          label="Stato"
          value={value.stato}
          options={STATE_OPTIONS(FREELANCER_LIST_STATES)}
          onChange={(stato) => onChange({ stato })}
        />
        <InputFilter id="campagna-talenti-q" label="Cerca" maxLength={200} value={value.q} onChange={(q) => onChange({ q })} />
        <SelectFilter id="campagna-talenti-cv" label="Ha un CV" value={value.has_cv} options={YES_NO} onChange={(has_cv) => onChange({ has_cv })} />
        <SelectFilter
          id="campagna-talenti-accessi"
          label="Ha fatto accesso"
          value={value.con_accessi}
          options={YES_NO}
          onChange={(con_accessi) => onChange({ con_accessi })}
        />
      </div>
      {altri && (
        <div className="grid gap-4 sm:grid-cols-2">
          <InputFilter
            id="campagna-talenti-posizione"
            label="Posizione"
            maxLength={160}
            value={value.posizione}
            onChange={(posizione) => onChange({ posizione })}
          />
          <SelectFilter
            id="campagna-talenti-remoto"
            label="Da remoto"
            value={value.remoto}
            options={Object.entries(REMOTO_LABELS)}
            onChange={(remoto) => onChange({ remoto })}
          />
          <EuroFilter
            id="campagna-talenti-tariffa-min"
            label="Tariffa min (€/giorno)"
            value={value.tariffa_min}
            onChange={(tariffa_min) => onChange({ tariffa_min })}
          />
          <EuroFilter
            id="campagna-talenti-tariffa-max"
            label="Tariffa max (€/giorno)"
            value={value.tariffa_max}
            onChange={(tariffa_max) => onChange({ tariffa_max })}
          />
          <InputFilter
            id="campagna-talenti-origine"
            label="Pagina di provenienza"
            maxLength={40}
            placeholder="home, pigrocrm…"
            value={value.origine}
            onChange={(origine) => onChange({ origine })}
          />
          <InputFilter
            id="campagna-talenti-utm-source"
            label="UTM source"
            maxLength={200}
            value={value.utm_source}
            onChange={(utm_source) => onChange({ utm_source })}
          />
          <InputFilter
            id="campagna-talenti-creato-da"
            label="Creato dal"
            kind="date"
            value={value.creato_da}
            onChange={(creato_da) => onChange({ creato_da })}
          />
          <InputFilter
            id="campagna-talenti-creato-a"
            label="Creato al"
            kind="date"
            value={value.creato_a}
            onChange={(creato_a) => onChange({ creato_a })}
          />
        </div>
      )}
    </>
  )
}

function AziendeFiltri({
  value,
  onChange,
  altri,
}: {
  value: AziendeFiltriForm
  onChange: (patch: Partial<AziendeFiltriForm>) => void
  altri: boolean
}) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectFilter
          id="campagna-aziende-stato"
          label="Stato"
          value={value.stato}
          options={STATE_OPTIONS(COMPANY_STATES)}
          onChange={(stato) => onChange({ stato })}
        />
        <InputFilter id="campagna-aziende-q" label="Cerca" maxLength={200} value={value.q} onChange={(q) => onChange({ q })} />
      </div>
      {altri && (
        <div className="grid gap-4 sm:grid-cols-2">
          <EuroFilter
            id="campagna-aziende-budget-min"
            label="Budget min (€/giorno)"
            value={value.budget_min}
            onChange={(budget_min) => onChange({ budget_min })}
          />
          <EuroFilter
            id="campagna-aziende-budget-max"
            label="Budget max (€/giorno)"
            value={value.budget_max}
            onChange={(budget_max) => onChange({ budget_max })}
          />
          <InputFilter
            id="campagna-aziende-periodo-da"
            label="Periodo dal"
            kind="date"
            value={value.periodo_da}
            onChange={(periodo_da) => onChange({ periodo_da })}
          />
          <InputFilter
            id="campagna-aziende-origine"
            label="Pagina di provenienza"
            maxLength={40}
            placeholder="home, pigrocrm…"
            value={value.origine}
            onChange={(origine) => onChange({ origine })}
          />
          <InputFilter
            id="campagna-aziende-creato-da"
            label="Creata dal"
            kind="date"
            value={value.creato_da}
            onChange={(creato_da) => onChange({ creato_da })}
          />
          <InputFilter
            id="campagna-aziende-creato-a"
            label="Creata al"
            kind="date"
            value={value.creato_a}
            onChange={(creato_a) => onChange({ creato_a })}
          />
        </div>
      )}
    </>
  )
}

function AudienceTable({
  audience,
  esclusi,
  onToggle,
}: {
  audience: AudiencePreview
  esclusi: readonly string[]
  onToggle: (email: string, included: boolean) => void
}) {
  return (
    <div className="border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Nome</TableHead>
            <TableHead>Indirizzo</TableHead>
            <TableHead>Includi</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {audience.righe.map((row) => {
            const forced = row.escluso !== null
            // A row the rules leave out is never «Includi»: unticked and disabled.
            const checked = !forced && !esclusi.includes(row.email)
            return (
              <TableRow key={row.email}>
                <TableCell>{row.nome ?? '—'}</TableCell>
                <TableCell>{row.email}</TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      aria-label={row.email}
                      checked={checked}
                      disabled={forced}
                      onCheckedChange={(value) => onToggle(row.email, value === true)}
                    />
                    {row.escluso && <span className="text-xs text-muted-foreground">{row.escluso}</span>}
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
  esclusi,
  onToggle,
}: {
  audience: AudiencePreview
  count: AudienceCount
  stale: boolean
  esclusi: readonly string[]
  onToggle: (email: string, included: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const parts = [
    count.regole > 0 ? `${count.regole} escluse dalle regole` : null,
    count.tolte > 0 ? `${count.tolte} tolte da te` : null,
  ].filter(Boolean)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-live="polite">
        <p className="text-sm">
          <span className="text-lg font-semibold">{count.riceveranno}</span>{' '}
          {count.riceveranno === 1 ? 'riceverà la mail' : 'riceveranno la mail'}
          {parts.length > 0 && <span className="text-muted-foreground"> · {parts.join(' · ')}</span>}
          {stale && <span className="text-muted-foreground"> · aggiorno l’elenco…</span>}
        </p>
        {audience.righe.length > 0 && (
          <Button type="button" variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Nascondi l’elenco' : `Mostra l’elenco (${audience.righe.length})`}
          </Button>
        )}
      </div>
      {open && <AudienceTable audience={audience} esclusi={esclusi} onToggle={onToggle} />}
    </div>
  )
}

/** Destinatari (REB-526): a journey state or the lists' own filters, and under them
 *  the list those give, as it stands after the last save. */
export function Destinatari({
  form,
  templates,
  onChange,
  onTemplate,
  audience,
  count,
  stale,
  audienceError,
  esclusi,
  onToggle,
}: {
  form: CampaignForm
  templates: CampaignTemplate[]
  onChange: (patch: Partial<CampaignForm>) => void
  onTemplate: (value: string) => void
  audience: AudiencePreview | undefined
  count: AudienceCount | null
  stale: boolean
  audienceError: string | null
  esclusi: readonly string[]
  onToggle: (email: string, included: boolean) => void
}) {
  // Open by itself when a stored filter lives in there (the edit route), so no filter
  // that shapes the list is ever out of sight.
  const [altri, setAltri] = useState(() => hasOtherFilters(form))
  return (
    <section aria-labelledby="campagna-destinatari" className="space-y-4">
      <h2 id="campagna-destinatari" className="text-lg font-semibold">
        Destinatari
      </h2>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Da chi parte la campagna">
        {(
          [
            ['stato', 'Uno stato del percorso'],
            ['filtri', 'Filtri'],
          ] as [Fonte, string][]
        ).map(([fonte, text]) => (
          <Button
            key={fonte}
            type="button"
            variant={form.fonte === fonte ? 'default' : 'outline'}
            aria-pressed={form.fonte === fonte}
            onClick={() => onChange({ fonte })}
          >
            {text}
          </Button>
        ))}
      </div>
      {form.fonte === 'stato' ? (
        <div className="max-w-sm space-y-1.5">
          <Label htmlFor="campagna-stato-percorso">Stato del percorso</Label>
          <Select value={form.statoPercorso ?? NONE} onValueChange={onTemplate}>
            <SelectTrigger id="campagna-stato-percorso" aria-label="Stato del percorso" className="w-full">
              <SelectValue placeholder="Scegli uno stato" />
            </SelectTrigger>
            <SelectContent>
              {templates.map((template) => (
                <SelectItem key={template.stato_percorso} value={template.stato_percorso}>
                  {template.etichetta}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="max-w-sm space-y-1.5">
            <Label htmlFor="campagna-lista">Lista</Label>
            <Select value={form.lista} onValueChange={(lista) => onChange({ lista: lista as Lista })}>
              <SelectTrigger id="campagna-lista" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="talenti">Talenti</SelectItem>
                <SelectItem value="aziende">Aziende</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {form.lista === 'talenti' ? (
            <TalentiFiltri value={form.talenti} altri={altri} onChange={(patch) => onChange({ talenti: { ...form.talenti, ...patch } })} />
          ) : (
            <AziendeFiltri value={form.aziende} altri={altri} onChange={(patch) => onChange({ aziende: { ...form.aziende, ...patch } })} />
          )}
          <Button type="button" variant="ghost" size="sm" aria-expanded={altri} onClick={() => setAltri(!altri)}>
            {altri ? 'Meno filtri' : 'Altri filtri'}
          </Button>
        </div>
      )}
      {audienceError ? (
        <p role="alert" className="text-sm text-destructive">
          {audienceError}
        </p>
      ) : audience && count ? (
        <AudienceSummary audience={audience} count={count} stale={stale} esclusi={esclusi} onToggle={onToggle} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {form.fonte === 'stato' && form.statoPercorso === null
            ? 'Scegli uno stato per vedere chi riceve la mail.'
            : 'Carico l’elenco…'}
        </p>
      )}
    </section>
  )
}
