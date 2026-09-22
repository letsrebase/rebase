import { Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ApiError, type CompanyRequest } from '@/lib/api'
import { toCompanyApplication, toCompanyUpdate, useMe, useUpdateCompany } from '@/lib/me'
import { COMPANY_FIELDS } from '@/pages/CompanyWizard'
import type { Field } from '@/wizard/Wizard'

/**
 * The four project answers self-edit reaches (REB-314 decision): the same
 * `COMPANY_FIELDS` entries `CompanyWizard` renders, filtered to the ones a company
 * contact may change once signed in. `nome_azienda` and `referente` are left out on
 * purpose -- self-edit reaches only the signed-in person's most recent request's
 * project answers, never the company's own identity or who its referente is.
 */
export function editCompanyFields(): Field<CompanyRequest>[] {
  return COMPANY_FIELDS.filter(
    (field) => field.id === 'progetto' || field.id === 'periodo_da' || field.id === 'budget_giornaliero',
  )
}

export function ModificaAzienda() {
  const me = useMe()
  const navigate = useNavigate()
  const update = useUpdateCompany()
  const [draft, setDraft] = useState<CompanyRequest | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string | null>(null)

  // State that follows a prop, adjusted during render: the draft starts from the
  // profile the first time it is known, and never again while the person is typing.
  if (draft === null && me.data) setDraft(toCompanyApplication(me.data))
  if (!me.data || draft === null) {
    return <p className="text-sm text-muted-foreground">Caricamento…</p>
  }
  const value = draft
  const fields = editCompanyFields()
  const set = (patch: Partial<CompanyRequest>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current))
  const saving = update.isPending

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
      await update.mutateAsync(toCompanyUpdate(value))
      void navigate({ to: '/me' })
    } catch (error) {
      const refusal = error instanceof ApiError ? error : null
      // `durata` shares a field with `periodo_da` (CompanyWizard.tsx's own comment):
      // the server names the model column, the form has one input for both.
      const mapped = refusal?.fields.map((field) => (field === 'durata' ? 'periodo_da' : field)) ?? []
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
          Progetto, periodo e budget della tua richiesta più recente.
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
