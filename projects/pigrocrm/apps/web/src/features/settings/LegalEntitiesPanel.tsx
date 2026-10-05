import { Plus } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Skeleton } from '@rebase/ui/skeleton'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { LegalEntityPanel } from './LegalEntityPanel'
import { FiscalPanel } from './FiscalPanel'
import { NewLegalEntityForm } from './NewLegalEntityForm'
import { useLegalEntities, type LegalEntityRecord } from './queries'

/**
 * Impostazioni → Aziende (REB-617, spec 2026-10-03 §5): the one page that replaced
 * «Emittente» and «Fiscale» when the emitter profile became one row per azienda.
 *
 * With one azienda the page opens straight on it and reads as the two panels did: who
 * issues, then how it is taxed. From the second azienda on, a row of buttons picks
 * which one the two panels show, with the default marked. «Nuova azienda» arrived with
 * milestone 5 (REB-632, spec §9): it waited until the register, the customer chain,
 * the rendering and the per-azienda taxes could serve a second azienda, and it is the
 * first visible piece of the whole project, since the sidebar's selector appears the
 * moment the list has two.
 */
export function LegalEntitiesPanel() {
  const aziende = useLegalEntities()
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  if (aziende.isError) return <QueryErrorBanner error={aziende.error} />
  if (aziende.isLoading || !aziende.data) return <Skeleton className="h-64 w-full" />

  const list = aziende.data
  // The default first, which `GET /api/aziende` already guarantees; a selection that
  // no longer names a listed azienda (deactivated meanwhile) falls back to it.
  const current = list.find((a) => a.id === selected) ?? list[0] ?? null

  if (current === null) {
    return (
      <p className="text-muted-foreground text-sm">
        Questo spazio non ha ancora un'azienda: arriva al primo avvio dopo l'iscrizione.
      </p>
    )
  }

  if (creating) {
    return (
      <NewLegalEntityForm
        onCreated={(created) => {
          setSelected(created.id)
          setCreating(false)
        }}
        onCancel={() => setCreating(false)}
      />
    )
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {list.length > 1 ? (
          <LegalEntityPicker list={list} current={current} onPick={setSelected} />
        ) : (
          <span />
        )}
        <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
          <Plus className="mr-1 size-4" aria-hidden="true" />
          Nuova azienda
        </Button>
      </div>
      <LegalEntityPanel azienda={current} />
      <FiscalPanel aziendaId={current.id} />
    </div>
  )
}

function LegalEntityPicker({
  list,
  current,
  onPick,
}: {
  list: LegalEntityRecord[]
  current: LegalEntityRecord
  onPick: (id: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Aziende">
      {list.map((azienda) => (
        <Button
          key={azienda.id}
          role="tab"
          aria-selected={azienda.id === current.id}
          variant={azienda.id === current.id ? 'default' : 'outline'}
          size="sm"
          onClick={() => onPick(azienda.id)}
        >
          {azienda.nome}
          {azienda.predefinita ? (
            <Badge variant="secondary" className="ml-2">
              predefinita
            </Badge>
          ) : null}
        </Button>
      ))}
    </div>
  )
}
