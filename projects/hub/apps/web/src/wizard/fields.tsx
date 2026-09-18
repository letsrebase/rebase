import { Upload } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { Input } from '@rebase/ui/input'
import { Textarea } from '@rebase/ui/textarea'
import { cn } from '@rebase/ui/cn'

/** The controls a step renders: each one large, alone on its screen, focused on arrival. */

export function TextField({
  value,
  onChange,
  autoFocus,
  ...rest
}: {
  value: string
  onChange: (value: string) => void
  autoFocus?: boolean
  placeholder?: string
  type?: string
  inputMode?: 'text' | 'email' | 'decimal' | 'url'
  'aria-label': string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])
  return (
    <Input
      ref={ref}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="text-lg md:text-lg"
      {...rest}
    />
  )
}

export function LongTextField({
  value,
  onChange,
  autoFocus,
  ...rest
}: {
  value: string
  onChange: (value: string) => void
  autoFocus?: boolean
  placeholder?: string
  'aria-label': string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])
  return (
    <Textarea
      ref={ref}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      rows={6}
      className="text-base md:text-base"
      {...rest}
    />
  )
}

/** A choice made by pressing one of a few large cards: the number key selects too. */
export function ChoiceField<V extends string>({
  value,
  onChange,
  options,
  invalid,
  describedBy,
}: {
  value: V | ''
  onChange: (value: V) => void
  options: { value: V; label: string; hint?: string }[]
  invalid?: boolean
  describedBy?: string
}) {
  return (
    <div
      role="radiogroup"
      aria-invalid={invalid}
      aria-describedby={describedBy}
      className="grid gap-3 sm:grid-cols-3"
    >
      {options.map((option, index) => {
        const selected = value === option.value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex flex-col items-start gap-1 rounded-2xl border-(length:--landing-border-width) bg-card p-4 text-left transition-colors hover:bg-muted focus-visible:outline-3 focus-visible:outline-(--landing-focus) focus-visible:outline-offset-3',
              selected && 'bg-(--landing-ink) text-(--landing-cta-ink) hover:bg-(--landing-ink)',
            )}
          >
            <span className={cn('text-xs', selected ? 'text-(--landing-cta-ink)/70' : 'text-muted-foreground')}>
              {index + 1}
            </span>
            <span className="font-medium">{option.label}</span>
            {option.hint && (
              <span className={cn('text-sm', selected ? 'text-(--landing-cta-ink)/85' : 'text-muted-foreground')}>
                {option.hint}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** Several lines, one per link, so the person is never asked how many they have. */
export function LinksField({
  value,
  onChange,
  autoFocus,
  invalid,
  describedBy,
}: {
  value: string[]
  onChange: (value: string[]) => void
  autoFocus?: boolean
  invalid?: boolean
  describedBy?: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])
  return (
    <Textarea
      ref={ref}
      aria-label="Link aggiuntivi"
      aria-invalid={invalid}
      aria-describedby={describedBy}
      value={value.join('\n')}
      onChange={(event) => onChange(event.target.value.split('\n'))}
      rows={4}
      placeholder={'https://github.com/…\nhttps://il-tuo-sito.it'}
      className="text-base md:text-base"
    />
  )
}

/** The CV: drop it or pick it. A PDF, five megabytes at most, said before the server
 *  has to say it. */
export function FileField({
  value,
  onChange,
  accept,
  hint,
  invalid,
  describedBy,
}: {
  value: File | null
  onChange: (file: File | null) => void
  accept: string
  hint: ReactNode
  invalid?: boolean
  describedBy?: string
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div
      className="flex flex-col items-center gap-3 rounded-2xl border border-dashed bg-card p-8 text-center"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault()
        onChange(event.dataTransfer.files[0] ?? null)
      }}
    >
      <Upload className="size-6 text-muted-foreground" aria-hidden="true" />
      {value ? (
        <p className="text-sm">
          <span className="font-medium">{value.name}</span>{' '}
          <span className="text-muted-foreground">({Math.round(value.size / 1024)} KB)</span>
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">Trascina qui il file, oppure</p>
      )}
      <label className="cursor-pointer text-sm font-medium underline underline-offset-2 has-[:focus-visible]:outline-3 has-[:focus-visible]:outline-(--landing-focus) has-[:focus-visible]:outline-offset-3">
        {value ? 'Scegli un altro file' : 'Scegli il file'}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="sr-only"
          aria-label="CV"
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        />
      </label>
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  )
}

