import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { api, toProblem, unwrap } from '@/lib/api'
import { changesBetween, draftFrom, type Draft, type SpaceSettingsUpdate } from './spaceChanges'

export const SPACE_SETTINGS_KEY = ['settings', 'space'] as const

function Origin({ name, overridden }: { name: string; overridden: string[] }) {
  return (
    <span className="text-muted-foreground text-xs">
      {overridden.includes(name) ? 'impostato qui' : "dall'ambiente"}
    </span>
  )
}

export function SpacePanel() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: SPACE_SETTINGS_KEY,
    queryFn: () => unwrap(api.GET('/api/settings/space')),
  })
  // The form is the server's payload with the person's edits laid over it: no copy to
  // keep in sync, nothing to reset but the edits.
  const [edits, setEdits] = useState<Partial<Draft>>({})

  const save = useMutation({
    mutationFn: (body: SpaceSettingsUpdate) => unwrap(api.PUT('/api/settings/space', { body })),
    onSuccess: (saved) => {
      queryClient.setQueryData(SPACE_SETTINGS_KEY, saved)
      setEdits({})
      toast.success('Impostazioni salvate')
    },
    onError: (error) => toast.error(toProblem(error).detail),
  })

  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (!query.data) return <p className="text-muted-foreground text-sm">Caricamento…</p>

  const settings = query.data
  const draft: Draft = { ...draftFrom(settings), ...edits }
  const changes = changesBetween(settings, draft)
  const dirty = Object.keys(changes).length > 0

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (dirty) save.mutate(changes)
  }

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setEdits((current) => ({ ...current, [key]: value }))

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{settings.spazio ? `Spazio ${settings.spazio}` : 'Installazione radice'}</CardTitle>
          <CardDescription>
            {settings.public_url
              ? `Risponde su ${settings.public_url}. `
              : 'Nessun indirizzo pubblico configurato. '}
            Ogni valore qui sotto vale per questo spazio e prevale su quello dell'ambiente del
            server.
          </CardDescription>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Google: Gmail e Drive</CardTitle>
          <CardDescription>
            Un client OAuth tuo, dalla console Google Cloud. Registra lì i due redirect qui
            sotto. La chiave che cifra i token viene generata da sola al primo salvataggio.
            {settings.gmail_configurato
              ? ' Gmail e Drive sono attivi per questo spazio.'
              : ' Finché manca qualcosa, Gmail e Drive non compaiono.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="google_client_id">Client ID</Label>
              <Origin name="google_client_id" overridden={settings.sovrascritte} />
            </div>
            <Input
              id="google_client_id"
              autoComplete="off"
              value={draft.google_client_id}
              onChange={(event) => set('google_client_id', event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="google_client_secret">Client secret</Label>
              <span className="text-muted-foreground text-xs">
                {settings.google_client_secret_impostato ? 'impostato, non mostrato' : 'non impostato'}
              </span>
            </div>
            <Input
              id="google_client_secret"
              type="password"
              autoComplete="off"
              placeholder={settings.google_client_secret_impostato ? '••••••••' : ''}
              value={draft.google_client_secret}
              onChange={(event) => set('google_client_secret', event.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="google_app_unverified"
              checked={draft.google_app_unverified}
              onCheckedChange={(checked) => set('google_app_unverified', checked === true)}
            />
            <Label htmlFor="google_app_unverified">
              Il client Google è ancora in "Testing" (i token scadono dopo 7 giorni)
            </Label>
          </div>
          {settings.redirect_uri_gmail && (
            <dl className="text-sm">
              <dt className="text-muted-foreground">Redirect URI da registrare su Google</dt>
              <dd>
                <code className="break-all">{settings.redirect_uri_gmail}</code>
              </dd>
              <dd>
                <code className="break-all">{settings.redirect_uri_drive}</code>
              </dd>
            </dl>
          )}
          <p className="text-muted-foreground text-xs">
            Chiave dei token:{' '}
            {settings.google_token_key_impostata ? 'presente' : 'sarà generata al salvataggio del client'}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Documenti</CardTitle>
          <CardDescription>
            Dove finiscono i PDF che il CRM produce: sul disco del server, o sul Google Drive
            collegato in Impostazioni → Google Drive.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="storage_backend">Archivio</Label>
            <Origin name="storage_backend" overridden={settings.sovrascritte} />
          </div>
          <select
            id="storage_backend"
            className="border-input h-8 w-full border bg-transparent px-2 text-sm"
            value={draft.storage_backend}
            onChange={(event) => set('storage_backend', event.target.value as Draft['storage_backend'])}
          >
            <option value="local">Disco del server</option>
            <option value="gdrive">Google Drive</option>
          </select>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Agente</CardTitle>
          <CardDescription>
            Con l'accesso completo un token personale può anche emettere fatture e chiudere
            periodi: operazioni che non si annullano.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <Checkbox
              id="mcp_full_access"
              checked={draft.mcp_full_access}
              onCheckedChange={(checked) => set('mcp_full_access', checked === true)}
            />
            <Label htmlFor="mcp_full_access">Accesso completo per i token dell'agente</Label>
            <Origin name="mcp_full_access" overridden={settings.sovrascritte} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Gmail</CardTitle>
          <CardDescription>
            Quanti giorni di posta caricare al primo collegamento della casella.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {(
            [
              ['gmail_backfill_days', 'Giorni di posta al primo collegamento', 1, 3650],
            ] as const
          ).map(([key, label, min, max]) => (
            <div key={key} className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor={key}>{label}</Label>
                <Origin name={key} overridden={settings.sovrascritte} />
              </div>
              <Input
                id={key}
                type="number"
                min={min}
                max={max}
                value={draft[key]}
                onChange={(event) => set(key, event.target.value)}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!dirty || save.isPending}>
          {save.isPending ? 'Salvataggio…' : 'Salva'}
        </Button>
        {dirty && (
          <Button type="button" variant="ghost" onClick={() => setEdits({})}>
            Annulla
          </Button>
        )}
      </div>
    </form>
  )
}
