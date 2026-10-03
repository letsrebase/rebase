import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { AziendaPanel } from './AziendaPanel'
import { FiscalPanel } from './FiscalPanel'
import { useAziende, type AziendaRecord } from './queries'

/**
 * Impostazioni → Aziende (REB-617, spec 2026-10-03 §5): the one page that replaced
 * «Emittente» and «Fiscale» when the emitter profile became one row per azienda.
 *
 * With one azienda, which is every space until milestone 5 opens creation, the page
 * opens straight on it and reads as the two panels did: who issues, then how it is
 * taxed. From the second azienda on, a row of buttons picks which one the two panels
 * show, with the default marked. There is no «Nuova azienda» here on purpose: a second
 * azienda would have invoices nothing can number yet and customers it cannot own
 * (spec §9), so the button lands with the milestone that can serve it.
 */
export function AziendePanel() {
  const aziende = useAziende()
  const [selected, setSelected] = useState<string | null>(null)

  if (aziende.isError) return <QueryErrorBanner error={aziende.error} />
  if (aziende.isLoading || !aziende.data) return null

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

  return (
    <div className="space-y-8">
      {list.length > 1 ? <AziendaPicker list={list} current={current} onPick={setSelected} /> : null}
      <AziendaPanel azienda={current} />
      <FiscalPanel aziendaId={current.id} />
    </div>
  )
}

function AziendaPicker({
  list,
  current,
  onPick,
}: {
  list: AziendaRecord[]
  current: AziendaRecord
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
