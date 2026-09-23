import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ApiError, type CompanyRequest } from '@/lib/api'
import { toCompanyUpdate, useCreateCompanyRequest, useMe } from '@/lib/me'
import { COMPANY_FIELDS, EMPTY } from '@/pages/CompanyWizard'
import type { Field } from '@/wizard/Wizard'

/**
 * Every `COMPANY_FIELDS` entry but the company's own identity (`nome_azienda`,
 * `referente`) -- REB-381's own filter, distinct from `ModificaAzienda.tsx`'s
 * `editCompanyFields()`: that one changes a request the referente already answered,
 * this one starts a request that has no earlier answers to fall back on, so every
 * project question (`durata` included, wherever it travels with another field's
 * render) is asked fresh rather than pre-filled from the newest request.
 */
export function newCompanyFields(): Field<CompanyRequest>[] {
  return COMPANY_FIELDS.filter((field) => field.id !== 'nome_azienda' && field.id !== 'referente')
}

/** A signed-in referente files a brand-new request without leaving the member area
 *  (REB-381): the company's name and their own identity are never asked again --
 *  `POST /me/company` reads the name off the newest request already on file -- only
 *  the project itself is. Same flat-form shape as `ModificaAzienda.tsx`, starting
 *  from a blank draft instead of the newest request's answers. Reachable only when
 *  `ha_azienda` is true -- `Area.tsx`'s own CTA is the only door here, but a direct
 *  visit with no company yet would otherwise let the whole form be filled before
 *  `POST /me/company` refuses it with a 404 (Greptile, PR #313): the same
 *  mount-time redirect `AdminGuard` uses for its own access rule bounces it back to
 *  `/me` before the form ever renders. */
export function NuovaRichiestaAzienda() {
  const navigate = useNavigate()
  const me = useMe()
  const create = useCreateCompanyRequest()
  const [value, setValue] = useState<CompanyRequest>(EMPTY)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string | null>(null)
  const fields = newCompanyFields()
  const set = (patch: Partial<CompanyRequest>) =>
    setValue((current) => ({ ...current, ...patch }))
  const saving = create.isPending
  const hasCompany = me.data?.ha_azienda ?? true

  useEffect(() => {
    if (me.data && !me.data.ha_azienda) void navigate({ to: '/me', replace: true })
  }, [me.data, navigate])

  if (!hasCompany) return null

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
      await create.mutateAsync(toCompanyUpdate(value))
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
        setFailure(refusal?.message ?? 'Non siamo riusciti a inviare la richiesta. Riprova.')
      }
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Richiedi una nuova figura</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Azienda e contatto restano quelli che conosciamo già: raccontaci solo il nuovo progetto.
        </p>
      </div>

      {fields.map((field) => {
        const error = errors[field.id] ?? null
        const errorId = `${field.id}-error`
        return (
          <section key={field.id} className="space-y-3" aria-labelledby={`new-${field.id}`}>
            <div>
              <h2 id={`new-${field.id}`} className="text-lg font-semibold tracking-tight">
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
          {saving ? 'Invio…' : 'Invia la richiesta'}
        </Button>
      </div>
    </div>
  )
}
