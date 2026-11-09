/**
 * The onboarding of a new space (spec 2026-11-09, B3, REB-957): a full screen with three
 * steps, shown to an administrator while the space has no profile and never by itself
 * again. Every step has «Decido dopo».
 *
 * 1. «Come lavori?»: alone with a partita IVA, or as a company. Skipping it leaves every
 *    module on and moves the onboarding to the first item of «Primi passi»
 *    (`skip_space_onboarding`).
 * 2. The regime of the first legal entity: writes its fiscal pack, unless the space already
 *    saved a fiscal profile (a whole-row save would overwrite what it holds).
 * 3. The modules the profile switches on and off, as a list to confirm or edit, with how
 *    many records each holds. For a company the collaborators and staff questions join this
 *    step once those modules exist (C5, C7); until then it asks nothing it cannot act on.
 *
 * Nothing is written before the last step except the regime, so leaving halfway leaves the
 * space as it was and the onboarding comes back at the next login. The writes are the REST
 * counterparts of `set_space_profile` and `set_space_modules`; an existing space with data
 * goes through the same confirmation, which names what would be hidden.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { BrandMark } from '@rebase/ui/brand-mark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Checkbox } from '@rebase/ui/checkbox'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { cn } from '@rebase/ui/cn'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import { api, toProblem, unwrap } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { ModuleKey } from '@/lib/modules'
import { queryKeys } from '@/lib/query'
import { ORDINARY_RATE, bodyFrom, emptyValues } from '@/features/settings/fiscalModel'
import {
  COMING_FORMS,
  REGIMES,
  editsAgainst,
  hiddenWithData,
  proposedState,
  withDependencies,
  type FiscalPack,
  type Profile,
  type Proposal,
} from './model'

type Step = 1 | 2 | 3

const PROFILE_CHOICES: readonly { value: Profile; label: string; hint: string }[] = [
  {
    value: 'solo',
    label: 'Da solo, con partita IVA',
    hint: 'Lavori per i tuoi clienti a giornata o a canone: contratti, giornate, fatture.',
  },
  {
    value: 'company',
    label: 'Come azienda',
    hint: 'Hai una pipeline di trattative, ore sulle commesse e più persone nel team.',
  },
]

function problem(error: unknown): string {
  const found = toProblem(error)
  return found.detail ?? found.title
}

/** The first legal entity and whether it already has a fiscal profile: the regime step
 *  never overwrites one, since the save replaces the whole row. */
function useFirstLegalEntity() {
  return useQuery({
    queryKey: [...queryKeys.legal_entities, 'onboarding'],
    queryFn: async () => {
      const list = await unwrap(api.GET('/api/legal-entities'))
      const first = list[0]
      if (!first) return null
      const { data, response } = await api.GET('/api/legal-entities/{legal_entity_id}/fiscal-profile', {
        params: { path: { legal_entity_id: first.id } },
      })
      if (response.status !== 404 && !data) throw new Error('fiscal-profile: unreadable')
      return { id: first.id, saved: data ?? null }
    },
    retry: false,
  })
}

/** The whole row a first save sends, the form's own defaults for the pack (a save replaces
 *  the row, so a key left out would be the server's default for another regime). */
function fiscalBodyFor(pack: FiscalPack): Record<string, unknown> {
  const ordinary = pack === 'it-standard'
  return bodyFrom({
    ...emptyValues(),
    pack_id: pack,
    regime_code: ordinary ? 'RF01' : 'RF19',
    ...(ordinary ? { default_vat_rate: ORDINARY_RATE } : {}),
  })
}

function defaultPack(profile: Profile): FiscalPack | null {
  const choices = REGIMES[profile]
  return choices.length === 1 ? (choices[0]?.pack ?? null) : null
}

function Progress({ step }: { step: Step }) {
  return (
    <div className="space-y-2" data-testid="onboarding-progress">
      <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        Passo {step} di 3
      </p>
      <div className="flex gap-1" aria-hidden="true">
        {[1, 2, 3].map((n) => (
          <span key={n} className={cn('h-1 flex-1', n <= step ? 'bg-foreground' : 'bg-foreground/15')} />
        ))}
      </div>
    </div>
  )
}

function Choice({
  checked,
  disabled = false,
  label,
  hint,
  badge,
  onSelect,
}: {
  checked: boolean
  disabled?: boolean
  label: string
  hint: string
  badge?: string
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        'flex min-h-14 w-full items-start gap-3 border p-4 text-left transition-colors outline-none',
        'focus-visible:ring-ring/50 focus-visible:ring-3',
        checked ? 'border-foreground bg-card' : 'border-border hover:border-foreground/60',
        disabled && 'cursor-not-allowed opacity-50 hover:border-border',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'mt-0.5 grid size-4 shrink-0 place-content-center rounded-full border',
          checked ? 'border-foreground' : 'border-input',
        )}
      >
        {checked && <span className="bg-foreground size-2 rounded-full" />}
      </span>
      <span className="min-w-0">
        <span className="block font-medium">
          {label}
          {badge && (
            <span className="text-muted-foreground ml-2 text-xs font-normal">{badge}</span>
          )}
        </span>
        <span className="text-muted-foreground block text-sm">{hint}</span>
      </span>
    </button>
  )
}

function Footer({
  onLater,
  onBack,
  onNext,
  nextLabel = 'Avanti',
  nextDisabled = false,
  busy = false,
}: {
  onLater: () => void
  onBack?: () => void
  onNext: () => void
  nextLabel?: string
  nextDisabled?: boolean
  busy?: boolean
}) {
  return (
    <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <Button type="button" variant="ghost" className="min-h-11 sm:min-h-9" onClick={onLater} disabled={busy}>
          Decido dopo
        </Button>
        {onBack && (
          <Button type="button" variant="outline" className="min-h-11 sm:min-h-9" onClick={onBack} disabled={busy}>
            Indietro
          </Button>
        )}
      </div>
      <Button type="button" className="min-h-11 sm:min-h-9" onClick={onNext} disabled={nextDisabled || busy}>
        {nextLabel}
      </Button>
    </div>
  )
}

export function Onboarding() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>(1)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [pack, setPack] = useState<FiscalPack | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [wanted, setWanted] = useState<Record<ModuleKey, boolean> | null>(null)
  const legalEntity = useFirstLegalEntity()
  const { logout } = useAuth()

  const finish = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.modules })
    await navigate({ to: '/app', search: defaultDashboardSearch() })
  }

  const skip = useMutation({
    mutationFn: () => unwrap(api.POST('/api/settings/modules/onboarding/skip')),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.modules })
      await navigate({ to: '/app/get-started' })
    },
    onError: (error) => toast.error(problem(error)),
  })

  /** Step 2 to step 3: the modules the profile would switch, with nothing written yet. */
  const propose = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT('/api/settings/modules/profile', {
          body: { profile: profile as Profile, confirm: false },
        }),
      ),
    onSuccess: (result) => {
      setProposal(result)
      setWanted(proposedState(result))
      setStep(3)
    },
    onError: (error) => toast.error(problem(error)),
  })

  /** The last step: the regime (unless the space already has one) and then the profile with
   *  its module edits. Written together, here, so going back to change the profile never
   *  leaves a regime that belongs to the other answer. */
  const apply = useMutation({
    mutationFn: async (modules: Partial<Record<ModuleKey, boolean>>) => {
      const entity = legalEntity.data
      const chosen = pack ?? (profile ? defaultPack(profile) : null)
      if (entity && !entity.saved && chosen) {
        await unwrap(
          api.PUT('/api/legal-entities/{legal_entity_id}/fiscal-profile', {
            params: { path: { legal_entity_id: entity.id } },
            // `null` is a real version check: a profile saved since this screen opened is a
            // 409, never silently replaced.
            body: { ...fiscalBodyFor(chosen), updated_at: null } as never,
          }),
        )
        await queryClient.invalidateQueries({ queryKey: queryKeys.legal_entities })
      }
      return unwrap(
        api.PUT('/api/settings/modules/profile', {
          body: { profile: profile as Profile, confirm: true, modules },
        }),
      )
    },
    onSuccess: finish,
    onError: (error) => toast.error(problem(error)),
  })

  const chooseProfile = (next: Profile) => {
    setProfile(next)
    setPack(null)
  }

  return (
    <div className="bg-background flex min-h-full items-start justify-center p-4 sm:items-center sm:p-8" data-testid="onboarding">
      <Card className="w-full max-w-xl">
        <CardHeader className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="inline-flex items-center text-lg font-semibold">
              <BrandMark className="mr-2.5 size-3.5" />
              Pigro<span className="text-[var(--color-watermelon)]">CRM</span>
            </p>
            {/* The screen stands outside the shell, so it carries its own way out. */}
            <Button type="button" variant="ghost" size="sm" onClick={() => void logout()}>
              Esci
            </Button>
          </div>
          <Progress step={step} />
          {step === 1 && (
            <>
              <CardTitle>
                <h1 className="text-2xl">Come lavori?</h1>
              </CardTitle>
              <CardDescription>
                Lo spazio mostra solo quello che ti serve. Puoi cambiare tutto dopo, in Impostazioni.
              </CardDescription>
            </>
          )}
          {step === 2 && (
            <>
              <CardTitle>
                <h1 className="text-2xl">Con quale regime fatturi?</h1>
              </CardTitle>
              <CardDescription>
                È il regime della tua prima azienda: decide IVA e testo delle fatture.
              </CardDescription>
            </>
          )}
          {step === 3 && (
            <>
              <CardTitle>
                <h1 className="text-2xl">Ecco come sarà il tuo spazio</h1>
              </CardTitle>
              <CardDescription>
                Questi sono i moduli accesi per il tuo profilo. Cambia quelli che vuoi: nessun dato viene toccato.
              </CardDescription>
            </>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {step === 1 && (
            <>
              <div role="radiogroup" aria-label="Come lavori?" className="space-y-2">
                {PROFILE_CHOICES.map((choice) => (
                  <Choice
                    key={choice.value}
                    checked={profile === choice.value}
                    label={choice.label}
                    hint={choice.hint}
                    onSelect={() => chooseProfile(choice.value)}
                  />
                ))}
              </div>
              <Footer
                onLater={() => skip.mutate()}
                onNext={() => setStep(2)}
                nextDisabled={profile === null}
                busy={skip.isPending}
              />
            </>
          )}
          {step === 2 && profile && (
            <RegimeStep
              profile={profile}
              pack={pack}
              onPack={setPack}
              legalEntity={legalEntity}
              busy={propose.isPending || skip.isPending}
              onBack={() => setStep(1)}
              onLater={() => skip.mutate()}
              onNext={() => propose.mutate()}
            />
          )}
          {step === 3 && proposal && wanted && (
            <ModulesStep
              proposal={proposal}
              wanted={wanted}
              onChange={setWanted}
              busy={apply.isPending || skip.isPending}
              onBack={() => setStep(2)}
              onConfirm={() => apply.mutate(editsAgainst(proposal, wanted))}
              onLater={() => skip.mutate()}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RegimeStep({
  profile,
  pack,
  onPack,
  legalEntity,
  busy,
  onBack,
  onLater,
  onNext,
}: {
  profile: Profile
  pack: FiscalPack | null
  onPack: (pack: FiscalPack) => void
  legalEntity: ReturnType<typeof useFirstLegalEntity>
  busy: boolean
  onBack: () => void
  onLater: () => void
  onNext: () => void
}) {
  if (legalEntity.isPending) {
    return (
      <div className="flex justify-center py-8">
        <Loader className="size-8" />
      </div>
    )
  }
  if (legalEntity.isError || !legalEntity.data) {
    // Nothing to write the regime on: say so rather than let the answer vanish.
    return (
      <>
        <p className="text-sm" role="alert">
          {legalEntity.isError
            ? 'Non riesco a leggere i dati della tua azienda.'
            : 'Non c’è ancora un’azienda a cui dare un regime: la crei in Impostazioni → Aziende.'}
        </p>
        {legalEntity.isError && (
          <Button type="button" variant="outline" onClick={() => void legalEntity.refetch()}>
            Riprova
          </Button>
        )}
        <Footer onLater={onLater} onBack={onBack} onNext={onNext} nextDisabled busy={busy} />
      </>
    )
  }
  if (legalEntity.data.saved) {
    return (
      <>
        <p className="text-sm" role="status">
          Il regime della tua azienda è già impostato. Lo cambi in Impostazioni → Aziende.
        </p>
        <Footer onLater={onLater} onBack={onBack} onNext={onNext} busy={busy} />
      </>
    )
  }
  const choices = REGIMES[profile]
  const selected = pack ?? defaultPack(profile)
  return (
    <>
      <div role="radiogroup" aria-label="Regime" className="space-y-2">
        {choices.map((choice) => (
          <Choice
            key={choice.pack}
            checked={selected === choice.pack}
            label={choice.label}
            hint={choice.hint}
            onSelect={() => onPack(choice.pack)}
          />
        ))}
        {profile === 'company' &&
          COMING_FORMS.map((form) => (
            <Choice key={form} checked={false} disabled label={form} hint="Altre forme societarie." badge="presto" onSelect={() => {}} />
          ))}
      </div>
      <Footer onLater={onLater} onBack={onBack} onNext={onNext} nextDisabled={selected === null} busy={busy} />
    </>
  )
}

function ModulesStep({
  proposal,
  wanted,
  onChange,
  busy,
  onBack,
  onConfirm,
  onLater,
}: {
  proposal: Proposal
  wanted: Record<ModuleKey, boolean>
  onChange: (next: Record<ModuleKey, boolean>) => void
  busy: boolean
  onBack: () => void
  onConfirm: () => void
  onLater: () => void
}) {
  const requires = Object.fromEntries(proposal.state.modules.map((m) => [m.key, m.requires]))
  const hidden = hiddenWithData(proposal, wanted)
  const toggle = (key: ModuleKey, on: boolean) =>
    onChange(withDependencies({ ...wanted, [key]: on }, requires) as Record<ModuleKey, boolean>)
  return (
    <>
      <ul className="divide-y border" data-testid="onboarding-modules">
        {proposal.state.modules.map((module) => {
          const blocked = module.requires.some((need) => !wanted[need as ModuleKey])
          const id = `module-${module.key}`
          return (
            <li key={module.key} className="flex items-start gap-3 p-3">
              <Checkbox
                id={id}
                className="mt-1"
                checked={wanted[module.key]}
                disabled={blocked}
                onCheckedChange={(value) => toggle(module.key, value === true)}
              />
              <div className="min-w-0">
                <Label htmlFor={id} className="font-medium">
                  {module.label}
                </Label>
                <p className="text-muted-foreground text-sm">{module.description}</p>
                {module.holds_data && (
                  <p className="text-sm">
                    {module.records} record: restano, riaccendendolo li ritrovi.
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ul>
      {hidden.length > 0 && (
        <p className="border-foreground border-l-2 pl-3 text-sm" role="alert">
          Scompaiono dal menu:{' '}
          {hidden.map((h) => `${h.label} (${h.records} record)`).join(', ')}. Spunta il modulo per tenerlo acceso.
        </p>
      )}
      <Footer onLater={onLater} onBack={onBack} onNext={onConfirm} nextLabel="Conferma" busy={busy} />
    </>
  )
}
