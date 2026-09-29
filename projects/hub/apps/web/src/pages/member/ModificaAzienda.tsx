import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ApiError, type CompanyRequest } from '@/lib/api'
import { toCompanyApplication, toCompanyUpdate, useMe, useUpdateCompany } from '@/lib/me'
import { COMPANY_FIELDS } from '@/pages/CompanyWizard'
import type { Field } from '@/wizard/Wizard'

/**
 * The seven project answers self-edit reaches (REB-314 decision; REB-380 widens it
 * from three to seven): the same `COMPANY_FIELDS` entries `CompanyWizard` renders,
 * filtered to the ones a company contact may change once signed in. `nome_azienda`,
 * `referente_*`/`email` and `telefono` are left out on purpose -- self-edit reaches
 * only one of the signed-in person's own requests' project answers, never the
 * company's own identity or who its referente is. `giorni_presenza` has no field of
 * its own; it travels with `remoto`, whose render already carries it.
 */
export function editCompanyFields(): Field<CompanyRequest>[] {
  return COMPANY_FIELDS.filter(
    (field) =>
      field.id === 'progetto' ||
      field.id === 'periodo_da' ||
      field.id === 'budget_giornaliero' ||
      field.id === 'remoto' ||
      field.id === 'numero_risorse' ||
      field.id === 'figura_richiesta',
  )
}

/** One of the signed-in person's own requests, by the `$id` of `/me/edit-company/$id`
 *  (REB-602), or their newest one on `/me/edit-company` with no id, the address older
 *  links still use. The request shown is the one saved: its id is resolved once (the
 *  address's, or the newest at the first read of the list) and travels with the `PATCH`,
 *  so a request filed in another tab in the meantime never changes which one this saves.
 *
 *  An id that is not in the person's list (somebody else's, deleted by an admin, made
 *  up) or a person with no request at all would otherwise let the whole form be built
 *  from blank fields before the `PATCH` refuses it with a 404 (the same shape of bug
 *  Greptile found on `NuovaRichiestaAzienda.tsx`, PR #313; REB-383 is this page's own
 *  fix): the same mount-time redirect `AdminGuard` uses for its own access rule bounces
 *  it back to `/me` before the form ever renders. */
export function ModificaAzienda() {
  const { id } = useParams({ strict: false }) as { id?: string }
  // Keyed by the address: a jump from one request's page straight to another's (the
  // router keeps the component mounted across a change of `$id`) starts the form over,
  // instead of saving the first request's draft under the second one's id.
  return <EditRequest key={id ?? 'newest'} id={id} />
}

function EditRequest({ id }: { id: string | undefined }) {
  const me = useMe()
  const navigate = useNavigate()
  const update = useUpdateCompany()
  const [draft, setDraft] = useState<CompanyRequest | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [newestId, setNewestId] = useState<string | undefined>(undefined)
  const requests = me.data?.richieste
  // With no id in the address the newest request is pinned the first time the list is
  // known, like the draft below: a list refetched after a request filed in another tab
  // has a new newest, and the draft on screen still belongs to the one pinned here.
  if (id === undefined && newestId === undefined && requests?.[0]) setNewestId(requests[0].id)
  const wanted = id ?? newestId
  const target = requests?.find((item) => item.id === wanted)
  const missing =
    requests !== undefined && target === undefined && (wanted !== undefined || requests.length === 0)

  useEffect(() => {
    if (missing) void navigate({ to: '/me', replace: true })
  }, [missing, navigate])

  if (missing) return null

  // State that follows a prop, adjusted during render: the draft starts from the
  // request the first time it is known, and never again while the person is typing.
  if (draft === null && target) setDraft(toCompanyApplication(target))
  if (!target || draft === null) {
    return <p className="text-sm text-muted-foreground">Caricamento…</p>
  }
  const value = draft
  const fields = editCompanyFields()
  const set = (patch: Partial<CompanyRequest>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current))
  const saving = update.isPending
  const requestId = target.id

  async function save() {
    setFailure(null)
    const problems: Record<string, string> = {}
    for (const field of fields) {
      const problem = field.validate(value)
      if (problem) problems[field.id] = problem
    }
    setErrors(problems)
    if (Object.keys(problems).length) return
    try {
      await update.mutateAsync({ id: requestId, data: toCompanyUpdate(value) })
      void navigate({ to: '/me' })
    } catch (error) {
      const refusal = error instanceof ApiError ? error : null
      // `durata` shares a field with `periodo_da`, `giorni_presenza` with `remoto`
      // (CompanyWizard.tsx's own comment): the server names the model column, the
      // form has one input -- or one step -- for both.
      const mapped =
        refusal?.fields.map((field) =>
          field === 'durata' ? 'periodo_da' : field === 'giorni_presenza' ? 'remoto' : field,
        ) ?? []
      const known = mapped.filter((field) => fields.some((candidate) => candidate.id === field))
      if (known.length) {
        setErrors(Object.fromEntries(known.map((field) => [field, refusal!.message])))
      } else {
        setFailure(refusal?.message ?? 'Non siamo riusciti a salvare. Riprova.')
      }
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Correggi la tua richiesta</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Progetto, periodo e budget della richiesta «{target.figura_richiesta}».
        </p>
      </div>

      {fields.map((field) => {
        const error = errors[field.id] ?? null
        const errorId = `${field.id}-error`
        return (
          <section key={field.id} className="space-y-3" aria-labelledby={`edit-${field.id}`}>
            <div>
              <h2 id={`edit-${field.id}`} className="text-lg font-semibold tracking-tight">
                {field.label}
                {field.optional && (
                  <span className="ml-2 text-sm font-normal text-muted-foreground">(facoltativo)</span>
                )}
              </h2>
              {field.hint && <p className="mt-1 text-sm text-muted-foreground">{field.hint}</p>}
            </div>
            {field.render({
              value,
              set,
              next: () => void save(),
              error,
              autoFocus: false,
              errorId,
            })}
            {error && (
              <p role="alert" id={errorId} className="text-sm text-destructive">
                {error}
              </p>
            )}
          </section>
        )
      })}

      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex items-center justify-between">
        <Button asChild variant="ghost">
          <Link to="/me">Annulla</Link>
        </Button>
        <Button type="button" onClick={() => void save()} disabled={saving}>
          {saving ? 'Salvo…' : 'Salva'}
        </Button>
      </div>
    </div>
  )
}
