import { useId, useMemo, useState, type CSSProperties } from 'react'
import { ChevronDown } from 'lucide-react'
import { FilterChip } from '@/components/FilterRow'
import { LoadingState } from '@/components/LoadingState'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { FormGrid, FormRow } from '@/features/purchases/PurchaseFormLayout'
import { useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { partsWord, type BuildTarget } from './labels'
import {
  KINDS,
  KIND_LABELS,
  defaultPeriod,
  periodsOf,
  rangeSentence,
  type PeriodKind,
} from './periods'
import { useExportParts, useIvaFrequency } from './queries'

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
  const [kindChoice, setKindChoice] = useState<PeriodKind | null>(null)
  const [periodChoice, setPeriodChoice] = useState<string | null>(null)
  const [partsOpen, setPartsOpen] = useState(false)
  // Read once: a form open across midnight keeps the periods it was drawn with.
  const [now] = useState(() => new Date())

  // «Tutte le aziende» is a separate option and never the preselected one; a scoped admin has no
  // such option, since an export of every company is an unscoped admin's.
  const mayChooseAll = several && !scoped
  const allChosen = mayChooseAll && companyChoice === ALL
  const chosenId =
    companyChoice !== null && legal_entities.some((entity) => entity.id === companyChoice)
      ? companyChoice
      : null
  const company = allChosen ? null : (chosenId ?? selected ?? legal_entities[0]?.id ?? null)

  const frequency = useIvaFrequency(company)
  // The period the form opens on follows the company's IVA frequency, which is read first: a press
  // before it arrives would build the month the form is about to leave.
  const frequencyPending = company !== null && frequency.isPending
  const kind = kindChoice ?? (frequency.data === 'quarterly' ? 'quarter' : 'month')
  const options = useMemo(() => periodsOf(kind, now), [kind, now])
  const period = options.find((option) => option.value === periodChoice) ?? defaultPeriod(options)

  const parts = useExportParts(company, allChosen, loaded)
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
      {parts.data.map((part) => (
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
                    {mayChooseAll ? <SelectSeparator /> : null}
                    {mayChooseAll ? <SelectItem value={ALL}>Tutte le aziende</SelectItem> : null}
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
                  {KINDS.map((candidate) => (
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
                    {partsWord(parts.data.length)}
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
          </FormGrid>
        </div>
      )}

      {/* The action bar, bottom right, and sticky above the phone's bar so «Crea esportazione» is
          never covered. */}
      <div
        style={
          { '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties
        }
        className="sticky bottom-(--phone-bar) z-10 flex justify-end border-t bg-card py-3 sm:bottom-0"
      >
        <Button type="submit" disabled={!loaded || frequencyPending || period === undefined || sent} className="w-full sm:w-auto">
          Crea esportazione
        </Button>
      </div>
    </form>
  )
}
