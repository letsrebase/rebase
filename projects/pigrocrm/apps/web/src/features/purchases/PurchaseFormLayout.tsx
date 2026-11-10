import { useEffect, type ReactNode } from 'react'
import { DynamicFieldRenderer } from '@/components/DynamicFieldRenderer'
import { cn } from '@rebase/ui/cn'
import { Label } from '@rebase/ui/label'
import type { FieldDefinition } from '@/lib/schema'

/**
 * The layout of the purchase form (the approved brief of 2026-11-10: «forms two-column with the
 * labels beside the fields»): the label on the left and the field on the right, two rows to a
 * line, and the label over the field only when the form is narrow.
 *
 * «Narrow» is the width the form has, not the viewport's: the file of «In arrivo» sits beside it
 * from `lg`, which leaves the form a column of its own, so every breakpoint here is a container
 * query on `FormGrid` (`@md`: label beside the field, `@2xl`: two rows to a line). On a phone, or
 * beside the file, a row is read one label over one field and never overflows.
 */

/** A required mark, the one `DynamicFieldRenderer` draws, for the rows this form draws itself. */
export function Required() {
  return (
    <span className="ml-1 text-destructive" aria-hidden="true">
      *
    </span>
  )
}

/** The grid the rows live in. It is the container the breakpoints below ask about. */
export function FormGrid({ children }: { children: ReactNode }) {
  return (
    <div className="@container min-w-0">
      <div className="grid gap-x-10 gap-y-4 @2xl:grid-cols-2">{children}</div>
    </div>
  )
}

/** A row that takes the whole line: the recap, a long text, a sentence. */
export function WideRow({ children }: { children: ReactNode }) {
  return <div className="min-w-0 @2xl:col-span-2">{children}</div>
}

/**
 * One labelled row of a control this form draws itself (a picker, a select). The label is a
 * real `<label htmlFor>`; `hint` and `error` sit under the control, in the field's column.
 */
export function FormRow({
  label,
  htmlFor,
  required = false,
  hint,
  error,
  wide = false,
  children,
}: {
  label: string
  htmlFor?: string
  required?: boolean
  hint?: ReactNode
  error?: ReactNode
  wide?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        'grid min-w-0 gap-x-4 gap-y-2 @md:grid-cols-[minmax(0,8rem)_minmax(0,1fr)]',
        wide && '@2xl:col-span-2',
      )}
    >
      <Label htmlFor={htmlFor} className="@md:min-h-9">
        {label}
        {required && <Required />}
      </Label>
      <div className="min-w-0 space-y-2">
        {children}
        {hint}
        {error}
      </div>
    </div>
  )
}

/**
 * Puts the label of a `DynamicFieldRenderer` beside its control from `@md`. The renderer draws
 * its label above the control and is shared with every other form, so the placement is done from
 * outside: its root is a grid of two columns, the label (`data-slot="label"`) in the first, the
 * control and the problem line under it in the second. A checkbox has no label of its own above
 * it, only the one beside the box, and lands in the field column like every other control.
 */
const LABEL_BESIDE =
  '@md:[&>div]:grid @md:[&>div]:grid-cols-[minmax(0,8rem)_minmax(0,1fr)] @md:[&>div]:gap-x-4 @md:[&>div]:gap-y-2 ' +
  '@md:[&>div>*]:m-0 ' +
  '@md:[&>div>[data-slot=label]]:col-start-1 @md:[&>div>[data-slot=label]]:row-start-1 @md:[&>div>[data-slot=label]]:min-h-9 ' +
  '@md:[&>div>:not([data-slot=label])]:col-start-2'

/**
 * The rows of a list of field definitions, native and custom, in the form's grid: what
 * `DynamicForm` draws, with the label beside the control and two rows to a line. A long text and
 * a multiple choice take the whole line.
 *
 * On create, and only on create, a checkbox nobody touched is still an answer and is written as
 * `false` (the reason `DynamicForm` gives for the same effect: an absent key and an unchecked box
 * must not be two different things in the stored data). Edit leaves it alone.
 */
export function FieldRows({
  fields,
  values,
  onChange,
  problem,
  mode,
}: {
  fields: FieldDefinition[]
  values: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
  /** The server's refusal, when it names one of these fields. */
  problem: { field: string; message: string } | null
  mode: 'create' | 'edit'
}) {
  useEffect(() => {
    if (mode !== 'create') return
    for (const field of fields) {
      const current = values[field.key]
      if (field.type === 'checkbox' && (current === undefined || current === null)) {
        onChange(field.key, false)
      }
    }
  }, [fields, values, onChange, mode])

  return fields.map((field) => (
    <div
      key={field.key}
      className={cn(
        'min-w-0',
        LABEL_BESIDE,
        (field.type === 'textarea' || field.type === 'multiselect') && '@2xl:col-span-2',
      )}
    >
      <DynamicFieldRenderer
        field={field}
        value={values[field.key] ?? null}
        onChange={(value) => onChange(field.key, value)}
        error={problem?.field === field.key ? problem.message : undefined}
      />
    </div>
  ))
}
