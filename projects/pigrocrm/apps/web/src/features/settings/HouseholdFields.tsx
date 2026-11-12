import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  MEMBERS_MAX,
  MemberKinds,
  newMember,
  type HouseholdDraft,
  type HouseholdMode,
  type MemberDraft,
  type MemberKind,
} from './fiscalModel'

const SELECT = 'border-input bg-background h-9 w-full rounded-md border px-3 text-sm'

const MODES: readonly { value: HouseholdMode; label: string }[] = [
  { value: 'unstated', label: 'Non indicati' },
  { value: 'none', label: 'Nessuno' },
  { value: 'listed', label: 'Li indico sotto' },
]

/**
 * The ordinary regime's household (REB-1087): who is fiscally dependent on the taxpayer, for
 * the IRPEF's family detractions. It holds no figure of the law: the ceilings in the hint are
 * the sentence a person reads to decide whom to list, and the arithmetic is the server's.
 */
export function HouseholdFields({
  household,
  disabled,
  error,
  onChange,
}: {
  household: HouseholdDraft
  disabled: boolean
  error: string | null
  onChange: (next: HouseholdDraft) => void
}) {
  const listed = household.mode === 'listed'
  const stated = household.mode !== 'unstated'
  const hasSpouse = household.members.some((member) => member.kind === 'spouse')
  const anyDisabled = listed && household.members.some((member) => member.disabled)

  function patchMember(key: string, patch: Partial<MemberDraft>) {
    onChange({
      ...household,
      members: household.members.map((member) =>
        member.key === key ? { ...member, ...patch } : member,
      ),
    })
  }

  // A kind that changes takes the shares its new kind allows: a spouse is the whole
  // detraction, a child is 100 or 50, and an ascendant keeps whatever 1 to 100 was typed.
  function pickKind(member: MemberDraft, kind: MemberKind) {
    const share =
      kind === 'spouse' || (kind === 'child' && member.share_percent !== '50')
        ? '100'
        : member.share_percent
    patchMember(member.key, { kind, share_percent: share })
  }

  function add() {
    if (household.members.length >= MEMBERS_MAX) return
    // A second spouse is refused here, so the first one added is the child the form offers.
    onChange({
      ...household,
      members: [...household.members, newMember(hasSpouse ? 'child' : 'spouse')],
    })
  }

  return (
    <fieldset className="space-y-3">
      <legend className="sr-only">Familiari a carico</legend>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="fiscal-household-mode">Familiari a carico</Label>
          <select
            id="fiscal-household-mode"
            className={SELECT}
            value={household.mode}
            disabled={disabled}
            aria-invalid={error ? true : undefined}
            onChange={(event) => onChange({ ...household, mode: event.target.value as HouseholdMode })}
          >
            {MODES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {error ? (
            <p className="text-destructive text-sm">{error}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              Facoltativo. Serve per le detrazioni dell’IRPEF e per alcune regole dell’addizionale
              regionale. Elenca solo chi ha un reddito proprio entro 2.840,51 euro (4.000 euro per
              un figlio fino a 24 anni).
            </p>
          )}
        </div>

        {stated ? (
          <div className="space-y-2">
            <Label htmlFor="fiscal-household-year">Anno di riferimento</Label>
            <Input
              id="fiscal-household-year"
              type="number"
              inputMode="numeric"
              value={household.year}
              disabled={disabled}
              onChange={(event) => onChange({ ...household, year: event.target.value })}
            />
            <p className="text-muted-foreground text-xs">
              La dichiarazione vale solo per la stima di quest’anno.
            </p>
          </div>
        ) : null}
      </div>

      {stated ? (
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <Checkbox
            checked={household.taxpayer_disabled}
            disabled={disabled}
            onCheckedChange={(checked) =>
              onChange({ ...household, taxpayer_disabled: checked === true })
            }
          />
          Sono disabile (legge 104/1992)
        </label>
      ) : null}

      {listed ? (
        <div className="space-y-3">
          <ul className="space-y-3">
            {household.members.map((member, index) => {
              const name = `${MemberKinds.find((kind) => kind.value === member.kind)?.label ?? ''} ${index + 1}`
              const id = `fiscal-household-${member.key}`
              return (
                <li
                  key={member.key}
                  aria-label={name}
                  className="grid gap-3 rounded-md border p-3 sm:grid-cols-2 lg:grid-cols-4"
                >
                  <div className="space-y-2">
                    <Label htmlFor={`${id}-kind`}>Familiare</Label>
                    <select
                      id={`${id}-kind`}
                      className={SELECT}
                      value={member.kind}
                      disabled={disabled}
                      onChange={(event) => pickKind(member, event.target.value as MemberKind)}
                    >
                      {MemberKinds.map((option) => (
                        <option
                          key={option.value}
                          value={option.value}
                          disabled={
                            option.value === 'spouse' && hasSpouse && member.kind !== 'spouse'
                          }
                        >
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  {member.kind === 'child' ? (
                    <div className="space-y-2">
                      <Label htmlFor={`${id}-birth`}>Data di nascita</Label>
                      <Input
                        id={`${id}-birth`}
                        type="date"
                        required
                        value={member.birth_date}
                        disabled={disabled}
                        onChange={(event) =>
                          patchMember(member.key, { birth_date: event.target.value })
                        }
                      />
                    </div>
                  ) : null}

                  {member.kind === 'child' ? (
                    <div className="space-y-2">
                      <Label htmlFor={`${id}-share`}>Quota</Label>
                      <select
                        id={`${id}-share`}
                        className={SELECT}
                        value={member.share_percent}
                        disabled={disabled}
                        onChange={(event) =>
                          patchMember(member.key, { share_percent: event.target.value })
                        }
                      >
                        <option value="100">100%</option>
                        <option value="50">50%</option>
                      </select>
                      <p className="text-muted-foreground text-xs">
                        50% quando la detrazione è divisa con l’altro genitore.
                      </p>
                    </div>
                  ) : null}

                  {member.kind === 'ascendant' ? (
                    <div className="space-y-2">
                      <Label htmlFor={`${id}-share`}>Quota (%)</Label>
                      <Input
                        id={`${id}-share`}
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={100}
                        value={member.share_percent}
                        disabled={disabled}
                        onChange={(event) =>
                          patchMember(member.key, { share_percent: event.target.value })
                        }
                      />
                    </div>
                  ) : null}

                  <div className="space-y-2">
                    <Label htmlFor={`${id}-months`}>Mesi a carico</Label>
                    <Input
                      id={`${id}-months`}
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={12}
                      value={member.months}
                      disabled={disabled}
                      onChange={(event) => patchMember(member.key, { months: event.target.value })}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-2 sm:col-span-2 lg:col-span-4">
                    <label className="flex min-h-9 items-center gap-2 text-sm">
                      <Checkbox
                        checked={member.disabled}
                        disabled={disabled}
                        onCheckedChange={(checked) =>
                          patchMember(member.key, { disabled: checked === true })
                        }
                      />
                      Disabile (legge 104/1992)
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={disabled}
                      aria-label={`Rimuovi ${name.toLowerCase()}`}
                      onClick={() =>
                        onChange({
                          ...household,
                          members: household.members.filter((other) => other.key !== member.key),
                        })
                      }
                    >
                      Rimuovi
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
          <Button
            variant="outline"
            size="sm"
            disabled={disabled || household.members.length >= MEMBERS_MAX}
            onClick={add}
          >
            Aggiungi familiare
          </Button>
        </div>
      ) : null}

      {anyDisabled ? (
        <div className="space-y-2">
          <Label htmlFor="fiscal-household-supporters">
            Reddito imponibile degli altri a carico dei quali è lo stesso familiare disabile
          </Label>
          <Input
            id="fiscal-household-supporters"
            type="number"
            inputMode="decimal"
            min={0}
            step="0.01"
            value={household.other_supporters_income}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...household, other_supporters_income: event.target.value })
            }
          />
          <p className="text-muted-foreground text-xs">
            Somma, ai fini dell’addizionale regionale. 0 se non ce ne sono: serve solo in Veneto e
            nelle Marche.
          </p>
        </div>
      ) : null}
    </fieldset>
  )
}
