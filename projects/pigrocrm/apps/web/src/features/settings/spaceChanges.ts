import type { components } from '@/lib/api-types'

export type SpaceSettings = components['schemas']['SpaceSettingsRead']
export type SpaceSettingsUpdate = components['schemas']['SpaceSettingsUpdate']

/** What the form holds while the person edits: strings for the inputs, booleans for
 *  the checkboxes. Secrets are write-only -- the field starts empty and an empty field
 *  means "leave it as it is". */
export interface Draft {
  google_client_id: string
  google_client_secret: string
  google_app_unverified: boolean
  storage_backend: 'local' | 'gdrive'
  mcp_full_access: boolean
  gmail_backfill_days: string
}

/** Whether the draft replaces the platform's client, which this space borrows, with a
 *  client of its own (REB-394). */
export function switchingFromShared(saved: SpaceSettings, draft: Draft): boolean {
  return (
    saved.google_client_condiviso &&
    draft.google_client_id !== '' &&
    draft.google_client_id !== saved.google_client_id
  )
}

export function draftFrom(settings: SpaceSettings): Draft {
  return {
    google_client_id: settings.google_client_id,
    google_client_secret: '',
    // While the space borrows the platform's client, the «Testing» switch belongs to the
    // client the person may be about to bring, and every new OAuth client starts in
    // Testing on Google's side: so it starts ticked, and travels with the switch.
    google_app_unverified: settings.google_client_condiviso ? true : settings.google_app_unverified,
    storage_backend: settings.storage_backend === 'gdrive' ? 'gdrive' : 'local',
    mcp_full_access: settings.mcp_full_access,
    gmail_backfill_days: String(settings.gmail_backfill_days),
  }
}

/** Only what changed travels: the API treats an absent key as "leave it". */
export function changesBetween(saved: SpaceSettings, draft: Draft): SpaceSettingsUpdate {
  const changes: SpaceSettingsUpdate = {}
  if (draft.google_client_id !== saved.google_client_id) {
    changes.google_client_id = draft.google_client_id
  }
  if (draft.google_client_secret !== '') changes.google_client_secret = draft.google_client_secret
  // A new client of the space's own is declared with it, whatever the platform's said:
  // the server reads the platform's value for the borrowed client, not for theirs.
  if (
    switchingFromShared(saved, draft) ||
    (!saved.google_client_condiviso && draft.google_app_unverified !== saved.google_app_unverified)
  ) {
    changes.google_app_unverified = draft.google_app_unverified
  }
  if (draft.storage_backend !== saved.storage_backend) changes.storage_backend = draft.storage_backend
  if (draft.mcp_full_access !== saved.mcp_full_access) changes.mcp_full_access = draft.mcp_full_access
  // The three `solleciti_*` keys still exist on the API and keep their values; the form
  // stopped editing them on 2026-09-09, when the Solleciti page left the interface.
  for (const key of ['gmail_backfill_days'] as const) {
    const value = Number(draft[key])
    if (Number.isFinite(value) && value !== saved[key]) changes[key] = value
  }
  return changes
}

