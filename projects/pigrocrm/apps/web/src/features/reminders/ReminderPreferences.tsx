import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { toast } from '@rebase/ui/sonner'
import { fieldErrorFrom, toProblem } from '@/lib/api'
import { useIsAdmin } from '@/lib/auth'
import { SEVERITIES, type Severity } from './fields'
import { groupTypes } from './preferenceGroups'
import {
  CHANNEL_LABELS,
  SOURCE_LABELS,
  SWITCHABLE_CHANNELS,
  hasOwnRow,
  resolveGeneral,
  resolveType,
  thresholdWording,
  visibleTypes,
  type EmailMode,
  type PreferenceType,
  type PreferenceWrite,
  type Preferences,
  type Scope,
  type Source,
  type SwitchableChannel,
} from './preferences'
import {
  useReminderPreferences,
  useResetPreference,
  useWritePreference,
} from './preferencesQueries'

/** What the way back is called, by layer: a person goes back to the space's value, an admin
 *  to the built-in one. */
const RESET_LABELS: Record<Scope, string> = {
  me: 'Torna al valore dello spazio',
  space: 'Torna al predefinito',
}

/** «Secondo l'urgenza» is the built-in rule (`email_mode` null); a person who clears their
 *  own choice inherits the space's, which is what the same option says in that scope. */
const AUTO = 'auto'
const EMAIL_MODES: { value: EmailMode | typeof AUTO; label: Record<Scope, string> }[] = [
  { value: 'immediate', label: { me: 'Subito', space: 'Subito' } },
  { value: 'summary', label: { me: 'Nel riepilogo', space: 'Nel riepilogo' } },
  { value: AUTO, label: { me: 'Come lo spazio', space: "Secondo l'urgenza" } },
]

/** A write that was refused: the API's sentence and the field it belongs under. */
interface Refusal {
  field: string
  message: string
}

/**
 * The reminder settings of one layer (spec 2026-10-26 §5, §7), one component for both:
 * `scope="space"` is the admins' defaults, `scope="me"` a person's own overrides.
 *
 * Every control saves as it changes (the thresholds, which are one ladder, save together
 * with their own button) and shows whose value it holds: the person's own, the space's or
 * the built-in one. The way back is a delete of the layer's row (`Torna al valore dello
 * spazio` for a person, `Torna al predefinito` for the space), never a copy of the default
 * into the override, so a later change of the default still reaches who never overrode it.
 * The API's own sentences, in Italian, are what a refused save shows under its control.
 */
export function ReminderPreferences({ scope }: { scope: Scope }) {
  const preferences = useReminderPreferences()
  const isAdmin = useIsAdmin()

  if (preferences.isError) return <QueryErrorBanner error={preferences.error} />
  if (preferences.isLoading || !preferences.data) {
    return (
      <div className="space-y-3" role="status" aria-label="Caricamento impostazioni dei promemoria">
        {Array.from({ length: 3 }, (_, index) => (
          <Skeleton key={index} className="h-24 w-full" />
        ))}
      </div>
    )
  }
  const data = preferences.data
  const groups = groupTypes(visibleTypes(data.types, scope, isAdmin))

  return (
    <div className="space-y-10">
      <GeneralBlock scope={scope} data={data} />
      {groups.map((group) => (
        <section key={group.id} aria-labelledby={`promemoria-${scope}-${group.id}`}>
          <h3 id={`promemoria-${scope}-${group.id}`} className="mb-2 text-sm font-semibold">
            {group.label}
          </h3>
          <ul className="border-t">
            {group.types.map((kind) => (
              <TypeRow key={kind.key} kind={kind} scope={scope} data={data} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function SourceNote({ source }: { source: Source }) {
  return (
    <span className="text-xs text-muted-foreground" data-source={source}>
      {SOURCE_LABELS[source]}
    </span>
  )
}

function Refused({ refusal }: { refusal: Refusal | null }) {
  return refusal ? (
    <p role="alert" className="text-sm text-destructive">
      {refusal.message}
    </p>
  ) : null
}

/** The write and the way back of one row, with the sentence a refusal leaves under it. */
function useRow(scope: Scope, type: string) {
  const write = useWritePreference(scope)
  const reset = useResetPreference(scope)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const fail = (error: unknown) => {
    const problem = toProblem(error)
    const field = fieldErrorFrom(problem)
    setRefusal(field ?? { field: 'row', message: problem.detail })
    if (!field) toast.error(problem.detail)
  }
  return {
    refusal,
    busy: write.isPending || reset.isPending,
    save: (body: PreferenceWrite, done?: () => void) => {
      setRefusal(null)
      write.mutate({ type, body }, { onError: fail, onSuccess: done })
    },
    back: () => {
      setRefusal(null)
      reset.mutate(type, { onError: fail })
    },
  }
}

function GeneralBlock({ scope, data }: { scope: Scope; data: Preferences }) {
  const row = useRow(scope, 'general')
  const { quietHours, summaryHour } = resolveGeneral(scope, data)
  const quiet = quietHours.value
  const own = hasOwnRow(scope, data, null)
  const changeQuiet = (change: Partial<typeof quiet>) =>
    row.save({ quiet_hours: { ...quiet, ...change } })
  const id = `promemoria-${scope}-general`

  return (
    <section aria-labelledby={id} className="space-y-4">
      <div>
        <h3 id={id} className="text-sm font-semibold">
          Ore di silenzio e riepilogo
        </h3>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Nelle ore di silenzio notifiche push, Slack ed email restano in attesa, anche quelle
          urgenti: l’elenco degli avvisi nell’app resta sempre aggiornato. Il riepilogo è la
          email del mattino con quello che non era urgente.
        </p>
      </div>

      <div className="space-y-3 border-t pt-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id={`${id}-attive`}
              checked={quiet.enabled}
              disabled={row.busy}
              onCheckedChange={(checked) => changeQuiet({ enabled: checked === true })}
            />
            <Label htmlFor={`${id}-attive`}>Ore di silenzio</Label>
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor={`${id}-dalle`}>Dalle</Label>
            <Input
              id={`${id}-dalle`}
              type="time"
              className="w-28"
              value={quiet.start}
              disabled={row.busy || !quiet.enabled}
              onChange={(event) => event.target.value && changeQuiet({ start: event.target.value })}
            />
            <Label htmlFor={`${id}-alle`}>alle</Label>
            <Input
              id={`${id}-alle`}
              type="time"
              className="w-28"
              value={quiet.end}
              disabled={row.busy || !quiet.enabled}
              onChange={(event) => event.target.value && changeQuiet({ end: event.target.value })}
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id={`${id}-weekend`}
              checked={quiet.weekend}
              disabled={row.busy || !quiet.enabled}
              onCheckedChange={(checked) => changeQuiet({ weekend: checked === true })}
            />
            <Label htmlFor={`${id}-weekend`}>Anche sabato e domenica</Label>
          </div>
          <SourceNote source={quietHours.source} />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Label htmlFor={`${id}-ora`}>Ora del riepilogo</Label>
          <Select
            value={String(summaryHour.value)}
            disabled={row.busy}
            onValueChange={(value) => row.save({ summary_hour: Number(value) })}
          >
            <SelectTrigger id={`${id}-ora`} className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Array.from({ length: 24 }, (_, hour) => (
                <SelectItem key={hour} value={String(hour)}>
                  {String(hour).padStart(2, '0')}:00
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <SourceNote source={summaryHour.source} />
        </div>

        <Refused refusal={row.refusal} />
        {own && (
          <Button variant="ghost" size="sm" disabled={row.busy} onClick={row.back}>
            {RESET_LABELS[scope]}
          </Button>
        )}
      </div>
    </section>
  )
}

function TypeRow({
  kind,
  scope,
  data,
}: {
  kind: PreferenceType
  scope: Scope
  data: Preferences
}) {
  const row = useRow(scope, kind.key)
  const resolved = resolveType(kind, scope, data)
  const own = hasOwnRow(scope, data, kind.key)
  const id = `promemoria-${scope}-${kind.key}`
  const ladderRefused = row.refusal?.field === 'ladder' ? row.refusal : null
  const otherRefusal = ladderRefused ? null : row.refusal

  const toggleChannel = (channel: SwitchableChannel, on: boolean) =>
    row.save({
      channels: SWITCHABLE_CHANNELS.filter((candidate) =>
        candidate === channel ? on : resolved.channels.value.includes(candidate),
      ),
    })

  return (
    <li className="space-y-4 border-b px-2 py-4" aria-labelledby={`${id}-nome`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl space-y-1">
          <div className="flex flex-wrap items-center gap-3">
            <Checkbox
              id={`${id}-attivo`}
              checked={resolved.enabled.value}
              disabled={row.busy || resolved.spaceOff}
              onCheckedChange={(checked) => row.save({ enabled: checked === true })}
            />
            <Label htmlFor={`${id}-attivo`} id={`${id}-nome`} className="text-base font-medium">
              {kind.label}
            </Label>
            <SourceNote source={resolved.enabled.source} />
          </div>
          <p className="text-sm text-muted-foreground">{kind.description}</p>
          {kind.audience === 'admins' && (
            <p className="text-xs text-muted-foreground">Arriva solo agli amministratori.</p>
          )}
          {resolved.spaceOff && (
            <p className="text-sm text-muted-foreground">
              Lo spazio ha spento questo promemoria: non lo puoi riaccendere per te.
            </p>
          )}
        </div>
        {own && (
          <Button variant="ghost" size="sm" disabled={row.busy} onClick={row.back}>
            {RESET_LABELS[scope]}
          </Button>
        )}
      </div>

      <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">
            Canali <SourceNote source={resolved.channels.source} />
          </legend>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <div className="flex items-center gap-2">
              <Checkbox id={`${id}-in_app`} checked disabled />
              <Label htmlFor={`${id}-in_app`}>App (sempre attiva)</Label>
            </div>
            {SWITCHABLE_CHANNELS.map((channel) => (
              <div key={channel} className="flex items-center gap-2">
                <Checkbox
                  id={`${id}-${channel}`}
                  checked={resolved.channels.value.includes(channel)}
                  disabled={row.busy}
                  onCheckedChange={(checked) => toggleChannel(channel, checked === true)}
                />
                <Label htmlFor={`${id}-${channel}`}>{CHANNEL_LABELS[channel]}</Label>
              </div>
            ))}
          </div>
        </fieldset>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor={`${id}-urgente`}>Urgente da</Label>
            <Select
              value={resolved.urgentFrom.value}
              disabled={row.busy}
              onValueChange={(value) => row.save({ urgent_from: value as Severity })}
            >
              <SelectTrigger id={`${id}-urgente`} className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SEVERITIES.slice()
                  .reverse()
                  .map((severity) => (
                    <SelectItem key={severity.id} value={severity.id}>
                      {severity.single}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <SourceNote source={resolved.urgentFrom.source} />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor={`${id}-modo-email`}>Invio email</Label>
            <Select
              value={resolved.emailMode.value ?? AUTO}
              disabled={row.busy}
              onValueChange={(value) =>
                row.save({ email_mode: value === AUTO ? null : (value as EmailMode) })
              }
            >
              <SelectTrigger id={`${id}-modo-email`} className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EMAIL_MODES.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value}>
                    {mode.label[scope]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <SourceNote source={resolved.emailMode.source} />
          </div>
        </div>
      </div>

      <Ladder
        // A saved or reset ladder remounts the editor, so its draft starts from what the
        // server holds and never from what was typed before.
        key={JSON.stringify(resolved.ladder.value)}
        kind={kind}
        id={id}
        ladder={resolved.ladder.value}
        source={resolved.ladder.source}
        busy={row.busy}
        refusal={ladderRefused}
        onSave={(ladder) => row.save({ ladder })}
      />
      <Refused refusal={otherRefusal} />
    </li>
  )
}

/**
 * The thresholds of a type, one per severity, in the type's own unit. A box left empty is a
 * severity the type does not climb to; the ladder is saved as a whole, because the API
 * checks the order of the rungs against each other and answers with the sentence.
 */
function Ladder({
  kind,
  id,
  ladder,
  source,
  busy,
  refusal,
  onSave,
}: {
  kind: PreferenceType
  id: string
  ladder: Record<string, number>
  source: Source
  busy: boolean
  refusal: Refusal | null
  onSave: (ladder: Record<string, number>) => void
}) {
  const initial = Object.fromEntries(
    SEVERITIES.map((severity) => [severity.id, ladder[severity.id]?.toString() ?? '']),
  )
  const [draft, setDraft] = useState<Record<string, string>>(initial)
  const [local, setLocal] = useState<string | null>(null)
  const wording = thresholdWording(kind)
  const changed = SEVERITIES.some((severity) => draft[severity.id] !== initial[severity.id])

  const save = () => {
    const next: Record<string, number> = {}
    for (const severity of SEVERITIES) {
      const text = (draft[severity.id] ?? '').trim()
      if (text === '') continue
      if (!/^\d+$/.test(text)) {
        setLocal('Scrivi un numero intero, da 0 in su.')
        return
      }
      next[severity.id] = Number(text)
    }
    setLocal(null)
    onSave(next)
  }

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">
        Soglie <SourceNote source={source} />
      </legend>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {SEVERITIES.map((severity) => (
          <div key={severity.id} className="flex items-center gap-2">
            <Label htmlFor={`${id}-soglia-${severity.id}`} className="w-24">
              {severity.single}
            </Label>
            {wording.before && <span className="text-sm text-muted-foreground">{wording.before}</span>}
            <Input
              id={`${id}-soglia-${severity.id}`}
              inputMode="numeric"
              className="w-20"
              value={draft[severity.id] ?? ''}
              disabled={busy}
              aria-invalid={refusal !== null || local !== null ? true : undefined}
              onChange={(event) => setDraft((previous) => ({ ...previous, [severity.id]: event.target.value }))}
            />
            <span className="text-sm text-muted-foreground">{wording.after}</span>
          </div>
        ))}
        <Button variant="outline" size="sm" disabled={busy || !changed} onClick={save}>
          Salva soglie
        </Button>
      </div>
      {(local ?? refusal?.message) && (
        <p role="alert" className="text-sm text-destructive">
          {local ?? refusal?.message}
        </p>
      )}
    </fieldset>
  )
}
