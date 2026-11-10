import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import type { ModuleKey, ModuleRead, SpaceModules } from '@/lib/modules'
import { queryKeys } from '@/lib/query'
import { brokenDependency, dependantsOf, requirementsOf } from './modulesModel'

type Profile = components['schemas']['SpaceProfileSet']['profile']
type Proposal = components['schemas']['SpaceProfileProposal']

const PROFILES: ReadonlyArray<{ value: Profile; label: string; hint: string }> = [
  { value: 'solo', label: 'Da solo', hint: 'Libero professionista con partita IVA' },
  { value: 'company', label: 'Come azienda', hint: 'Una società con un team' },
]

const KEPT = 'I dati restano: riaccendendolo li ritrovi'

function recordsOf(module: Pick<ModuleRead, 'records'>): string {
  return module.records === 1 ? '1 record' : `${module.records} record`
}

/** A switch the person asked for, waiting for the confirmation that lists what it moves. */
interface Pending {
  to: 'on' | 'off'
  /** The module the person asked for. */
  asked: ModuleRead
  /** It first, then the ones that follow it. */
  modules: ModuleRead[]
}

/**
 * Settings, Space and people, Modules (REB-954, spec 2026-11-09 B1 and B2): the space's
 * profile and one switch per module. Admin only, like the rest of the space group; the
 * two writes are the service's own admin checks and this page only asks.
 *
 * Nothing switches silently. Switching a module off asks first when it holds records (they
 * stay and return with it) or when others need it, and the confirmation lists those others
 * by name and what each holds, because the server takes the whole list in one call. Changing
 * the profile asks the server for the diff without applying it, shows every module that
 * would move with how many records it holds, and lets the admin keep any of them as it is.
 */
export function ModulesPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.modules,
    queryFn: () => unwrap(api.GET('/api/settings/modules')),
  })
  const [pending, setPending] = useState<Pending | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [kept, setKept] = useState<Set<ModuleKey>>(new Set())

  const store = (state: SpaceModules) => queryClient.setQueryData(queryKeys.modules, state)

  const switchModules = useMutation({
    mutationFn: (body: components['schemas']['SpaceModulesSet']) =>
      unwrap(api.PUT('/api/settings/modules', { body })),
    onSuccess: (state) => {
      store(state)
      setPending(null)
      toast.success('Moduli aggiornati')
    },
    onError: (error) => toast.error(toProblem(error).detail),
  })

  const propose = useMutation({
    mutationFn: (profile: Profile) =>
      unwrap(api.PUT('/api/settings/modules/profile', { body: { profile, confirm: false } })),
    onSuccess: (result) => {
      if (result.changes.length === 0) {
        // Nothing would move: the profile is still the admin's choice, so it is written.
        applyProfile.mutate({ profile: result.profile, keep: [], changes: [] })
        return
      }
      setKept(new Set())
      setProposal(result)
    },
    onError: (error) => toast.error(toProblem(error).detail),
  })

  const applyProfile = useMutation({
    mutationFn: (args: {
      profile: Profile
      keep: ModuleKey[]
      changes: Proposal['changes']
    }) => {
      const modules: Partial<Record<ModuleKey, boolean>> = {}
      for (const change of args.changes) {
        if (args.keep.includes(change.module)) modules[change.module] = change.was
      }
      return unwrap(
        api.PUT('/api/settings/modules/profile', {
          body: { profile: args.profile, confirm: true, modules },
        }),
      )
    },
    onSuccess: (result) => {
      store(result.state)
      setProposal(null)
      toast.success('Profilo aggiornato')
    },
    onError: (error) => toast.error(toProblem(error).detail),
  })

  if (query.isError && !query.data) return <QueryErrorBanner error={query.error} />
  if (!query.data) return <p className="text-muted-foreground text-sm">Caricamento…</p>
  const { profile, modules } = query.data

  // The modules as they would be once the profile is applied with the admin's keeps.
  const final = new Map<ModuleKey, boolean>(modules.map((m) => [m.key, m.enabled]))
  if (proposal) {
    for (const change of proposal.changes) {
      final.set(change.module, kept.has(change.module) ? change.was : change.becomes)
    }
  }
  const broken = proposal ? brokenDependency(modules, final) : null

  function toggle(module: ModuleRead, wantOn: boolean) {
    if (wantOn) {
      const needs = requirementsOf(modules, module.key)
      if (needs.length === 0) {
        switchModules.mutate({ on: [module.key] })
      } else {
        setPending({ to: 'on', asked: module, modules: [module, ...needs] })
      }
      return
    }
    const dependants = dependantsOf(modules, module.key)
    if (dependants.length === 0 && !module.holds_data) {
      switchModules.mutate({ off: [module.key] })
    } else {
      setPending({ to: 'off', asked: module, modules: [module, ...dependants] })
    }
  }

  function confirmPending(current: Pending) {
    const keys = current.modules.map((module) => module.key)
    if (current.to === 'on') {
      switchModules.mutate({ on: keys })
    } else {
      switchModules.mutate({
        off: keys,
        confirm_off: current.modules.filter((module) => module.holds_data).map((m) => m.key),
      })
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Profilo</CardTitle>
          <CardDescription>
            Come lavora lo spazio. Cambiarlo propone quali moduli accendere e spegnere: li
            confermi tu, nessun dato viene toccato.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Profilo">
            {PROFILES.map((option) => (
              <Button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={profile === option.value}
                variant={profile === option.value ? 'default' : 'outline'}
                className="h-auto flex-col items-start gap-1 whitespace-normal py-3 text-left"
                disabled={propose.isPending}
                onClick={() => {
                  if (profile !== option.value) propose.mutate(option.value)
                }}
              >
                <span>{option.label}</span>
                <span className="text-xs font-normal opacity-80">{option.hint}</span>
              </Button>
            ))}
          </div>
          {profile === null ? (
            <p className="mt-3 text-sm text-muted-foreground">
              Nessun profilo scelto: tutti i moduli sono accesi.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Moduli</CardTitle>
          <CardDescription>
            Un modulo spento sparisce da menu, ricerca e Home per tutto lo spazio.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-border">
            {modules.map((module) => {
              const id = `module-${module.key}`
              return (
                <li key={module.key} className="flex items-start gap-3 py-3">
                  <Checkbox
                    id={id}
                    className="mt-1"
                    checked={module.enabled}
                    disabled={switchModules.isPending}
                    onCheckedChange={(next) => toggle(module, next === true)}
                  />
                  <div className="min-w-0 flex-1">
                    <label htmlFor={id} className="block text-sm font-medium">
                      {module.label}
                    </label>
                    <p className="text-sm text-muted-foreground">{module.description}</p>
                    {module.requires.length > 0 ? (
                      <p className="text-xs text-muted-foreground">
                        Richiede{' '}
                        {module.requires
                          .map((need) => modules.find((m) => m.key === need)?.label ?? need)
                          .join(', ')}
                      </p>
                    ) : null}
                    {module.holds_data ? (
                      <p className="text-xs text-muted-foreground">
                        {recordsOf(module)}. {KEPT}.
                      </p>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        </CardContent>
      </Card>

      <Dialog open={pending !== null} onOpenChange={(open) => (open ? null : setPending(null))}>
        <DialogContent>
          {pending ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  {pending.to === 'off'
                    ? `Spegnere ${pending.asked.label}?`
                    : `Accendere ${pending.asked.label}?`}
                </DialogTitle>
                <DialogDescription>
                  {pending.to === 'off'
                    ? pending.modules.length > 1
                      ? `${pending.asked.label} serve ad altri moduli accesi: si spengono insieme.`
                      : KEPT + '.'
                    : `${pending.asked.label} ha bisogno di altri moduli: si accendono insieme.`}
                </DialogDescription>
              </DialogHeader>
              <ul className="space-y-1 text-sm" aria-label="Moduli coinvolti">
                {pending.modules.map((module) => (
                  <li key={module.key}>
                    <span className="font-medium">{module.label}</span>
                    {pending.to === 'off' && module.holds_data
                      ? `: ${recordsOf(module)}. ${KEPT}`
                      : ''}
                  </li>
                ))}
              </ul>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Annulla
                </Button>
                <Button disabled={switchModules.isPending} onClick={() => confirmPending(pending)}>
                  {pending.to === 'off' ? 'Spegni' : 'Accendi'}
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={proposal !== null} onOpenChange={(open) => (open ? null : setProposal(null))}>
        <DialogContent>
          {proposal ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  Passare a «{PROFILES.find((p) => p.value === proposal.profile)?.label}»?
                </DialogTitle>
                <DialogDescription>
                  Questi moduli cambierebbero. Togli la spunta a quelli che vuoi lasciare come
                  sono. I dati non vengono toccati.
                </DialogDescription>
              </DialogHeader>
              <ul className="space-y-2 text-sm" aria-label="Moduli che cambiano">
                {proposal.changes.map((change) => {
                  const id = `change-${change.module}`
                  return (
                    <li key={change.module} className="flex items-start gap-3">
                      <Checkbox
                        id={id}
                        className="mt-1"
                        checked={!kept.has(change.module)}
                        onCheckedChange={(next) =>
                          setKept((current) => {
                            const out = new Set(current)
                            if (next === true) out.delete(change.module)
                            else out.add(change.module)
                            return out
                          })
                        }
                      />
                      <label htmlFor={id}>
                        <span className="font-medium">{change.label}</span>{' '}
                        {change.becomes ? 'si accende' : 'si spegne'}
                        {change.records > 0 ? (
                          <span className="block text-xs text-muted-foreground">
                            {recordsOf(change)}
                            {change.becomes ? '' : `. ${KEPT}`}
                          </span>
                        ) : null}
                      </label>
                    </li>
                  )
                })}
              </ul>
              {broken ? (
                <p role="alert" className="text-sm text-destructive">
                  {broken.module.label} richiede {broken.needs.label}: tieni insieme i due moduli o
                  cambiali entrambi.
                </p>
              ) : null}
              <DialogFooter>
                <Button variant="ghost" onClick={() => setProposal(null)}>
                  Annulla
                </Button>
                <Button
                  disabled={applyProfile.isPending || broken !== null}
                  onClick={() =>
                    applyProfile.mutate({
                      profile: proposal.profile,
                      keep: [...kept],
                      changes: proposal.changes,
                    })
                  }
                >
                  Conferma
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
