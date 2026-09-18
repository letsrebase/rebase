import { useEffect } from 'react'
import { DynamicFieldRenderer } from './DynamicFieldRenderer'
import { fieldErrorFrom, type ProblemDetail } from '@/lib/api'
import type { FieldDefinition } from '@/lib/schema'
import { cn } from '@rebase/ui/cn'

interface Props {
  fields: FieldDefinition[]
  values: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
  problem?: ProblemDetail | null
  /**
   * Whether this form is composing a record that does not exist yet, or editing one
   * that does. Required, with no default, on purpose: the only thing it controls is
   * whether the form is allowed to put a value into the caller's state that the user
   * never typed (see the checkbox seeding below), and a component that silently
   * writes data must never let a call site acquire that behaviour by forgetting a
   * prop. Persons and Deals will each have to answer the question explicitly.
   */
  mode: 'create' | 'edit'
}

/**
 * The message for a problem this form cannot pin on any control it renders.
 *
 * Returning `null` means "some rendered field is already showing this", which is the
 * common case and needs nothing else. Every other case used to be swallowed
 * completely: `fieldErrorFrom` names a field, the form attaches the error only to a
 * field it actually renders, and if no such field exists the user got a dialog that
 * did nothing when they pressed Salva -- no error, no toast, no clue. That is not a
 * corner case: a field definition archived while a record still carries a value for
 * it produces exactly this shape (`{code: 'validation_failed', field: '<archived
 * key>'}`, for a key no longer in the schema and therefore rendered nowhere), and so
 * does any non-field problem the caller hands over -- a 409, a 500, an expired
 * session.
 *
 * The text is the server's own, never a client-side re-wording of it; the field name
 * is prefixed only when the server named one, so a message about a control that is
 * not on screen still says which field it is about.
 */
function unattributedMessage(
  problem: ProblemDetail,
  fields: FieldDefinition[],
): string | null {
  const fieldError = fieldErrorFrom(problem)
  if (fieldError === null) return problem.detail
  if (fields.some((field) => field.key === fieldError.field)) return null
  return `${fieldError.field}: ${fieldError.message}`
}

/** Errors come from the API's problem document, never from a client-side rule.
 *  Duplicating validation here is how the web app and the MCP server start
 *  disagreeing about what is valid. `fieldErrorFrom` is the one place that decides
 *  which field a problem document is about; this component only asks it and
 *  attaches the answer to the matching control — at most one field is ever
 *  highlighted, because a single request can only fail on one field at a time.
 *  Anything it cannot attach is shown above the fields instead (see
 *  `unattributedMessage`), so no server message can go missing. */
export function DynamicForm({ fields, values, onChange, problem, mode }: Props) {
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const formError = problem ? unattributedMessage(problem, fields) : null

  // On create, and only on create, an unchecked checkbox nobody touched is still an
  // answer: the user looked at that box in the dialog they were filling in and
  // submitted it unchecked, so `false` is an honest record of what they sent. Nothing
  // writes to a checkbox's slot in `values` until it is clicked, so without this the
  // key was omitted from the POST entirely and "unchecked" and "no opinion" became
  // indistinguishable in the stored data.
  //
  // On edit it is the opposite, and doing it there was a real bug: an edit that only
  // changed Telefono also persisted `vip: false` on a record that had never had a
  // value for it -- data the user never chose, written as a side effect of touching
  // an unrelated field. Untouched native columns are not rewritten either; an
  // untouched checkbox gets the same respect. The reason this costs nothing is that
  // the *rendering* rule now carries the meaning instead: `renderFieldValue` reads an
  // absent checkbox as "No", identically to a stored `false`, in the table and in the
  // detail view, and the control here draws it as an unchecked box either way. A
  // checkbox the user actually toggles is a different thing entirely and is sent as
  // whatever they chose, `false` included -- `isBlank` (CustomerForm) treats `false`
  // as a value, never as a blank.
  //
  // Idempotent by construction: once the value is present this loop calls nothing, so
  // it settles after exactly one extra render and cannot cycle. It has to be an
  // effect and not a render-time computation because the value belongs to the
  // caller's state -- this component is controlled and owns none of it -- and it has
  // to re-run when `values` changes rather than only on mount, because a dialog that
  // stays mounted across opens (see CustomerForm) resets its values without ever
  // unmounting this form.
  useEffect(() => {
    if (mode !== 'create') return
    for (const field of fields) {
      const current = values[field.key]
      if (field.type === 'checkbox' && (current === undefined || current === null)) {
        onChange(field.key, false)
      }
    }
  }, [fields, values, onChange, mode])

  return (
    <div className="space-y-5">
      {formError && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {formError}
        </p>
      )}

      <div className="grid gap-5 sm:grid-cols-2">
        {fields.map((field) => (
          <div
            key={field.key}
            className={cn((field.type === 'textarea' || field.type === 'multiselect') && 'sm:col-span-2')}
          >
            <DynamicFieldRenderer
              field={field}
              value={values[field.key] ?? null}
              onChange={(value) => onChange(field.key, value)}
              error={fieldError?.field === field.key ? fieldError.message : undefined}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
