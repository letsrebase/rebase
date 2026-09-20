import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { formatIsoDateItalian } from '@/lib/dates'
import type { FieldDefinition } from '@/lib/schema'

interface Props {
  field: FieldDefinition
  value: unknown
  onChange: (value: unknown) => void
  error?: string
}

const EMPTY = '—'

/** The value carried by the `select` control's "back to empty" entry.
 *
 *  Radix's `Select` refuses an item whose `value` is the empty string — that string
 *  is reserved for "nothing is selected", which is how the placeholder is shown at
 *  all — so the clear entry has to carry *some* other string, and that string must be
 *  one no tenant-defined option can ever equal, or picking a legitimate option would
 *  silently clear the field instead of setting it. A single NUL byte is provably
 *  that: `FieldDefinitionCreate.options` is `list[SafeStr]`
 *  (packages/core/src/pigrocrm/core/validation.py), and `SafeStr` rejects any string
 *  containing "\x00" outright, so this value is unreachable through the only API that
 *  can define an option. It never leaves this component either — `onValueChange`
 *  below maps it back to `null` before anything else sees it. */
const CLEAR_OPTION = '\u0000'

/** Shared by both the label above a control and the label beside a checkbox. Always
 *  the semantic `destructive` token (the darkened Watermelon step every `aria-invalid`
 *  state in `@rebase/ui` already uses, declared in `@rebase/ui/tokens.css`), never the
 *  raw brand `--color-watermelon` that AppShell and the login reserve for decorative
 *  accents: this asterisk is small body text, not a logo. How far that step clears AA
 *  as text was REB-307, answered on 2026-09-18: the token moved to the deep step and
 *  measures 5.38:1 on the page ground and 6.04:1 on a white card. */
function RequiredMark() {
  return <span className="ml-1 text-destructive">*</span>
}

/** One definition in, the right control out. This is what makes a field added at
 *  runtime work in every form without touching the frontend. */
export function DynamicFieldRenderer({ field, value, onChange, error }: Props) {
  const id = `field-${field.key}`
  const invalid = Boolean(error)

  return (
    <div className="space-y-2">
      {field.type !== 'checkbox' && (
        <Label htmlFor={id}>
          {field.label}
          {field.required && <RequiredMark />}
        </Label>
      )}

      {(() => {
        switch (field.type) {
          case 'textarea':
            return (
              <Textarea
                id={id}
                rows={4}
                aria-invalid={invalid}
                value={(value as string) ?? ''}
                onChange={(event) => onChange(event.target.value)}
              />
            )
          case 'number':
          case 'currency':
            return (
              <Input
                id={id}
                type="number"
                step={field.type === 'currency' ? '0.01' : 'any'}
                aria-invalid={invalid}
                value={(value as string) ?? ''}
                onChange={(event) => onChange(event.target.value)}
              />
            )
          case 'date':
            return (
              <Input
                id={id}
                type="date"
                aria-invalid={invalid}
                value={(value as string) ?? ''}
                onChange={(event) => onChange(event.target.value)}
              />
            )
          case 'url':
            return (
              <Input
                id={id}
                type="url"
                placeholder="https://"
                aria-invalid={invalid}
                value={(value as string) ?? ''}
                onChange={(event) => onChange(event.target.value)}
              />
            )
          case 'checkbox':
            return (
              <div className="flex items-center gap-2">
                <Checkbox
                  id={id}
                  aria-invalid={invalid}
                  checked={Boolean(value)}
                  onCheckedChange={(checked) => onChange(checked === true)}
                />
                <Label htmlFor={id} className="font-normal">
                  {field.label}
                  {field.required && <RequiredMark />}
                </Label>
              </div>
            )
          case 'select':
            return (
              // The listbox has to offer a way back to empty, not only the defined
              // options: a `select` that has been set once is otherwise impossible to
              // clear anywhere in the UI, and the backend has no problem clearing one
              // (a custom field's key sent as `null` removes it -- confirmed against
              // the running API). The clear entry is offered unconditionally, on a
              // required field too: refusing it here would be a client-side
              // re-implementation of a rule the server already owns, and a required
              // field cleared this way comes back as the server's own "campo
              // obbligatorio" on this very control.
              <Select
                value={(value as string) ?? ''}
                onValueChange={(next) => onChange(next === CLEAR_OPTION ? null : next)}
              >
                <SelectTrigger id={id} aria-invalid={invalid} className="w-full">
                  <SelectValue placeholder="Seleziona…" />
                </SelectTrigger>
                <SelectContent>
                  {field.options.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                  <SelectSeparator />
                  <SelectItem value={CLEAR_OPTION}>Nessuna selezione</SelectItem>
                </SelectContent>
              </Select>
            )
          case 'multiselect': {
            const selected = Array.isArray(value) ? (value as string[]) : []
            return (
              <div aria-invalid={invalid} className="flex flex-wrap gap-3 rounded-md border p-3 aria-invalid:border-destructive">
                {field.options.map((option) => (
                  <label key={option} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={selected.includes(option)}
                      onCheckedChange={(checked) =>
                        onChange(
                          checked === true
                            ? [...selected, option]
                            : selected.filter((item) => item !== option),
                        )
                      }
                    />
                    {option}
                  </label>
                ))}
              </div>
            )
          }
          default:
            return (
              <Input
                id={id}
                aria-invalid={invalid}
                value={(value as string) ?? ''}
                onChange={(event) => onChange(event.target.value)}
              />
            )
        }
      })()}

      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}


/** The read-only counterpart, used by tables and detail panels.
 *
 *  The absence check is intentionally `=== null || === undefined || === ''`, never
 *  a falsiness check (`!value`): `0`, `false` and the currency string `"0.00"` are
 *  all real, present values — `!0` is `true` in JavaScript, which would render a
 *  deal worth nothing exactly like a deal nobody has priced yet. The backend made
 *  this same mistake once, in `is_blank` (fields/validator.py) — it now special-
 *  cases `False`/`0` as present for exactly this reason, and this function has to
 *  agree with it or the same value would read as "empty" in a table and "provided"
 *  everywhere else. An empty array gets the same treatment for `multiselect`: the
 *  backend never actually persists `[]` (an empty selection is "absent" there too,
 *  see `is_blank`), but a caller handing this function a fresh `[]` before that
 *  round-trip should not see "" where every other empty state reads as "—".
 *
 *  `checkbox` is the one type that never reaches that absence check at all, because
 *  it is the one type with no third state to report. A yes/no question that nobody
 *  answered is a "no" — the same thing a stored `false` says — so a dash there would
 *  be inventing a distinction the control itself cannot express: the form draws an
 *  unchecked box either way. This is deliberately a *rendering* rule and not a
 *  data-backfilling one: writing `false` into every record that lacks the key would
 *  put a value in the database the user never chose (see `DynamicForm`'s `mode` prop
 *  for where that line is drawn), while reading absence as "No" changes nothing and
 *  is correct for a record created before the field existed, one created through the
 *  API, and one created through this UI alike. */
export function renderFieldValue(field: FieldDefinition, value: unknown): string {
  if (field.type === 'checkbox') return value ? 'Sì' : 'No'
  if (value === null || value === undefined || value === '') return EMPTY
  if (Array.isArray(value) && value.length === 0) return EMPTY

  switch (field.type) {
    // `useGrouping: 'always'` is load-bearing, not decoration: the ICU data backing
    // `Intl.NumberFormat` resolves `it-IT`'s *default* ("auto") grouping to CLDR's
    // "min2" strategy, which withholds the thousands separator until the integer
    // part has five digits or more (verified directly: plain `Intl.NumberFormat
    // ('it-IT').format(1234)` renders "1234", no separator, on this stack's Node/ICU
    // version, while `9999 -> "9999"` and `12345 -> "12.345"` show exactly where the
    // cutover sits). Every Italian reader still expects "1.234,56 €" starting at four
    // digits, so grouping is forced on rather than left to that cutover.
    case 'currency':
      return new Intl.NumberFormat('it-IT', {
        style: 'currency',
        currency: 'EUR',
        useGrouping: 'always',
      }).format(Number(value))
    case 'number':
      return new Intl.NumberFormat('it-IT', { useGrouping: 'always' }).format(Number(value))
    case 'date':
      return formatIsoDateItalian(String(value))
    case 'multiselect':
      return Array.isArray(value) ? value.join(', ') : String(value)
    default:
      return String(value)
  }
}
