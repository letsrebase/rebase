import { useId, useMemo, useState, type CSSProperties } from 'react'
import { ChevronDown } from 'lucide-react'
import { FilterChip } from '@/components/FilterRow'
import { LoadingState } from '@/components/LoadingState'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { FormGrid, FormRow } from '@/features/purchases/PurchaseFormLayout'
import { useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import {
  EXPORT_KIND_LABELS,
  PAYROLL_PART_LABELS,
  partsWord,
  type BuildTarget,
  type ExportKind,
} from './labels'
import {
  KINDS,
  KIND_LABELS,
  defaultPeriod,
  periodsOf,
  rangeSentence,
  type PeriodKind,
} from './periods'
import { Readiness } from './Readiness'
import { useExportChecklist, useExportParts, useIvaFrequency } from './queries'

/** The value of «Tutte le aziende» in the company select: Radix refuses an empty string and no
 *  company id is this word. */
const ALL = 'all'

const PHONE = '(max-width: 639px)'

/**
 * «Prepara»: the dense form (the approved brief of 2026-11-10). The company, the period and the
 * parts the server says apply, read-only; one strong button at the foot, which only asks for the
 * build: delivering it is done from the stored export's panel.
 *
 * Nothing here knows a fiscal rule or a module: the parts come from `GET /parts`, which answers
 * for the company's own modules and pack, and an empty answer is simply a shorter list.
 */
export function PrepareTab({ onCreate }: { onCreate: (target: BuildTarget) => void }) {
  const entities = useLegalEntity()
  const { legal_entities, several, scoped, pinned, loaded, selected } = entities
  const phone = useMediaQuery(PHONE)
  const mayBuild = useCan('build_accountant_export')
  const listId = useId()
  const [sent, setSent] = useState(false)
  const [companyChoice, setCompanyChoice] = useState<string | null>(null)
  const [exportKind, setExportKind] = useState<ExportKind>('accountant')
  const [kindChoice, setKindChoice] = useState<PeriodKind | null>(null)
  const [payrollChoice, setPayrollChoice] = useState<readonly string[]>([])
  const [periodChoice, setPeriodChoice] = useState<string | null>(null)
  const [partsOpen, setPartsOpen] = useState(false)
  // Read once: a form open across midnight keeps the periods it was drawn with.
  const [now] = useState(() => new Date())

  // «Tutte le aziende» is a separate option and never the preselected one; a scoped admin has no
  // such option, since an export of every company is an unscoped admin's.
  const mayChooseAll = several && !scoped
  // «Variazioni paghe» is one company's file: «Tutte le aziende» is not offered for it.
  const payrollChanges = exportKind === 'payroll_changes'
  const allChosen = mayChooseAll && !payrollChanges && companyChoice === ALL
  const chosenId =
    companyChoice !== null && legal_entities.some((entity) => entity.id === companyChoice)
      ? companyChoice
      : null
  const company = allChosen ? null : (chosenId ?? selected ?? legal_entities[0]?.id ?? null)

  const frequency = useIvaFrequency(company)
  // The period the form opens on follows the company's IVA frequency, which is read first: a press
  // before it arrives would build the month the form is about to leave.
  const frequencyPending = company !== null && frequency.isPending
  // «Variazioni paghe» is the payroll consultant's monthly file: a month, whatever the IVA says.
  const kind: PeriodKind = payrollChanges
    ? 'month'
    : (kindChoice ?? (frequency.data === 'quarterly' ? 'quarter' : 'month'))
  const options = useMemo(() => periodsOf(kind, now), [kind, now])
  const period = options.find((option) => option.value === periodChoice) ?? defaultPeriod(options)

  const parts = useExportParts(company, allChosen, loaded, exportKind)
  // The payroll consultant's type exists only where there is staff: the server lists its parts
  // for a company whose module and pack allow them, and the module is the space's answer.
  const modules = useModules()
  const mayChooseKind = modules.on('staff')
  // The parts every build holds, and apart from them the ones an admin ticks (the payslips): the
  // page draws what the server lists and holds no name of its own.
  const standing = parts.data?.filter((part) => part.opt_in === null) ?? []
  const asked = (parts.data ?? []).filter((part) => part.opt_in !== null && !payrollChanges)
  const payrollParts = asked.flatMap((part) =>
    part.opt_in !== null && payrollChoice.includes(part.opt_in) ? [part.opt_in] : [],
  )
  // The checklist of the period as the books stand now. It follows the company, the period and
  // the pay parts, and a «variazioni paghe» export has none (brief, answer 6).
  const checklist = useExportChecklist(
    loaded && period !== undefined && !payrollChanges && !frequencyPending
      ? {
          date_from: period.from,
          date_to: period.to,
          legal_entity_id: allChosen ? null : company,
          all_entities: allChosen,
          payroll_parts: payrollParts,
        }
      : null,
  )
  const companyName = allChosen
    ? 'Tutte le aziende'
    : (legal_entities.find((entity) => entity.id === company)?.name ?? '')

  function submit() {
    if (period === undefined || sent) return
    setSent(true)
    onCreate({
      request: {
        date_from: period.from,
        date_to: period.to,
        legal_entity_id: allChosen ? null : company,
        all_entities: allChosen,
        kind: exportKind,
        payroll_parts: payrollParts,
      },
      company: companyName,
      period: period.name,
    })
  }

  // A role that may not build is not given a form: the page answers with the server's own sentence
  // (an administrator's page reached on a stale link), which the parts read already carries.
  if (!mayBuild) {
    return (
      <div className="max-w-3xl px-8 pb-6 pt-6">
        {parts.isError ? (
          <QueryErrorBanner error={parts.error} />
        ) : (
          <LoadingState label="Controllo il periodo…" className="py-8" />
        )}
      </div>
    )
  }

  const partsList = parts.isError ? (
    <QueryErrorBanner error={parts.error} onRetry={() => void parts.refetch()} />
  ) : parts.isPending ? (
    <LoadingState label="Controllo il periodo…" className="py-1" />
  ) : (
    <ul id={listId} aria-label="Cosa contiene" className="divide-y border">
      {standing.map((part) => (
        <li key={part.key} className="px-3 py-2 text-sm">
          <p className="font-medium">{part.label}</p>
          <p className="text-xs text-muted-foreground">
            {part.contents} · {part.date_rule}
          </p>
        </li>
      ))}
    </ul>
  )

  return (
    <form
      aria-label="Prepara l’esportazione"
      className="max-w-3xl space-y-4 px-8 pb-6 pt-6"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      {!loaded ? (
        <LoadingState label="Leggo le aziende…" className="py-8" />
      ) : (
        <div className="space-y-4 border bg-card p-4 sm:p-5">
          <FormGrid>
            {mayChooseKind ? (
              <FormRow label="Tipo" wide>
                <div role="group" aria-label="Tipo di esportazione" className="flex flex-wrap items-center gap-2">
                  {(['accountant', 'payroll_changes'] as const).map((candidate) => (
                    <FilterChip
                      key={candidate}
                      pressed={exportKind === candidate}
                      onPress={() => {
                        setExportKind(candidate)
                        setPeriodChoice(null)
                      }}
                    >
                      {EXPORT_KIND_LABELS[candidate]}
                    </FilterChip>
                  ))}
                </div>
                {payrollChanges ? (
                  <p className="text-sm text-muted-foreground">
                    Il file per chi fa le paghe: assunzioni, cessazioni, variazioni di retribuzione
                    e note spese da rimborsare in busta paga.
                  </p>
                ) : null}
              </FormRow>
            ) : null}
            {pinned ? (
              <FormRow label="Azienda" wide>
                <p className="flex min-h-9 items-center text-sm font-medium">{companyName}</p>
              </FormRow>
            ) : several ? (
              <FormRow label="Azienda" htmlFor="export-company" wide>
                <Select
                  value={allChosen ? ALL : (company ?? undefined)}
                  onValueChange={(next) => setCompanyChoice(next)}
                >
                  <SelectTrigger id="export-company" className="w-full sm:w-80">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {legal_entities.map((entity) => (
                      <SelectItem key={entity.id} value={entity.id}>
                        {entity.name}
                      </SelectItem>
                    ))}
                    {mayChooseAll && !payrollChanges ? <SelectSeparator /> : null}
                    {mayChooseAll && !payrollChanges ? (
                      <SelectItem value={ALL}>Tutte le aziende</SelectItem>
                    ) : null}
                  </SelectContent>
                </Select>
                {allChosen ? (
                  <p
                    role="note"
                    className="border bg-background px-3 py-2 text-sm text-muted-foreground"
                  >
                    Comprende anche gli acquisti che non hanno un’azienda.
                  </p>
                ) : null}
              </FormRow>
            ) : null}

            <FormRow label="Periodo" htmlFor="export-period" wide>
              <div className="flex flex-wrap items-center gap-2">
                <div role="group" aria-label="Tipo di periodo" className="flex items-center gap-2">
                  {(payrollChanges ? (['month'] as const) : KINDS).map((candidate) => (
                    <FilterChip
                      key={candidate}
                      pressed={kind === candidate}
                      onPress={() => {
                        setKindChoice(candidate)
                        setPeriodChoice(null)
                      }}
                    >
                      {KIND_LABELS[candidate]}
                    </FilterChip>
                  ))}
                </div>
                <Select
                  value={period?.value}
                  onValueChange={(next) => setPeriodChoice(next)}
                >
                  <SelectTrigger id="export-period" className="w-full sm:w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {options.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {period === undefined ? null : (
                <p className="text-sm text-muted-foreground">
                  {rangeSentence(period.from, period.to)}
                </p>
              )}
            </FormRow>

            <FormRow label="Cosa contiene" wide>
              {phone && parts.data !== undefined ? (
                <>
                  <button
                    type="button"
                    aria-expanded={partsOpen}
                    aria-controls={listId}
                    onClick={() => setPartsOpen((open) => !open)}
                    className="flex min-h-12 w-full items-center justify-between gap-2 border bg-background px-3 text-left text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {partsWord(standing.length)}
                    <ChevronDown
                      className={cn('size-4 transition-transform', partsOpen && 'rotate-180')}
                      aria-hidden
                    />
                  </button>
                  {partsOpen ? partsList : null}
                </>
              ) : (
                partsList
              )}
            </FormRow>

            {asked.length > 0 ? (
              <FormRow label="Parti paghe" wide>
                <div className="space-y-2">
                  {asked.map((part) => {
                    const name = part.opt_in ?? part.key
                    const id = `${listId}-pay-${name}`
                    return (
                      <div key={part.key} className="flex min-h-9 items-center gap-2">
                        <Checkbox
                          id={id}
                          checked={payrollChoice.includes(name)}
                          onCheckedChange={(next) =>
                            setPayrollChoice((previous) =>
                              next === true
                                ? [...previous.filter((other) => other !== name), name]
                                : previous.filter((other) => other !== name),
                            )
                          }
                        />
                        <Label htmlFor={id} className="text-sm">
                          {PAYROLL_PART_LABELS[name] ?? part.label}
                        </Label>
                      </div>
                    )
                  })}
                  <p className="text-sm text-muted-foreground">
                    Le paghe non viaggiano mai su un link semplice: si consegnano su Drive o con un
                    link che chiede un codice.
                  </p>
                </div>
              </FormRow>
            ) : null}

            {payrollChanges ? null : (
              <FormRow label="Il periodo è pronto?" wide>
                {checklist.isError ? (
                  <QueryErrorBanner error={checklist.error} onRetry={() => void checklist.refetch()} />
                ) : checklist.isPending ? (
                  <LoadingState label="Controllo il periodo…" className="py-1" />
                ) : (
                  <Readiness checks={checklist.data.checks} fix label="Il periodo è pronto?" />
                )}
              </FormRow>
            )}
          </FormGrid>
        </div>
      )}

      {/* The action bar, bottom right, and sticky above the phone's bar so «Crea esportazione» is
          never covered. */}
      <div
        style={
          { '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties
        }
        className="sticky bottom-(--phone-bar) z-10 flex flex-col gap-2 border-t bg-card py-3 sm:bottom-0 sm:flex-row sm:items-center sm:justify-end sm:gap-4"
      >
        {checklist.data !== undefined && checklist.data.open_count > 0 && !payrollChanges ? (
          <p className="text-sm text-muted-foreground">
            {checklist.data.open_count === 1
              ? 'C’è 1 punto da vedere.'
              : `Ci sono ${checklist.data.open_count} punti da vedere.`}{' '}
            Non fermano l’esportazione: restano scritti nell’indice.
          </p>
        ) : null}
        <Button type="submit" disabled={!loaded || frequencyPending || period === undefined || sent} className="w-full sm:w-auto">
          Crea esportazione
        </Button>
      </div>
    </form>
  )
}
