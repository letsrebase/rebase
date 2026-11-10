import { Plus } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { LegalEntityPanel } from './LegalEntityPanel'
import { FiscalPanel } from './FiscalPanel'
import { NewLegalEntityForm } from './NewLegalEntityForm'
import { useLegalEntities, type LegalEntityRecord } from './queries'

/**
 * Settings → Legal entities (REB-617, spec 2026-10-03 §5): the one page that replaced
 * «Emittente» and «Fiscale» when the emitter profile became one row per legal entity.
 *
 * With one legal entity the page opens straight on it and reads as the two panels did: who
 * issues, then how it is taxed. From the second legal entity on, a row of buttons picks
 * which one the two panels show, with the default marked. «Nuova azienda» arrived with
 * milestone 5 (REB-632, spec §9): it waited until the register, the customer chain,
 * the rendering and the per-legal-entity taxes could serve a second legal entity, and it is the
 * first visible piece of the whole project, since the sidebar's selector appears the
 * moment the list has two.
 */
export function LegalEntitiesPanel() {
  const legal_entities = useLegalEntities()
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  if (legal_entities.isError) return <QueryErrorBanner error={legal_entities.error} />
  if (legal_entities.isLoading || !legal_entities.data) return <LoadingState />

  const list = legal_entities.data
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
      <LegalEntityPanel legal_entity={current} />
      <FiscalPanel legalEntityId={current.id} />
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
      {list.map((legal_entity) => (
        <Button
          key={legal_entity.id}
          role="tab"
          aria-selected={legal_entity.id === current.id}
          variant={legal_entity.id === current.id ? 'default' : 'outline'}
          size="sm"
          onClick={() => onPick(legal_entity.id)}
        >
          {legal_entity.name}
          {legal_entity.is_default ? (
            <Badge variant="secondary" className="ml-2">
              predefinita
            </Badge>
          ) : null}
        </Button>
      ))}
    </div>
  )
}
