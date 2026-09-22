import { Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ApiError, type FreelancerApplication } from '@/lib/api'
import { toApplication, toUpdate, useMe, useReplaceCv, useUpdateProfile } from '@/lib/me'
import { FREELANCER_FIELDS } from '@/pages/FreelancerWizard'
import type { Field } from '@/wizard/Wizard'

/**
 * The wizard's fields, as a form: every question at once, because the person is
 * correcting and not answering for the first time. The email is not among them (it is
 * the identity the link proved). Everything else, control and rule alike, is the
 * wizard's own.
 *
 * The CV is optional here in both cases, and the hint is what differs. While we hold
 * one, `null` keeps it. When we hold none -- a card an admin wrote from a signup
 * (ORB-155), or a person who skipped the step in the wizard -- this used to be the one
 * required question on the page, which since the wizard stopped demanding a PDF would
 * only have moved the same wall one step later: somebody correcting their rate would
 * have been told to produce a CV first, and would have left with neither saved.
 */
export function editFields(hasCv: boolean): Field<FreelancerApplication>[] {
  return FREELANCER_FIELDS.filter((field) => field.id !== 'email').map((field) =>
    field.id === 'cv'
      ? {
          ...field,
          optional: true,
          hint: hasCv
            ? 'Solo se vuoi sostituirlo: un PDF, al massimo 5 MB. Altrimenti teniamo quello che abbiamo.'
            : 'Non ne abbiamo ancora uno. Un PDF, al massimo 5 MB: caricalo adesso o quando vuoi.',
        }
      : field,
  )
}

export function Modifica() {
  const me = useMe()
  const navigate = useNavigate()
  const update = useUpdateProfile()
  const replaceCv = useReplaceCv()
  const [draft, setDraft] = useState<FreelancerApplication | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string | null>(null)

  // State that follows a prop, adjusted during render: the draft starts from the
  // profile the first time it is known, and never again while the person is typing.
  if (draft === null && me.data) setDraft(toApplication(me.data))
  if (!me.data || draft === null) {
    return <p className="text-sm text-muted-foreground">Caricamento…</p>
  }
  const value = draft
  const fields = editFields(me.data.cv_filename !== null)
  const set = (patch: Partial<FreelancerApplication>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current))
  const saving = update.isPending || replaceCv.isPending

  async function save() {
    setFailure(null)
    const problems: Record<string, string> = {}
    for (const field of fields) {
      const problem = field.validate(value)
      if (problem) problems[field.id] = problem
    }
    setErrors(problems)
    if (Object.keys(problems).length) return
    let saved = false
    try {
      await update.mutateAsync(toUpdate(value))
      saved = true
      if (value.cv) await replaceCv.mutateAsync(value.cv)
      void navigate({ to: '/me' })
    } catch (error) {
      const refusal = error instanceof ApiError ? error : null
      // The PATCH and the CV replacement are two requests: when the first has already
      // gone through, a refusal on the second must not read as if nothing was saved.
      const suffix = saved ? ' Le altre risposte sono salvate.' : ''
      const known = refusal?.fields.filter((field) => fields.some((candidate) => candidate.id === field)) ?? []
      if (known.length) {
        setErrors(Object.fromEntries(known.map((field) => [field, refusal!.message + suffix])))
      } else {
        setFailure((refusal?.message ?? 'Non siamo riusciti a salvare. Riprova.') + suffix)
      }
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Correggi quello che ci hai mandato</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Ti scriviamo a <span className="font-medium text-foreground">{me.data.email}</span>: per
          cambiare indirizzo, rifai la candidatura con quello nuovo.
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
