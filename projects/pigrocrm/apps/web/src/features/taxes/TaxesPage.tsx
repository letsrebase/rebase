import { Landmark } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Tabs } from '@rebase/ui/tabs'
import { useAuth, useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { roleLabel } from '@/lib/roles'
import { EntityTaxes } from './EntityTaxes'
import type { TaxesTab } from './search'

/** The years the selector offers: the current one and the four before it. */
const YEARS_SHOWN = 5

/**
 * «Tasse» (REB-1026, the approved brief `docs/design/2026-11-10-taxes-brief.md`): a legal entity's
 * taxes and contributions, each with its three readings (estimate, declared, paid), the dates
 * that fall due and, on a solo flat-rate space, as one light page. The year is the tax year;
 * «Imposte» reads it and «Scadenze» lists the dates that fall in that calendar year.
 *
 * Taxes are one legal entity's and never summed: in «Tutte le aziende» each entity is a block
 * under its name. Every figure here is an administrator's to read: any other role gets one
 * sentence and no request is made for a figure, a date or a row, while the sidebar entry stays
 * for everyone so the page can explain itself.
 */
export function TaxesPage({
  tab,
  year,
  onTabChange,
  onYearChange,
}: {
  tab: TaxesTab
  /** The tax year; omitted, the current one. */
  year?: number
  onTabChange: (next: TaxesTab) => void
  onYearChange: (next: number) => void
}) {
  const { user } = useAuth()
  const allowed = useCan('get_fiscal_estimate')
  const { selected, legal_entities, several } = useLegalEntity()
  const current = new Date().getFullYear()
  const chosen = year ?? current
  const years = Array.from({ length: YEARS_SHOWN }, (_, index) => current - index)
  if (!years.includes(chosen)) years.push(chosen)
  const perEntity = several && selected === null

  return (
    <Tabs value={tab} onValueChange={(next) => onTabChange(next as TaxesTab)}>
      <PageHeader
        icon={Landmark}
        title="Tasse"
        description="Imposte e contributi dell’azienda: cosa è stimato, cosa è dichiarato, cosa è già pagato."
        actions={
          allowed ? (
            <Select value={String(chosen)} onValueChange={(next) => {
                const picked = years.find((candidate) => String(candidate) === next)
                if (picked !== undefined) onYearChange(picked)
              }}>
              <SelectTrigger aria-label="Anno d’imposta" className="w-full sm:w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {years.map((candidate) => (
                  <SelectItem key={candidate} value={String(candidate)}>
                    {candidate}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : undefined
        }
      />
      <div className="space-y-8 px-8 pb-6 pt-1">
        {!allowed ? (
          user === null ? null : (
            <p role="note" className="text-sm text-muted-foreground">
              Il tuo ruolo in questo spazio è «{roleLabel(user.role)}»: le tasse sono riservate
              all’«{roleLabel('admin')}».
            </p>
          )
        ) : perEntity ? (
          legal_entities.map((legalEntity) => (
            <EntityTaxes
              key={legalEntity.id}
              year={chosen}
              tab={tab}
              legalEntityId={legalEntity.id}
              name={legalEntity.name}
            />
          ))
        ) : (
          <EntityTaxes year={chosen} tab={tab} />
        )}
      </div>
    </Tabs>
  )
}
