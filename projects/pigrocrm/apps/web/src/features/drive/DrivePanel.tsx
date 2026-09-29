import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toProblem } from '@/lib/api'
import { useIsAdmin } from '@/lib/auth'
import { formatInstant } from './instants'
import {
  DRIVE_OAUTH_START,
  useDisconnectDrive,
  useDriveHealth,
  useSetDriveRoots,
  type DriveHealth,
  type GoogleDriveAccountRead,
} from './queries'

// The exact pattern `DriveRootsUpdate` validates against
// (`packages/core/src/pigrocrm/core/drive/schemas.py`): a Drive file id and nothing
// else. Checked here too, so a malformed id is refused before the request ever leaves
// the browser rather than surfacing only as a 422 after "Salva" is pressed.
const DRIVE_ID_PATTERN = /^[A-Za-z0-9_-]{10,128}$/

const STATUS_LABEL: Record<string, string> = {
  active: 'attiva',
  expired: 'consenso scaduto',
  revoked: 'consenso revocato',
  disconnected: 'scollegata',
}

/**
 * Impostazioni → Google Drive.
 *
 * The same four-screen order as `GmailPanel`, for the same reason -- a control
 * rendered from a health response that never arrived would offer to change (or delete)
 * a setting nobody managed to show the user:
 *
 * 1. **The read failed.** The banner, and nothing under it.
 * 2. **`configured: false`.** No Google client on this installation -- an explanation
 *    and no button.
 * 3. **Configured, no account (or one deliberately disconnected).** One link to the
 *    consent flow.
 * 4. **Connected.** Email, status, the roots editor, and «Scollega Drive». When
 *    `banner_text` is set (a revoked or expiring consent, a missing scope) the banner
 *    is shown *above* the editor, never instead of it: `set_roots` admits a revoked or
 *    expired credential on purpose -- the person on this screen is the one recovering
 *    from exactly that state, and the folder list is the setting they came to fix --
 *    and `expiring` is a working credential with a date attached, where hiding the
 *    editor would remove a control over a warning about next week.
 *
 * **Open to a collaboratore, one field short (REB-457).** The credential on this page
 * is the viewer's own (`google_drive_accounts.user_id`), and connecting, disconnecting
 * and the read roots all act on that row alone, under `require_write`: a collaboratore
 * gets them as an admin does. The write folder is different. `DriveRepository.
 * storage_account` picks the space's one place for generated documents from the rows of
 * the users who are admins now, so it is the space's setting, and `set_roots` refuses
 * `storage_folder_id` from anyone else (`require_admin`). A collaboratore therefore does
 * not see that field, is told who chooses it, and their «Salva» omits
 * `storage_folder_id`, which the `PATCH` reads as "leave it alone" (`DriveRootsUpdate`).
 * Their own row never supplies the space's storage, so their disconnect confirmation has
 * no write folder to warn about.
 *
 * **One more line, admin-only (REB-562).** `data.space_storage` is
 * `DriveRepository.storage_account`'s own answer -- the folder the space actually
 * writes generated documents into, which may be a *different* admin's, or nobody's,
 * regardless of what this page's own `account`/roots editor shows about the viewer's
 * own credential. The server computes it only for an admin actor
 * (`GoogleDriveAccountService.space_storage`) and, for anyone else, leaves the field
 * genuinely **unset** rather than `null` (`response_model_exclude_unset=True`,
 * `routers/drive.py`) -- fix round 1's answer to a stale client-side admin flag, read
 * right after a demotion, showing a false "no folder chosen" instead of nothing.
 *
 * **Both conditions, not one (fix round 2, Greptile; fix round 4, CodeRabbit).**
 * `useDriveHealth` now keys its query by the viewer's own id *and* role
 * (`driveKeys.healthForViewer`, `queries.ts`), so either kind of change -- a demotion,
 * or a session switch from one admin to another in the same tab without a `logout()`
 * in between -- is a genuinely different query, nothing cached under the new key, and
 * refetches at once rather than going on serving the previous viewer's response
 * (complete with *their* Drive account's email) until some unrelated mutation happens
 * to invalidate it. This component still checks `choosesWriteFolder` on top of
 * `data.space_storage`'s presence, belt and braces, so a render caught between that
 * change and the new query settling can never show a holder's name to someone this
 * render already knows is not an admin. Computed once, before every branch below
 * including the unconfigured one (also fix round 2, CodeRabbit): `space_storage`
 * answers a question about the space's storage, resolved independently of whether
 * *this* request's own Google client happens to be fully configured (fix round 1), so
 * an admin sees it on every screen this component can render -- disconnected,
 * connected, unconfigured -- because it is a question about the space, not about this
 * account or this installation's client secret.
 */
export function DrivePanel() {
  const health = useDriveHealth()
  const disconnect = useDisconnectDrive()
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Read before the early returns below: a hook is never conditional.
  const choosesWriteFolder = useIsAdmin()

  if (health.isError) return <QueryErrorBanner error={health.error} />
  if (health.isPending || !health.data) return <p className="text-muted-foreground">Caricamento…</p>

  const data: DriveHealth = health.data

  // Both conditions, not presence alone (REB-562 fix round 2, Greptile; fix round 4,
  // CodeRabbit): the query is now keyed by the viewer's own id *and* role
  // (`driveKeys.healthForViewer`, `queries.ts`), so either a demotion or a session
  // switch between two admins in one tab refetches at once rather than going on
  // serving a cached response with the previous viewer's own `space_storage` --
  // but `choosesWriteFolder` is still checked here too, belt and braces, so a render
  // caught between that change and the new query settling can never show a holder's
  // name to someone this render already knows is not an admin. `choosesWriteFolder`
  // also still governs the write-folder *editor* below, a client permission decision
  // the server does not need to answer for.
  //
  // Computed before the `!data.configured` branch, not only in the configured ones
  // (CodeRabbit, fix round 2): `space_storage` answers a question about the space's
  // storage, which `DriveRepository.storage_account` resolves independently of whether
  // *this* request's Google client happens to be fully configured (fix round 1) -- so
  // an admin must see it on every screen this component can render, unconfigured
  // included.
  const spaceStorage = choosesWriteFolder && data.space_storage != null ? (
    <SpaceStorageLine storage={data.space_storage} />
  ) : null

  if (!data.configured) {
    return (
      <>
        {spaceStorage}
        <NotConfigured />
      </>
    )
  }

  const account = data.account
  if (account === null || account.status === 'disconnected') {
    return (
      <>
        {spaceStorage}
        <NotConnected account={account} choosesWriteFolder={choosesWriteFolder} />
      </>
    )
  }

  return (
    <>
      {spaceStorage}
      <section className="max-w-3xl space-y-6">
        <header className="space-y-1">
          <h2 className="text-lg font-medium">Google Drive collegato</h2>
          <p className="font-medium">{account.email_address}</p>
          <p className="text-sm text-muted-foreground">
            Stato: {STATUS_LABEL[account.status] ?? account.status}
            {account.consent_expires_at
              ? ` · Consenso da rinnovare entro il ${formatInstant(account.consent_expires_at)}`
              : ''}
          </p>
        </header>

        {data.banner_text ? (
          <p
            role="status"
            className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {data.banner_text}
          </p>
        ) : null}

        {account.last_error ? (
          <p className="text-sm text-muted-foreground">
            Ultimo errore: {account.last_error} ({formatInstant(account.last_error_at)})
          </p>
        ) : null}

        {/* In every connected state, banner or not: see the component docstring. The
            server accepts this PATCH from a revoked or expired credential, so the panel
            must not be the thing that refuses it. */}
        <RootsEditor account={account} choosesWriteFolder={choosesWriteFolder} />

        <div className="space-y-2 border-t pt-4">
          <Button variant="destructive" onClick={() => setConfirmOpen(true)}>
            Scollega Drive
          </Button>
        </div>

        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Scollegare Google Drive?</DialogTitle>
              <DialogDescription>
                {choosesWriteFolder
                  ? 'La CRM non potrà più leggere le cartelle configurate né scrivere nella ' +
                    "cartella di archiviazione, finché non ricolleghi l'account."
                  : "La CRM non potrà più leggere le cartelle configurate, finché non ricolleghi l'account."}
              </DialogDescription>
            </DialogHeader>
            <ActionError error={disconnect.error} />
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                Annulla
              </Button>
              <Button
                variant="destructive"
                disabled={disconnect.isPending}
                onClick={() =>
                  disconnect.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })
                }
              >
                Scollega Drive
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </section>
    </>
  )
}

/** The server's own sentence for a failed action, or nothing. Derived from the
 *  mutation, so the next attempt clears it. */
function ActionError({ error }: { error: unknown }) {
  if (!error) return null
  return <p className="text-sm text-destructive">{toProblem(error).detail}</p>
}

/**
 * REB-562. What `DriveHealth.space_storage` answers, read as it comes -- the id of
 * which admin's row is not derived here, only what the server already resolved
 * (`GoogleDriveAccountService.space_storage`, from `DriveRepository.storage_holder`).
 * Rendered only when `DrivePanel` finds the field present at all (never `null` in
 * practice for the admin actor this component is reached for; see that field's own
 * docstring for why an admin's response always carries a concrete object).
 *
 * **Wording, fix round 1 (CodeRabbit).** `in_effect` never claims a document *will*
 * land there: `storage_holder`'s own query proves the row is `active` and names a
 * folder, not that the holder's credential still carries `drive.file` -- a re-consent
 * that dropped the scope leaves a working, `active` credential that cannot actually
 * write. So the sentence names the folder as configured ("cartella di scrittura dei
 * documenti: quella di ...") and only *adds* "al momento non raggiungibile" when
 * `holder.reachable` says the write itself would fail. And when nothing is in effect,
 * the sentence does not claim no admin ever chose one -- the row could be sitting on a
 * revoked or expired account, chosen once and then broken -- only that no write is
 * available right now.
 */
function SpaceStorageLine({
  storage,
}: {
  storage: NonNullable<DriveHealth['space_storage']>
}) {
  return (
    <p className="max-w-3xl text-sm text-muted-foreground">
      {storage.in_effect && storage.holder
        ? `Cartella di scrittura dei documenti: quella di ${storage.holder.name} ` +
          `(${storage.holder.email})` +
          (storage.holder.reachable ? '.' : ', al momento non raggiungibile.')
        : 'Nessuna cartella di scrittura disponibile: i documenti generati non si ' +
          'possono salvare finché un amministratore non ne collega una.'}
    </p>
  )
}

function NotConfigured() {
  return (
    <section className="max-w-3xl space-y-2">
      <h2 className="text-lg font-medium">Google Drive</h2>
      <p className="text-muted-foreground">
        Google Drive non è configurato su questa installazione. Per attivarlo servono un
        client OAuth di Google e le variabili <code>PIGROCRM_GOOGLE_CLIENT_ID</code>,{' '}
        <code>PIGROCRM_GOOGLE_CLIENT_SECRET</code>, <code>PIGROCRM_GOOGLE_TOKEN_KEY</code>{' '}
        e <code>PIGROCRM_PUBLIC_URL</code>.
      </p>
    </section>
  )
}

function NotConnected({
  account,
  choosesWriteFolder,
}: {
  account: GoogleDriveAccountRead | null
  choosesWriteFolder: boolean
}) {
  return (
    <section className="max-w-3xl space-y-3">
      <h2 className="text-lg font-medium">Nessun account Google Drive collegato</h2>
      {account ? (
        <p className="text-sm text-muted-foreground">
          L&apos;account {account.email_address} è stato scollegato il{' '}
          {formatInstant(account.disconnected_at)}.
        </p>
      ) : null}
      <p className="text-muted-foreground">
        {choosesWriteFolder
          ? 'Collegando Google Drive potrai indicare quali cartelle la CRM può leggere e in ' +
            'quale può scrivere i documenti generati.'
          : 'Collegando Google Drive potrai indicare quali cartelle la CRM può leggere.'}
      </p>
      <Button asChild>
        <a href={DRIVE_OAUTH_START}>Collega Google Drive</a>
      </Button>
    </section>
  )
}

interface RootRow {
  /** A client-only key: React needs a stable identity per row even before the id
   *  typed into it is valid, and a Drive id itself cannot serve that role while it is
   *  still empty or being edited. */
  key: string
  id: string
  /** A free-form local label -- never sent to the server. `DriveRootsUpdate` carries
   *  only `root_folder_ids`; this exists purely so the person editing the list can
   *  tell "Contratti clienti" apart from "Fatture fornitori" without memorising Drive
   *  ids. It resets on every reload, on purpose: the server has nowhere to keep it. */
  label: string
}

let nextRowKey = 0
function newRow(id = ''): RootRow {
  nextRowKey += 1
  return { key: `row-${nextRowKey}`, id, label: '' }
}

/**
 * The two fields `DriveRootsUpdate` carries: which folders the CRM may read from, and
 * the one it may write generated documents into. Each folder id is validated against
 * the same pattern the server enforces (`DRIVE_ID_PATTERN`) before «Salva» is even
 * enabled, so a typo is caught here rather than round-tripping to a 422.
 *
 * `choosesWriteFolder` is false for a collaboratore: the write folder is the space's,
 * not theirs (see `DrivePanel`), so its field is not drawn and «Salva» does not name it.
 */
function RootsEditor({
  account,
  choosesWriteFolder,
}: {
  account: GoogleDriveAccountRead
  choosesWriteFolder: boolean
}) {
  const setRoots = useSetDriveRoots()
  const [rows, setRows] = useState<RootRow[]>(() =>
    account.root_folder_ids.length > 0 ? account.root_folder_ids.map((id) => newRow(id)) : [newRow()],
  )
  const [storageFolderId, setStorageFolderId] = useState(account.storage_folder_id ?? '')

  const trimmedRows = rows.map((row) => ({ ...row, id: row.id.trim() }))
  const nonEmptyIds = trimmedRows.filter((row) => row.id.length > 0)
  const invalidRootIds = nonEmptyIds.filter((row) => !DRIVE_ID_PATTERN.test(row.id))
  const trimmedStorageId = storageFolderId.trim()
  const storageInvalid =
    choosesWriteFolder && trimmedStorageId.length > 0 && !DRIVE_ID_PATTERN.test(trimmedStorageId)

  const canSave =
    nonEmptyIds.length > 0 && invalidRootIds.length === 0 && !storageInvalid && !setRoots.isPending

  function updateRow(key: string, patch: Partial<RootRow>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)))
  }

  function removeRow(key: string) {
    setRows((current) => current.filter((row) => row.key !== key))
  }

  function addRow() {
    setRows((current) => [...current, newRow()])
  }

  function save() {
    const root_folder_ids = nonEmptyIds.map((row) => row.id)
    setRoots.mutate(
      // Omitted, not `null`, for a collaboratore: `null` would clear the folder, and
      // absent is what leaves it as it is.
      choosesWriteFolder
        ? {
            root_folder_ids,
            storage_folder_id: trimmedStorageId.length > 0 ? trimmedStorageId : null,
          }
        : { root_folder_ids },
      { onSuccess: () => toast.success('Cartelle Drive salvate') },
    )
  }

  return (
    <div className="space-y-4 border-t pt-4">
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Cartelle che la CRM può leggere</h3>
        <p className="text-sm text-muted-foreground">
          Copia l&apos;ID cartella dall&apos;URL di Google Drive: in{' '}
          <code>drive.google.com/drive/folders/&lt;ID&gt;</code>, l&apos;ID è la parte
          dopo l&apos;ultima barra.
        </p>
        <ul className="space-y-2">
          {rows.map((row) => {
            const trimmedId = row.id.trim()
            const invalid = trimmedId.length > 0 && !DRIVE_ID_PATTERN.test(trimmedId)
            return (
              <li key={row.key} className="flex flex-wrap items-start gap-2">
                <div className="flex-1 space-y-1">
                  <Input
                    aria-label="ID cartella"
                    aria-invalid={invalid}
                    placeholder="ID cartella Drive"
                    value={row.id}
                    onChange={(event) => updateRow(row.key, { id: event.target.value })}
                  />
                  {invalid ? (
                    <p className="text-sm text-destructive">
                      L&apos;ID non è valido: deve contenere solo lettere, cifre, «-» e
                      «_», tra 10 e 128 caratteri.
                    </p>
                  ) : null}
                </div>
                <Input
                  aria-label="Etichetta locale (facoltativa)"
                  placeholder="Etichetta locale (facoltativa)"
                  className="flex-1"
                  value={row.label}
                  onChange={(event) => updateRow(row.key, { label: event.target.value })}
                />
                <Button
                  type="button"
                  variant="ghost"
                  aria-label="Rimuovi cartella"
                  onClick={() => removeRow(row.key)}
                >
                  Rimuovi
                </Button>
              </li>
            )
          })}
        </ul>
        <Button type="button" variant="outline" onClick={addRow}>
          Aggiungi cartella
        </Button>
      </div>

      {choosesWriteFolder ? (
        <div className="space-y-1">
          <Label htmlFor="drive-storage-folder">Cartella di scrittura</Label>
          <Input
            id="drive-storage-folder"
            aria-invalid={storageInvalid}
            placeholder="ID cartella Drive (facoltativo)"
            value={storageFolderId}
            onChange={(event) => setStorageFolderId(event.target.value)}
            className="max-w-md"
          />
          <p className="text-sm text-muted-foreground">
            La cartella in cui la CRM scrive i documenti che genera. Lasciala vuota per non
            scrivere alcun documento.
          </p>
          {storageInvalid ? (
            <p className="text-sm text-destructive">
              L&apos;ID non è valido: deve contenere solo lettere, cifre, «-» e «_», tra 10
              e 128 caratteri.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          La cartella in cui la CRM scrive i documenti generati è una sola per tutto lo
          spazio: la sceglie un amministratore.
        </p>
      )}

      <div className="space-y-2">
        <Button onClick={save} disabled={!canSave}>
          Salva
        </Button>
        <ActionError error={setRoots.error} />
      </div>
    </div>
  )
}
