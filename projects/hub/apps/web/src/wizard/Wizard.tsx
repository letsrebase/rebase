import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import './site-controls.css'

/**
 * One question per screen, the shape Typeform and Tally made familiar: a progress bar,
 * the question large, one control, Enter to go on, Back always there, a review at the
 * end. The engine knows nothing about what is asked -- a screen is a title and the
 * fields it holds, a field is a render function, a validator and a name for the
 * review -- so the two wizards share every keystroke and differ only in their fields.
 *
 * A field is the question: one answer, one row in the review, in the member area and
 * on the edit page. A screen is what the wizard shows at once: today always one field,
 * since grouping several onto one screen is REB-120/121's job, not this file's.
 */
export interface Field<T> {
  id: string
  /** The question, as a sentence: the review row, the profile row and the edit
   *  page's own heading all show this text. */
  label: string
  hint?: string
  /** `true` for a field whose empty answer is fine; the review says «—» for it. */
  optional?: boolean
  render: (props: FieldRenderProps<T>) => ReactNode
  /** A sentence when the answer cannot go on, `null` when it can. Runs on «Avanti» and
   *  on Enter, never on every keystroke: nobody wants to be told they are wrong while
   *  they are still typing. */
  validate: (value: T) => string | null
  /** What the review shows for this field. */
  summary: (value: T) => string
}

export interface FieldRenderProps<T> {
  value: T
  set: (patch: Partial<T>) => void
  /** Hands the field's own submit (a multi-line control, a file picker) to the engine. */
  next: () => void
  error: string | null
  autoFocus: boolean
  /** The id the error paragraph will carry while `error` is set, already computed so
   *  the field's own control can wire `aria-describedby` to it -- combined with a
   *  describedby of its own, as the LinkedIn field does with its prefix span -- and
   *  `aria-invalid` from `error !== null`. Point a control at it only while `error` is
   *  set: a reference to an id that is not on the page is worse than none. */
  errorId: string
}

/** One screen the wizard walks: its own title and hint, and the fields it validates
 *  together before moving on. */
export interface Screen<T> {
  id: string
  title: string
  hint?: string
  fields: Field<T>[]
}

/** Wraps every field of `fields` in its own single-field screen, in the given order:
 *  the shape every screen has until REB-120/121 group some of them onto fewer
 *  screens. A field's `label` becomes that screen's `title`, and its `hint` the
 *  screen's own, so a one-field screen looks exactly as it did before the split. */
export function screensFromFields<T>(fields: Field<T>[]): Screen<T>[] {
  return fields.map((field) => ({ id: field.id, title: field.label, hint: field.hint, fields: [field] }))
}

export function Wizard<T>({
  title,
  screens,
  value,
  set,
  onSubmit,
  submitting,
  submitError,
  submitLabel,
  onStep,
  initialIndex = 0,
  onIndexChange,
  intro,
}: {
  title: string
  screens: Screen<T>[]
  value: T
  set: (patch: Partial<T>) => void
  onSubmit: () => void
  submitting: boolean
  /** An error from the server, shown on the review screen; when it names a field by id
   *  the engine finds that field's screen, jumps back to it and shows the message
   *  under that field's own control. An id that names no field (REB-243: `cognome`,
   *  say, which has no field of its own) never suppresses the review's own alert --
   *  the one moment a person is most likely to abandon is a submit that answers with
   *  silence. */
  submitError: { message: string; field?: string } | null
  submitLabel: string
  /** Called with the screen on screen and how many there are, whenever it changes: the
   *  first one on mount, the review as `screens.length`, a jump back on a server error
   *  too. Memoise it, or it fires on every render. */
  onStep?: (index: number, total: number) => void
  /** Where to start: the screen a draft was left at (`wizard/draft.ts`), clamped to the
   *  review, so a draft written by a longer version of the form still opens. */
  initialIndex?: number
  /** Every screen change, the first one included, for whoever keeps the draft. */
  onIndexChange?: (index: number) => void
  /** Shown above the first question only: what this is and how long it takes, for the
   *  person who arrived from an ad and is asked their name before anything else. */
  intro?: ReactNode
}) {
  const [index, setIndex] = useState(() => Math.min(Math.max(0, initialIndex), screens.length))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [focusId, setFocusId] = useState<string | null>(null)
  const review = index === screens.length
  const screen = screens[index]
  const fields = screens.flatMap((candidate) => candidate.fields)
  const [handled, setHandled] = useState<{ message: string; field?: string } | null>(null)

  useEffect(() => {
    onStep?.(index, screens.length)
  }, [index, screens.length, onStep])
  useEffect(() => {
    onIndexChange?.(index)
  }, [index, onIndexChange])

  // A server error that names a field sends the person back to its screen, once per
  // error. Keyed on the error object itself, not its text: `submit()` makes a fresh
  // object every attempt, so a second refusal with the same words (the address is
  // still taken, say) still gets handled rather than silently matching the first
  // one's `handled` and never jumping again. State adjusted during render, the way
  // React asks for "state that follows a prop", rather than in an effect that would
  // paint the review first and jump a frame later. An id that names no field is left
  // alone here: the review's own alert below shows it instead (REB-243).
  if (submitError?.field && handled !== submitError) {
    const at = screens.findIndex((candidate) => candidate.fields.some((field) => field.id === submitError.field))
    if (at >= 0) {
      setHandled(submitError)
      setIndex(at)
      setErrors({ [submitError.field]: submitError.message })
      setFocusId(submitError.field)
    }
  }
  // Whether the review's own alert should show the message: the error carries no
  // field id, or one that matches nothing here (REB-243).
  const reviewError = submitError && !fields.some((field) => field.id === submitError.field)

  function next() {
    if (!screen) return
    const problems: Record<string, string> = {}
    for (const field of screen.fields) {
      const problem = field.validate(value)
      if (problem) problems[field.id] = problem
    }
    const firstFailed = screen.fields.find((field) => problems[field.id] !== undefined)
    if (firstFailed) {
      setErrors(problems)
      setFocusId(firstFailed.id)
      return
    }
    setErrors({})
    setFocusId(null)
    setIndex((current) => current + 1)
  }

  function back() {
    setErrors({})
    setFocusId(null)
    setIndex((current) => Math.max(0, current - 1))
  }

  function editField(fieldId: string) {
    setErrors({})
    setFocusId(null)
    setIndex(screens.findIndex((candidate) => candidate.fields.some((field) => field.id === fieldId)))
  }

  const progress = Math.round((Math.min(index, screens.length) / screens.length) * 100)

  return (
    <div
      className="mx-auto flex w-full max-w-2xl flex-col gap-8"
      onKeyDown={(event) => {
        // Enter goes on, except inside a textarea (where it is a newline) or on a
        // button (where it is a click). Shift+Enter always means a newline.
        if (event.key !== 'Enter' || event.shiftKey || review) return
        const target = event.target as HTMLElement
        if (target.tagName === 'TEXTAREA' || target.tagName === 'BUTTON') return
        event.preventDefault()
        next()
      }}
    >
      {/* The page's single accessible heading (ORB-89): the design keeps the step
          question as an `<h2>` and the review screen's «Tutto giusto?» as another,
          so this names the wizard itself rather than promoting either -- visually
          hidden because the breadcrumb below already shows the same title on
          screen. That breadcrumb span carries `aria-hidden` so a screen reader
          hears the title once, from this heading, rather than twice. */}
      <h1 className="sr-only">{title}</h1>
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span aria-hidden="true">{title}</span>
          <span aria-live="polite">
            {review ? 'Riepilogo' : `${index + 1} di ${screens.length}`}
          </span>
        </div>
        <div
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Avanzamento"
          className="h-2 w-full overflow-hidden border-(length:--landing-border-width)"
        >
          <div
            className="h-full bg-(--landing-ink) transition-[width] duration-300"
            style={{ width: `${review ? 100 : progress}%` }}
          />
        </div>
      </div>

      {intro && index === 0 && <div>{intro}</div>}

      {review ? (
        <section className="space-y-6" aria-label="Riepilogo">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Tutto giusto?</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Rileggi le risposte: puoi tornare indietro su qualsiasi punto.
            </p>
          </div>
          <dl className="divide-y border bg-card">
            {fields.map((field) => (
              <div
                key={field.id}
                className="flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-start sm:gap-4"
              >
                <dt className="text-muted-foreground sm:w-40 sm:shrink-0">{field.label}</dt>
                {/* axe's definition-list rule runs only-dlitems, which flattens this row's
                    role-less wrapping div and then rejects any direct child that is not a
                    dt or a dd (a button, a span, even a stray text node); it never descends
                    into a dd, so the "Modifica" control lives inside one instead of beside
                    dt/dd. Nesting it here keeps the row spacing and wrap identical (REB-96). */}
                <dd className="flex flex-col gap-1 sm:min-w-0 sm:flex-1 sm:flex-row sm:items-start sm:gap-4">
                  <span className="break-words font-medium sm:min-w-0 sm:flex-1">
                    {field.summary(value) || '—'}
                  </span>
                  <button
                    type="button"
                    className="mt-1 self-start text-xs text-muted-foreground underline-offset-2 hover:underline sm:mt-0 sm:shrink-0"
                    onClick={() => editField(field.id)}
                  >
                    Modifica
                  </button>
                </dd>
              </div>
            ))}
          </dl>
          {reviewError && (
            <p role="alert" className="text-sm text-destructive">
              {submitError!.message}
            </p>
          )}
          <div className="flex items-center justify-between">
            <Button type="button" variant="outline" onClick={back} disabled={submitting}>
              Indietro
            </Button>
            <Button type="button" onClick={onSubmit} disabled={submitting}>
              {submitting ? 'Invio…' : submitLabel}
            </Button>
          </div>
        </section>
      ) : screen ? (
        <section key={screen.id} className="space-y-6" aria-labelledby={`screen-${screen.id}`}>
          <div>
            <h2 id={`screen-${screen.id}`} className="text-2xl font-semibold tracking-tight">
              {screen.title}
              {screen.fields.every((field) => field.optional) && (
                <span className="ml-2 text-base font-normal text-muted-foreground">
                  (facoltativo)
                </span>
              )}
            </h2>
            {screen.hint && <p className="mt-1 text-sm text-muted-foreground">{screen.hint}</p>}
          </div>
          {screen.fields.map((field) => {
            const error = errors[field.id] ?? null
            const errorId = `${field.id}-error`
            return (
              <div key={`${field.id}:${error ? 'invalid' : 'valid'}`} className="space-y-2">
                <div>
                  {field.render({
                    value,
                    set,
                    next,
                    error,
                    autoFocus: focusId ? focusId === field.id : field.id === screen.fields[0]!.id,
                    errorId,
                  })}
                </div>
                {error && (
                  <p role="alert" id={errorId} className="text-sm text-destructive">
                    {error}
                  </p>
                )}
              </div>
            )
          })}
          <div className="flex items-center justify-between">
            <Button
              type="button"
              variant="outline"
              onClick={back}
              className={cn(index === 0 && 'invisible')}
            >
              Indietro
            </Button>
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-muted-foreground sm:inline">
                Invio ↵ per continuare
              </span>
              <Button type="button" onClick={next}>
                {index === screens.length - 1 ? 'Rivedi' : 'Avanti'}
              </Button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  )
}
