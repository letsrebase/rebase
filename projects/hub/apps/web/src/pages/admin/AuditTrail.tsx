import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { ApiError, admin, type AdminAction, type CommentKind } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

const KIND_LABELS: Record<AdminAction['kind'], string> = {
  overridden: 'Modifica',
  cleared: 'CV rimosso',
  deleted: 'Eliminazione',
  restored: 'Ripristino',
}

const FIELD_LABELS: Record<string, string> = {
  nome: 'Nome',
  cognome: 'Cognome',
  linkedin_url: 'LinkedIn',
  tariffa_giornaliera: 'Tariffa a giornata',
  posizione: 'Posizione',
  remoto: 'Modalità',
  links: 'Link',
  compilata_da: 'Compilata da',
  nome_azienda: 'Azienda',
  progetto: 'Progetto',
  periodo_da: 'Periodo',
  durata: 'Durata',
  budget_giornaliero: 'Budget a giornata',
  cv: 'CV',
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  return String(value)
}

/**
 * Who overrode, cleared, deleted, restored or reverted a freelancer card or a company
 * request, and when (REB-355): newest first, with the before/after diff `field_changes`
 * kept on an `overridden`/`cleared` entry. Only an `overridden` entry can be reverted
 * (`AdminActionService`'s own module docstring); a cleared CV has no bytes left to put
 * back and a delete reverses through the record's own «Ripristina» instead.
 *
 * Self-contained, unlike `StatusEditor`: the trail is not part of the detail page's own
 * query at all (a separate `GET .../audit`, never embedded in `FreelancerRead`/
 * `CompanyRead` the way `commenti` is), so it fetches and reverts on its own and only
 * asks the page to refetch the record itself (`onReverted`) once a revert lands.
 */
export function AuditTrail({
  kind,
  id,
  canRevert,
  onReverted,
}: {
  kind: CommentKind
  id: string
  /** `false` while the record is soft-deleted: the server refuses a revert on it until
   *  it is restored (`FreelancerService.revert`/`CompanyService.revert`'s own `_require`). */
  canRevert: boolean
  onReverted: () => void
}) {
  const client = useQueryClient()
  const queryKey = ['audit', kind, id] as const
  const trail = useQuery({ queryKey, queryFn: () => admin.auditTrail(kind, id) })
  const revert = useMutation({
    mutationFn: (actionId: string) => admin.revertAction(kind, id, actionId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey })
      onReverted()
    },
  })
  const failure =
    revert.error instanceof ApiError
      ? revert.error.message
      : revert.error
        ? 'Non riesco a ripristinare questa modifica.'
        : null

  return (
    <section aria-label="Registro delle modifiche" className="space-y-4 px-6 pb-6">
      <h2 className="text-sm font-medium">
        Registro delle modifiche
        {!!trail.data?.length && (
          <span className="ml-2 font-normal text-muted-foreground">{trail.data.length}</span>
        )}
      </h2>
      {trail.isError ? (
        <p className="text-sm text-destructive">Non riesco a leggere il registro delle modifiche.</p>
      ) : trail.isPending ? (
        <p className="text-sm text-muted-foreground">Carico…</p>
      ) : trail.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessuna modifica registrata.</p>
      ) : (
        <ol className="divide-y border text-sm">
          {trail.data.map((action) => {
            const before = action.payload.before ?? {}
            const after = action.payload.after ?? {}
            // `changed` names the field REB-347's own service touched, but is not
            // always a key of `before`/`after`: `clear_cv`'s own payload sets
            // `changed: ["cv"]` while keying its diff by `cv_filename`/`cv_mime`/
            // `cv_size` (freelancers.py's `clear_cv`). Reading the diff off the union
            // of `before` and `after`'s own keys works for both that shape and an
            // `overridden` entry's own (where `field_changes` keys them identically to
            // `changed`), rather than assuming the two always name the same keys.
            const fields = Object.keys({ ...before, ...after })
            return (
              <li key={action.id} className="space-y-2 px-4 py-3">
                <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
                  <Badge variant="pill">{KIND_LABELS[action.kind]}</Badge>
                  <span className="font-medium text-foreground">{action.admin_nome}</span>
                  <time dateTime={action.created_at}>{formatDateTime(action.created_at)}</time>
                </p>
                {fields.length > 0 && (
                  <dl className="space-y-1">
                    {fields.map((field) => (
                      <div key={field} className="grid grid-cols-[9rem_1fr] gap-2">
                        <dt className="text-muted-foreground">{FIELD_LABELS[field] ?? field}</dt>
                        <dd className="min-w-0 break-words">
                          <span className="line-through">{renderValue(before[field])}</span>
                          {' → '}
                          <span>{renderValue(after[field])}</span>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                {action.kind === 'overridden' && canRevert && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => revert.mutate(action.id)}
                    disabled={revert.isPending}
                  >
                    {revert.isPending && revert.variables === action.id
                      ? 'Ripristino…'
                      : 'Ripristina questa modifica'}
                  </Button>
                )}
              </li>
            )
          })}
        </ol>
      )}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}
