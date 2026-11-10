import { Link } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { toProblem } from '@/lib/api'
import { formatInstant } from '@/features/gmail/instants'
import {
  useChangeMailboxPassword,
  useConnectMailbox,
  useDisconnectMailbox,
  useMailboxes,
  type Mailbox,
  type MailboxCreate,
} from './mailboxesQueries'

const HEALTH_LABEL: Record<Mailbox['health'], string> = {
  active: 'Funziona',
  failing: 'Non risponde',
  auth_failed: 'Password rifiutata',
  disconnected: 'Scollegata',
}

const HEALTH_TONE: Record<Mailbox['health'], StatusTone> = {
  active: 'ink',
  failing: 'gold',
  auth_failed: 'danger',
  disconnected: 'muted',
}

const PURPOSE_LABEL: Record<Mailbox['purposes'][number], string> = {
  correspondence: 'Corrispondenza',
  billing: 'Fatture e documenti',
}

type Security = MailboxCreate['security']
const DEFAULT_PORT: Record<Security, string> = { tls: '993', starttls: '143' }

function ActionError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      {toProblem(error).detail}
    </p>
  )
}

/**
 * The «Caselle» settings tab (REB-1044): the IMAP and PEC mailboxes PigroCRM reads, beside Gmail's.
 *
 * A PEC is an IMAP mailbox and the API does not tell them apart (`ImapMailboxCreate`), so
 * neither does the form: host, port, security, user and password connect either.
 * `GET /api/mailboxes` is the one list; a Gmail row is read-only here and points at its own tab,
 * because its consent and its disconnect are Gmail's (`GmailPanel`). The tab is an admin's
 * (`SETTINGS_TAB_MIN_ROLE`), so a mailbox connected here is the space's (`space_owned`).
 * A refused password is the one state with an action of its own: it is not tried again until a
 * person types a new one (`mail-sync` says so on Esecuzioni, with a link here).
 */
export function MailboxesPanel() {
  const query = useMailboxes()
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) return <LoadingState />

  return (
    <div className="max-w-3xl space-y-8">
      <section aria-labelledby="mailboxes-list" className="space-y-3">
        <div className="space-y-1">
          <h2 id="mailboxes-list" className="text-lg font-medium">
            Caselle collegate
          </h2>
          <p className="text-sm text-muted-foreground">
            PigroCRM legge queste caselle a ogni giro della sincronizzazione. La password serve solo
            a entrare: non viene mai mostrata di nuovo.
          </p>
        </div>
        {query.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nessuna casella collegata. Collega una casella IMAP o una PEC qui sotto, oppure Gmail
            dalla sua scheda.
          </p>
        ) : (
          <ul className="divide-y border" aria-label="Caselle collegate">
            {query.data.map((mailbox) => (
              <MailboxRow key={mailbox.id} mailbox={mailbox} />
            ))}
          </ul>
        )}
      </section>
      <ConnectForm />
    </div>
  )
}

function MailboxRow({ mailbox }: { mailbox: Mailbox }) {
  const [rekeying, setRekeying] = useState(false)
  const [password, setPassword] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const change = useChangeMailboxPassword()
  const disconnect = useDisconnectMailbox()
  const isGmail = mailbox.provider === 'gmail'
  const name = mailbox.address ?? (isGmail ? 'Gmail' : 'Casella senza indirizzo')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    change.mutate(
      { id: mailbox.id, password },
      {
        onSuccess: () => {
          setRekeying(false)
          setPassword('')
          toast.success(`Password di ${name} aggiornata.`)
        },
      },
    )
  }

  return (
    <li className="space-y-2 px-3 py-3" data-mailbox={mailbox.id}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium">{name}</p>
          <p className="text-sm text-muted-foreground">
            {isGmail ? 'Gmail' : `${mailbox.host}:${mailbox.port}`}
            {' · '}
            {mailbox.space_owned ? 'dello spazio' : 'personale'}
            {' · '}
            {mailbox.purposes.map((purpose) => PURPOSE_LABEL[purpose]).join(', ')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill tone={HEALTH_TONE[mailbox.health]}>{HEALTH_LABEL[mailbox.health]}</StatusPill>
          {isGmail ? (
            <Button asChild variant="outline" size="sm">
              <Link to="/app/settings/gmail" aria-label={`Gestisci ${name} in Gmail`}>
                Gestisci in Gmail
              </Link>
            </Button>
          ) : mailbox.health !== 'disconnected' ? (
            <>
              {!rekeying ? (
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={`Cambia la password di ${name}`}
                  onClick={() => setRekeying(true)}
                >
                  Cambia password
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Scollega ${name}`}
                onClick={() => setConfirmOpen(true)}
              >
                Scollega
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {mailbox.health === 'auth_failed' ? (
        <p className="text-sm text-muted-foreground">
          Il server non accetta più la password: PigroCRM non riprova finché non ne scrivi una
          nuova.
          {mailbox.last_error_at ? ` Rifiutata il ${formatInstant(mailbox.last_error_at)}.` : ''}
        </p>
      ) : null}
      {mailbox.health === 'failing' ? (
        <p className="text-sm text-muted-foreground">
          {mailbox.last_error ?? 'Il server non risponde.'}
          {mailbox.failing_since
            ? ` Non risponde dal ${formatInstant(mailbox.failing_since)}: PigroCRM riprova al prossimo giro.`
            : ''}
        </p>
      ) : null}
      {mailbox.health === 'active' && mailbox.last_error_at ? (
        <p className="text-sm text-muted-foreground">
          Ultimo errore il {formatInstant(mailbox.last_error_at)}, poi è tornata a funzionare.
        </p>
      ) : null}
      {rekeying ? (
        <form className="flex flex-wrap items-end gap-2" onSubmit={submit}>
          <div className="space-y-1">
            <Label htmlFor={`mailbox-password-${mailbox.id}`}>Nuova password di {name}</Label>
            <Input
              id={`mailbox-password-${mailbox.id}`}
              type="password"
              autoComplete="new-password"
              className="w-72"
              value={password}
              autoFocus
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <Button type="submit" size="sm" disabled={change.isPending || password === ''}>
            Salva
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setRekeying(false)
              setPassword('')
              change.reset()
            }}
          >
            Annulla
          </Button>
        </form>
      ) : null}
      <ActionError error={change.error} />
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Scollegare {name}?</DialogTitle>
            <DialogDescription>
              PigroCRM smette di leggerla e dimentica la password. Quello che ha già ricevuto resta
              dov’è.
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
                disconnect.mutate(mailbox.id, { onSuccess: () => setConfirmOpen(false) })
              }
            >
              Scollega
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </li>
  )
}

function ConnectForm() {
  const connect = useConnectMailbox()
  const [address, setAddress] = useState('')
  const [host, setHost] = useState('')
  const [security, setSecurity] = useState<Security>('tls')
  const [port, setPort] = useState(DEFAULT_PORT.tls)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [spaceOwned, setSpaceOwned] = useState(true)
  const [correspondence, setCorrespondence] = useState(true)
  const [billing, setBilling] = useState(false)

  const purposes: MailboxCreate['purposes'] = [
    ...(correspondence ? (['correspondence'] as const) : []),
    ...(billing && spaceOwned ? (['billing'] as const) : []),
  ]
  const portNumber = Number(port)
  const ready =
    address.trim() !== '' &&
    host.trim() !== '' &&
    username.trim() !== '' &&
    password !== '' &&
    Number.isInteger(portNumber) &&
    portNumber >= 1 &&
    portNumber <= 65535 &&
    purposes.length > 0

  const submit = (event: FormEvent) => {
    event.preventDefault()
    connect.mutate(
      {
        address: address.trim(),
        host: host.trim(),
        port: portNumber,
        security,
        username: username.trim(),
        password,
        purposes,
        space_owned: spaceOwned,
      },
      {
        onSuccess: () => {
          toast.success(`${address.trim()} collegata.`)
          setAddress('')
          setHost('')
          setUsername('')
          setPassword('')
        },
      },
    )
  }

  return (
    <section aria-labelledby="mailboxes-connect" className="space-y-3">
      <div className="space-y-1">
        <h2 id="mailboxes-connect" className="text-lg font-medium">
          Collega una casella IMAP o PEC
        </h2>
        <p className="text-sm text-muted-foreground">
          PigroCRM prova ad accedere una volta e tiene la casella solo se il server accetta la
          password. Il collegamento è sempre cifrato e il server deve essere raggiungibile da
          internet.
        </p>
      </div>
      <form className="grid max-w-xl gap-4" onSubmit={submit}>
        <div className="space-y-1">
          <Label htmlFor="mailbox-address">Indirizzo della casella</Label>
          <Input
            id="mailbox-address"
            type="email"
            placeholder="fatture@azienda.example"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-[1fr_auto_auto]">
          <div className="space-y-1">
            <Label htmlFor="mailbox-host">Server IMAP</Label>
            <Input
              id="mailbox-host"
              placeholder="imaps.pec.aruba.it"
              value={host}
              onChange={(event) => setHost(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mailbox-security">Cifratura</Label>
            <Select
              value={security}
              onValueChange={(value) => {
                const next = value as Security
                // Keep a port the person typed; move only one that is still the other default.
                if (port === DEFAULT_PORT[security]) setPort(DEFAULT_PORT[next])
                setSecurity(next)
              }}
            >
              <SelectTrigger id="mailbox-security" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tls">TLS</SelectItem>
                <SelectItem value="starttls">STARTTLS</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="mailbox-port">Porta</Label>
            <Input
              id="mailbox-port"
              inputMode="numeric"
              className="w-24"
              value={port}
              onChange={(event) => setPort(event.target.value)}
            />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="mailbox-username">Utente</Label>
            <Input
              id="mailbox-username"
              autoComplete="off"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mailbox-password">Password</Label>
            <Input
              id="mailbox-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Cosa cercare</legend>
          <div className="flex items-center gap-2">
            <Checkbox
              id="mailbox-correspondence"
              checked={correspondence}
              onCheckedChange={(value) => setCorrespondence(value === true)}
            />
            <Label htmlFor="mailbox-correspondence">
              Corrispondenza con clienti e contatti già in rubrica
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="mailbox-billing"
              checked={billing && spaceOwned}
              disabled={!spaceOwned}
              onCheckedChange={(value) => setBilling(value === true)}
            />
            <Label htmlFor="mailbox-billing">
              Fatture e documenti in allegato, da qualunque mittente
            </Label>
          </div>
        </fieldset>
        <div className="flex items-center gap-2">
          <Checkbox
            id="mailbox-space-owned"
            checked={spaceOwned}
            onCheckedChange={(value) => setSpaceOwned(value === true)}
          />
          <Label htmlFor="mailbox-space-owned">
            Casella dello spazio (condivisa, come fatture@ o una PEC)
          </Label>
        </div>
        <ActionError error={connect.error} />
        <div>
          <Button type="submit" disabled={connect.isPending || !ready}>
            Collega la casella
          </Button>
        </div>
      </form>
    </section>
  )
}
