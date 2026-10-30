import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { ApiError, admin, type AdminAction, type ThreadKind } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { noteSourceLabel } from '@/lib/notes'

const KIND_LABELS: Record<AdminAction['kind'], string> = {
  overridden: 'Modifica',
  cleared: 'CV rimosso',
  deleted: 'Eliminazione',
  restored: 'Ripristino',
  mail_resent: 'Mail di firma reinviata',
  document_cancelled: 'Contratto quadro annullato',
  notice_recorded: 'Disdetta registrata',
  vetted: 'Verifica',
  pigro_link: 'Collegamento a Pigro',
  merged: 'Unita a un’altra scheda',
  absorbed: 'Ha assorbito una scheda',
  unmerged: 'Unione annullata',
  note_added: 'Nota aggiunta',
  note_edited: 'Nota modificata',
  note_deleted: 'Nota eliminata',
  status_changed: 'Cambio di stato',
  anon_cv_downloaded: 'CV anonimo scaricato',
}

/** What a merge did, in one line (REB-800), for the two entries that carry its counts:
 *  `merged` on the absorbed card lists row ids per relation, `absorbed` on the one that
 *  stayed lists counts. `null` for every other kind. */
function mergeSummary(action: AdminAction): string | null {
  if (action.kind !== 'merged' && action.kind !== 'absorbed') return null
  const moved = Object.values(action.payload.moved ?? {}).reduce<number>(
    (total, rows) => total + (Array.isArray(rows) ? rows.length : rows),
    0,
  )
  const parts = [moved === 1 ? '1 riga spostata' : `${moved} righe spostate`]
  if (action.payload.cv === 'assorbita') parts.push('CV più recente passato alla scheda tenuta')
  if (action.payload.scheda_da_rigenerare) parts.push('scheda anonima da riscrivere')
  return parts.join(' · ')
}

/** The entry's word: its kind's, except `vetted` (REB-518), one kind both ways, whose
 *  payload says whether the mark went on or came off. */
function kindLabel(action: AdminAction): string {
  if (action.kind === 'vetted' && action.payload.vetted !== undefined) {
    return action.payload.vetted ? 'Segnato come verificato' : 'Verifica tolta'
  }
  if (action.kind === 'anon_cv_downloaded' && action.payload.riusato) return 'CV anonimo scaricato (già pronto)'
  return KIND_LABELS[action.kind]
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
  seniority_admin: 'Seniority (admin)',
  anni_admin: 'Anni (admin)',
  nome_azienda: 'Azienda',
  figura_richiesta: 'Figura richiesta',
  progetto: 'Progetto',
  periodo_da: 'Periodo',
  durata: 'Durata',
  budget_giornaliero: 'Budget a giornata',
  giorni_presenza: 'Giorni in sede',
  numero_risorse: 'Persone richieste',
  cv: 'CV',
}

/** The per-field half of a diff, `{}` for the plain strings a note edit and a status
 *  change carry. */
function fieldsOf(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
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
 * `CompanyRead` the way `note` is), so it fetches and reverts on its own and only
 * asks the page to refetch the record itself (`onReverted`) once a revert lands.
 */
export function AuditTrail({
  kind,
  id,
  canRevert,
  onReverted,
}: {
  kind: ThreadKind
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
            // A `note_edited` and a `status_changed` entry carry plain strings, shown
            // on their own line below; only a field diff is an object per field.
            const before = fieldsOf(action.payload.before)
            const after = fieldsOf(action.payload.after)
            const source = noteSourceLabel(action.payload.fonte)
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
                  <Badge variant="pill">{kindLabel(action)}</Badge>
                  <span className="font-medium text-foreground">{action.admin_nome}</span>
                  <time dateTime={action.created_at}>{formatDateTime(action.created_at)}</time>
                  {source && <span>{source}</span>}
                </p>
                {mergeSummary(action) && <p>{mergeSummary(action)}</p>}
                {(action.kind === 'note_added' || action.kind === 'note_deleted') && action.payload.testo && (
                  <p className="whitespace-pre-wrap break-words">{action.payload.testo}</p>
                )}
                {action.kind === 'note_edited' && (
                  <p className="break-words">
                    <span className="whitespace-pre-wrap line-through">{String(action.payload.before ?? '')}</span>
                    {' → '}
                    <span className="whitespace-pre-wrap">{String(action.payload.after ?? '')}</span>
                  </p>
                )}
                {action.kind === 'status_changed' && (
                  <p>
                    <span className="line-through">{String(action.payload.before ?? '—')}</span>
                    {' → '}
                    <span>{String(action.payload.after ?? '—')}</span>
                  </p>
                )}
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
