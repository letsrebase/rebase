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
import { tenantPrefix } from '@/lib/tenant'
import { formatInstant } from './instants'
import {
  useDisconnectDrive,
  useDriveHealth,
  useSetDriveRoots,
  type DriveHealth,
  type GoogleDriveAccountRead,
} from './queries'

// Under a space, and under the root's own name, the API answers at `/<slug>/api/...` and
// the session cookie is scoped to that prefix: a plain anchor to `/api/...` reaches the
// root API with no cookie and answers «Autenticazione richiesta» (live, 2026-09-09).
const OAUTH_START = `${tenantPrefix}/api/drive/oauth/start`

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
 */
export function DrivePanel() {
  const health = useDriveHealth()
  const disconnect = useDisconnectDrive()
  const [confirmOpen, setConfirmOpen] = useState(false)

  if (health.isError) return <QueryErrorBanner error={health.error} />
  if (health.isPending || !health.data) return <p className="text-muted-foreground">Caricamento…</p>

  const data: DriveHealth = health.data
  if (!data.configured) return <NotConfigured />

  const account = data.account
  if (account === null || account.status === 'disconnected') {
    return <NotConnected account={account} />
  }

  return (
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
      <RootsEditor account={account} />

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
              La CRM non potrà più leggere le cartelle configurate né scrivere nella
              cartella di archiviazione, finché non ricolleghi l&apos;account.
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
  )
}

/** The server's own sentence for a failed action, or nothing. Derived from the
 *  mutation, so the next attempt clears it. */
function ActionError({ error }: { error: unknown }) {
  if (!error) return null
  return <p className="text-sm text-destructive">{toProblem(error).detail}</p>
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

function NotConnected({ account }: { account: GoogleDriveAccountRead | null }) {
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
        Collegando Google Drive potrai indicare quali cartelle la CRM può leggere e in
        quale può scrivere i documenti generati.
      </p>
      <Button asChild>
        <a href={OAUTH_START}>Collega Google Drive</a>
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
 */
function RootsEditor({ account }: { account: GoogleDriveAccountRead }) {
  const setRoots = useSetDriveRoots()
  const [rows, setRows] = useState<RootRow[]>(() =>
    account.root_folder_ids.length > 0 ? account.root_folder_ids.map((id) => newRow(id)) : [newRow()],
  )
  const [storageFolderId, setStorageFolderId] = useState(account.storage_folder_id ?? '')

  const trimmedRows = rows.map((row) => ({ ...row, id: row.id.trim() }))
  const nonEmptyIds = trimmedRows.filter((row) => row.id.length > 0)
  const invalidRootIds = nonEmptyIds.filter((row) => !DRIVE_ID_PATTERN.test(row.id))
  const trimmedStorageId = storageFolderId.trim()
  const storageInvalid = trimmedStorageId.length > 0 && !DRIVE_ID_PATTERN.test(trimmedStorageId)

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
    setRoots.mutate(
      {
        root_folder_ids: nonEmptyIds.map((row) => row.id),
        storage_folder_id: trimmedStorageId.length > 0 ? trimmedStorageId : null,
      },
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

      <div className="space-y-2">
        <Button onClick={save} disabled={!canSave}>
          Salva
        </Button>
        <ActionError error={setRoots.error} />
      </div>
    </div>
  )
}
