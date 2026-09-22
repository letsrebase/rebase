import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Skeleton } from '@rebase/ui/skeleton'
import {
  useAutomations,
  useUpdateAutomationConfig,
  type AutomationConfigUpdate,
} from '@/features/dashboard/queries'
import { cn } from '@rebase/ui/cn'

/**
 * §9.5's third observability surface: the two rules, their switch, and the last executions
 * with their outcome -- a query over `activities` by `kind`, never a new table.
 *
 * The non-executions are the reason this page earns its place. Without them, "it did not
 * fire" and "it was not supposed to fire" are the same empty list.
 */

const REASONS: Record<string, string> = {
  stage_bersaglio_assente: 'lo stato bersaglio non esiste',
  // "c'è più di uno", not "ci sono più di uno": the subject is «uno».
  stage_bersaglio_ambiguo: 'c’è più di uno stato «vinto»: l’automazione non indovina',
  gia_nello_stato: 'il deal era già nello stato bersaglio',
  regola_disattivata: 'la regola è disattivata',
}

const KINDS: Record<string, string> = {
  'automazione.stage_spostato': 'Deal spostato',
  'automazione.non_eseguita': 'Non eseguita',
  'automazione.configurazione_modificata': 'Configurazione modificata',
}

const FIELD_BY_RULE: Record<string, keyof AutomationConfigUpdate> = {
  A1: 'a1_offerta_accettata_vince_deal',
  A2: 'a2_offerta_inviata_avanza_deal',
}

const timestamp = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })

/**
 * A switch, hand-rolled rather than imported: `components/ui/` has no `switch.tsx` and
 * adding one means running the shadcn generator, which is a network fetch and a new
 * dependency for one control. `role="switch"` with `aria-checked` is what a screen reader
 * and `getByRole('switch')`/`toBeChecked()` both read, so nothing is lost but the CLI's
 * animation.
 */
function RuleSwitch({
  label,
  checked,
  disabled,
  onToggle,
}: {
  label: string
  checked: boolean
  disabled: boolean
  onToggle: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onToggle(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 items-center border border-input transition-colors disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-muted',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block size-4 transition-transform',
          checked ? 'translate-x-4 bg-card' : 'translate-x-0.5 bg-foreground',
        )}
      />
    </button>
  )
}

export function AutomationsPanel() {
  const query = useAutomations()
  const update = useUpdateAutomationConfig()

  // The error branch before the loading branch: on a failure `isPending` is false while
  // `data` is still undefined, so a single `isPending || !data` guard would answer a failed
  // read with a spinner that never resolves.
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) return <Skeleton className="h-64 w-full" />

  const data = query.data

  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Automazioni</h2>
        {/* Rendered from the mutation's own `error`, never copied into component state: an
            error set into state and never cleared prints a refusal under the next success,
            a defect this codebase has fixed twice. */}
        {update.isError && <QueryErrorBanner error={update.error} />}
        {data.regole.map((rule) => {
          const field = FIELD_BY_RULE[rule.codice]
          return (
            <div key={rule.codice} className="flex items-start gap-4 rounded-lg border p-4">
              <RuleSwitch
                label={rule.titolo}
                checked={rule.attiva}
                // A rule the client has no field for cannot be switched from here. Better a
                // disabled control than a PUT with an `undefined` key, which `extra="forbid"`
                // would reject anyway -- and better a visible one than a rule silently
                // missing from the page.
                disabled={field === undefined || update.isPending}
                onToggle={(next) => {
                  if (field === undefined) return
                  // Only the rule that changed is sent. The API reads the body with
                  // `exclude_unset=True`, so an omitted field changes nothing -- and
                  // `false` is a value, never a blank.
                  update.mutate({ [field]: next })
                }}
              />
              <div className="min-w-0">
                <p className="text-sm font-medium">{rule.titolo}</p>
                <p className="mt-1 text-xs text-muted-foreground">{rule.descrizione}</p>
              </div>
            </div>
          )
        })}
      </section>

      <section>
        <h2 className="text-lg font-semibold">Ultime esecuzioni</h2>
        {data.esecuzioni.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nessuna esecuzione registrata.</p>
        ) : (
          <ul className="mt-2 divide-y text-sm">
            {data.esecuzioni.map((run, index) => (
              <li key={`${run.occurred_at}-${index}`} className="py-2">
                {/* An unknown `kind` shows its raw value rather than nothing: `activities.kind`
                    is an open value by project, so this map will fall behind the backend one
                    day and a blank line would hide the run entirely. */}
                <span className="font-medium">{KINDS[run.kind] ?? run.kind}</span>
                {run.regola && <span className="text-muted-foreground"> · {run.regola}</span>}
                {run.motivo && (
                  <span className="text-muted-foreground"> · {REASONS[run.motivo] ?? run.motivo}</span>
                )}
                <span className="ml-2 text-xs text-muted-foreground">
                  {timestamp.format(new Date(run.occurred_at))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
