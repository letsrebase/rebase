import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { useFiscalProfile } from '@/features/invoices/queries'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import {
  useAddLegalEntitySite,
  useIrapRegions,
  useLegalEntitySites,
  useRemoveLegalEntitySite,
  useUpdateLegalEntitySite,
  type LegalEntityRecord,
  type LegalEntitySite,
} from './queries'
import { sitePlace } from './sites'

/**
 * The places of business of a company beside its registered office (REB-1092): an srl with
 * people in more than one region splits its IRAP base between them by the pay of the people at
 * each (art. 4 c. 2 D.Lgs. 446/1997). Shown only for an azienda on the company pack, the one
 * that owes IRAP as a company; the server decides everything else, a refusal included.
 */
export function LegalEntitySites({ legal_entity }: { legal_entity: LegalEntityRecord }) {
  const profile = useFiscalProfile(legal_entity.id)
  if (profile.data?.pack_id !== 'it-company') return null
  return <SitesSection legalEntityId={legal_entity.id} />
}

function SitesSection({ legalEntityId }: { legalEntityId: string }) {
  const sites = useLegalEntitySites(legalEntityId)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)

  return (
    <section className="space-y-3" aria-labelledby={`sites-${legalEntityId}`}>
      <div>
        <h3 id={`sites-${legalEntityId}`} className="text-sm font-medium">
          Sedi
        </h3>
        <p className="text-muted-foreground text-xs">
          La sede legale è l'indirizzo dell'azienda. Aggiungi le altre sedi, con la regione: una srl con
          personale in più regioni ripartisce l'IRAP in base alle retribuzioni delle persone di ciascuna.
        </p>
      </div>

      {sites.isError ? (
        <p className="text-destructive text-sm" role="alert">
          {toProblem(sites.error).detail}
        </p>
      ) : sites.isLoading ? (
        <p className="text-muted-foreground text-sm">Caricamento…</p>
      ) : (
        <ul className="divide-y border">
          {(sites.data ?? []).length === 0 ? (
            <li className="text-muted-foreground px-3 py-3 text-sm">Nessuna sede oltre a quella legale.</li>
          ) : null}
          {(sites.data ?? []).map((site) =>
            editing === site.id ? (
              <li key={site.id} className="p-3">
                <EditSite legalEntityId={legalEntityId} site={site} onDone={() => setEditing(null)} />
              </li>
            ) : (
              <SiteRow
                key={site.id}
                legalEntityId={legalEntityId}
                site={site}
                onEdit={() => {
                  setAdding(false)
                  setEditing(site.id)
                }}
              />
            ),
          )}
        </ul>
      )}

      {adding ? (
        <div className="border p-3">
          <AddSite legalEntityId={legalEntityId} onDone={() => setAdding(false)} />
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setEditing(null)
            setAdding(true)
          }}
        >
          Aggiungi una sede
        </Button>
      )}
    </section>
  )
}

function peopleLabel(count: number): string {
  if (count === 0) return 'Nessuna persona'
  return count === 1 ? '1 persona' : `${count} persone`
}

function SiteRow({ legalEntityId, site, onEdit }: { legalEntityId: string; site: LegalEntitySite; onEdit: () => void }) {
  const remove = useRemoveLegalEntitySite(legalEntityId, site.id)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function clear() {
    setProblem(null)
    remove.mutate(undefined, {
      onSuccess: () => toast.success('Sede rimossa'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  return (
    <li className="space-y-1 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">{site.region_name ?? site.region}</p>
          <p className="text-muted-foreground text-xs">
            {sitePlace(site) ?? 'Senza comune né indirizzo'}
            {' · '}
            {peopleLabel(site.staff_count)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={remove.isPending} onClick={onEdit}>
            Modifica
          </Button>
          <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={clear}>
            Rimuovi
          </Button>
        </div>
      </div>
      {problem ? (
        <p className="text-destructive text-sm" role="alert">
          {problem.detail}
        </p>
      ) : null}
    </li>
  )
}

function AddSite({ legalEntityId, onDone }: { legalEntityId: string; onDone: () => void }) {
  const add = useAddLegalEntitySite(legalEntityId)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  return (
    <SiteForm
      legalEntityId={legalEntityId}
      initial={{ region: '', city: '', address: '' }}
      submitLabel="Aggiungi"
      pending={add.isPending}
      problem={problem}
      onCancel={onDone}
      onSubmit={(values) => {
        setProblem(null)
        add.mutate(
          { region: values.region, city: values.city.trim() || null, address: values.address.trim() || null },
          {
            onSuccess: () => {
              toast.success('Sede aggiunta')
              onDone()
            },
            onError: (error) => setProblem(toProblem(error)),
          },
        )
      }}
    />
  )
}

function EditSite({ legalEntityId, site, onDone }: { legalEntityId: string; site: LegalEntitySite; onDone: () => void }) {
  const update = useUpdateLegalEntitySite(legalEntityId, site.id)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  return (
    <SiteForm
      legalEntityId={legalEntityId}
      initial={{ region: site.region, city: site.city ?? '', address: site.address ?? '' }}
      submitLabel="Salva"
      pending={update.isPending}
      problem={problem}
      onCancel={onDone}
      onSubmit={(values) => {
        setProblem(null)
        update.mutate(
          { region: values.region, city: values.city.trim() || null, address: values.address.trim() || null },
          {
            onSuccess: () => {
              toast.success('Sede salvata')
              onDone()
            },
            onError: (error) => setProblem(toProblem(error)),
          },
        )
      }}
    />
  )
}

interface SiteValues {
  region: string
  city: string
  address: string
}

function SiteForm({
  legalEntityId,
  initial,
  submitLabel,
  pending,
  problem,
  onCancel,
  onSubmit,
}: {
  legalEntityId: string
  initial: SiteValues
  submitLabel: string
  pending: boolean
  problem: ProblemDetail | null
  onCancel: () => void
  onSubmit: (values: SiteValues) => void
}) {
  const regions = useIrapRegions()
  const [values, setValues] = useState(initial)
  const [missing, setMissing] = useState(false)
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const regionError = missing ? 'Scegli la regione.' : fieldError?.field === 'region' ? fieldError.message : null
  const prefix = `sede-${legalEntityId}`

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (values.region === '') {
          setMissing(true)
          return
        }
        onSubmit(values)
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor={`${prefix}-region`}>Regione *</Label>
          <Select
            value={values.region === '' ? undefined : values.region}
            onValueChange={(next) => {
              setMissing(false)
              setValues((previous) => ({ ...previous, region: next }))
            }}
          >
            <SelectTrigger id={`${prefix}-region`} className="w-full" aria-invalid={regionError ? true : undefined}>
              <SelectValue placeholder="Scegli la regione" />
            </SelectTrigger>
            <SelectContent>
              {(regions.data ?? []).map((region) => (
                <SelectItem key={region.key} value={region.key}>
                  {region.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {regionError ? <p className="text-destructive text-sm">{regionError}</p> : null}
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${prefix}-city`}>Comune</Label>
          <Input
            id={`${prefix}-city`}
            value={values.city}
            onChange={(event) => setValues((previous) => ({ ...previous, city: event.target.value }))}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${prefix}-address`}>Indirizzo</Label>
          <Input
            id={`${prefix}-address`}
            value={values.address}
            onChange={(event) => setValues((previous) => ({ ...previous, address: event.target.value }))}
          />
        </div>
      </div>
      {problem && !fieldError ? (
        <p className="text-destructive text-sm" role="alert">
          {problem.detail}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onCancel}>
          Annulla
        </Button>
        <Button type="submit" size="sm" disabled={pending}>
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}
