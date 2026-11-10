import type { components } from '@/lib/api-types'
import type { Severity } from './fields'

/**
 * The two layers of reminder settings and how the page reads them (spec 2026-10-26 §5,
 * §7). The service resolves a setting as the person's override, then the space's default,
 * then the built-in one; this module only *shows* that: which layer a value comes from,
 * and what the control holds. It never writes a default into an override, which is why the
 * way back is a delete and not a copy.
 */

export type Preferences = components['schemas']['ReminderPreferencesRead']
export type PreferenceType = components['schemas']['ReminderTypeRead']
export type PreferenceRow = components['schemas']['ReminderPreferenceRead']
export type PreferenceWrite = components['schemas']['ReminderPreferenceWrite']
export type QuietHours = components['schemas']['QuietHours']
export type Scope = 'space' | 'me'
export type EmailMode = 'immediate' | 'summary'

/** The channels a person can switch. In-app is the source of truth and is never a setting. */
export const SWITCHABLE_CHANNELS = ['push', 'slack', 'email'] as const
export type SwitchableChannel = (typeof SWITCHABLE_CHANNELS)[number]

export const CHANNEL_LABELS: Record<SwitchableChannel, string> = {
  push: 'Notifica push',
  slack: 'Slack',
  email: 'Email',
}

/** The built-in values, those of `reminders/catalogue.py` and `schemas.py`. */
export const DEFAULT_URGENT_FROM: Severity = 'critical'
export const DEFAULT_QUIET_HOURS: QuietHours = {
  enabled: true,
  start: '20:00',
  end: '08:00',
  weekend: true,
}
export const DEFAULT_SUMMARY_HOUR = 8

/** Where a value comes from, nearest first: the person's own, the space's, the built-in. */
export type Source = 'mine' | 'space' | 'default'

export const SOURCE_LABELS: Record<Source, string> = {
  mine: 'Tuo valore',
  space: 'Valore dello spazio',
  default: 'Predefinito',
}

export interface Resolved<T> {
  value: T
  source: Source
}

/** The rows of one layer by type key, `null` standing for the settings of no type. */
type Layer = { source: Source; rows: Map<string | null, PreferenceRow> }

function layers(scope: Scope, data: Preferences): Layer[] {
  const index = (rows: PreferenceRow[], source: Source): Layer => ({
    source,
    rows: new Map(rows.map((row) => [row.type, row])),
  })
  return scope === 'me'
    ? [index(data.mine, 'mine'), index(data.space, 'space')]
    : [index(data.space, 'space')]
}

/**
 * The first layer that sets `column` for this type, or the built-in `fallback`. In the
 * `space` scope there is no personal layer: an admin editing the space's defaults sees the
 * space's own values, not the ones their personal overrides would give them.
 */
function pick<K extends keyof PreferenceRow>(
  stack: Layer[],
  typeKey: string | null,
  column: K,
  fallback: NonNullable<PreferenceRow[K]>,
): Resolved<NonNullable<PreferenceRow[K]>> {
  for (const layer of stack) {
    const value = layer.rows.get(typeKey)?.[column]
    if (value !== null && value !== undefined) {
      return { value: value as NonNullable<PreferenceRow[K]>, source: layer.source }
    }
  }
  return { value: fallback, source: 'default' }
}

export interface ResolvedType {
  /** What the checkbox shows. For `me`, a type the space switched off reads off. */
  enabled: Resolved<boolean>
  /** The space has the type off, which a person cannot undo for themselves. */
  spaceOff: boolean
  channels: Resolved<SwitchableChannel[]>
  ladder: Resolved<Record<string, number>>
  urgentFrom: Resolved<Severity>
  /** `null` is «decide l'urgenza»; the source is `default` then. */
  emailMode: Resolved<EmailMode | null>
}

export function resolveType(kind: PreferenceType, scope: Scope, data: Preferences): ResolvedType {
  const stack = layers(scope, data)
  const own = stack[0]?.rows.get(kind.key)
  const spaceOff = scope === 'me' && data.space.some((row) => row.type === kind.key && row.enabled === false)
  const enabled: Resolved<boolean> = spaceOff
    ? { value: false, source: 'space' }
    : own?.enabled !== null && own?.enabled !== undefined
      ? { value: own.enabled, source: scope === 'me' ? 'mine' : 'space' }
      : { value: true, source: 'default' }

  const channels = pick(stack, kind.key, 'channels', [...SWITCHABLE_CHANNELS])
  const ladder = pick(stack, kind.key, 'ladder', { ...kind.ladder })
  const mode = pick(stack, kind.key, 'email_mode', 'auto')
  return {
    enabled,
    spaceOff,
    channels: {
      source: channels.source,
      value: SWITCHABLE_CHANNELS.filter((channel) => channels.value.includes(channel)),
    },
    ladder,
    urgentFrom: pick(stack, kind.key, 'urgent_from', DEFAULT_URGENT_FROM) as Resolved<Severity>,
    emailMode: {
      source: mode.source,
      value: mode.value === 'immediate' || mode.value === 'summary' ? mode.value : null,
    },
  }
}

export interface ResolvedGeneral {
  quietHours: Resolved<QuietHours>
  summaryHour: Resolved<number>
}

export function resolveGeneral(scope: Scope, data: Preferences): ResolvedGeneral {
  const stack = layers(scope, data)
  return {
    quietHours: pick(stack, null, 'quiet_hours', DEFAULT_QUIET_HOURS),
    summaryHour: pick(stack, null, 'summary_hour', DEFAULT_SUMMARY_HOUR),
  }
}

/** Whether this layer holds a row for the type, which is what the way back deletes. */
export function hasOwnRow(scope: Scope, data: Preferences, typeKey: string | null): boolean {
  const rows = scope === 'me' ? data.mine : data.space
  return rows.some((row) => row.type === typeKey)
}

/**
 * The types a reader sees. A space-wide type (`audience: admins`) reaches admins alone, so
 * a member's personal settings do not list it: they could never receive it. The space's
 * defaults are an admin's page, so there every type is listed.
 */
export function visibleTypes(types: PreferenceType[], scope: Scope, isAdmin: boolean): PreferenceType[] {
  return scope === 'space' || isAdmin ? types : types.filter((kind) => kind.audience === 'subject')
}

const UNIT_WORDS: Record<PreferenceType['unit'], string> = {
  days: 'giorni',
  hours: 'ore',
  minutes: 'minuti',
  percent: '%',
}

/**
 * How one threshold reads around its number: «da 8 giorni» for a type that climbs as the
 * measure grows (`since`), «7 giorni prima» for one that climbs as it shrinks (`until`).
 */
export function thresholdWording(kind: Pick<PreferenceType, 'direction' | 'unit'>): {
  before: string
  after: string
} {
  const unit = UNIT_WORDS[kind.unit]
  return kind.direction === 'since'
    ? { before: 'da', after: unit }
    : { before: '', after: `${unit} prima` }
}
