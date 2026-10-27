import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { toProblem } from '@/lib/api'
import { formatInstant } from '@/features/gmail/instants'
import {
  SLACK_OAUTH_START,
  useDisconnectSlack,
  useLinkSlackUser,
  useRematchSlack,
  useSetSlackChannel,
  useSlackChannels,
  useSlackHealth,
  useUnlinkSlackUser,
  type SlackMember,
  type SlackWorkspace,
} from './queries'

const NO_CHANNEL = '__none__'

const MATCH_LABEL: Record<SlackMember['state'], string> = {
  matched: 'Riconosciuto dall’email',
  manual: 'Abbinato a mano',
  unmatched: 'Non trovato',
}

const MATCH_TONE: Record<SlackMember['state'], StatusTone> = {
  matched: 'ink',
  manual: 'accent',
  unmatched: 'gold',
}

const STATUS_LABEL: Record<SlackWorkspace['status'], string> = {
  active: 'attivo',
  revoked: 'accesso revocato',
  disconnected: 'scollegato',
}

/**
 * Impostazioni → Slack (REB-739), the Slack twin of `DrivePanel`, in the same order and for
 * the same reason: a control rendered from a health response that never arrived would offer
 * to change a setting nobody managed to show.
 *
 * 1. **The read failed.** The banner, and nothing under it.
 * 2. **`configured: false`.** No Slack app on this installation: an explanation, no button.
 * 3. **Configured, nothing connected (or deliberately disconnected).** One link to the
 *    install, which leaves for Slack.
 * 4. **Connected.** The workspace, what is wrong with it if anything (a revoked token, a
 *    key that no longer opens it), every user with how they were matched and the way to fix a
 *    mismatch, the channel for the space-wide reminders, and «Scollega Slack».
 *
 * Admins only: the page lists every user of the space, which is why the tab is an admin's
 * (`SETTINGS_TAB_MIN_ROLE`) and the API refuses anyone else. A person's own state is the hint
 * on Notifiche (`SlackNotice`).
 */
export function SlackPanel() {
  const query = useSlackHealth()

  // The error branch before the loading one: on a failure `isPending` is false while `data`
  // is still undefined, so a single guard would answer a failed read with a spinner.
  if (query.isError) return <QueryErrorBanner error={query.error} />
  if (query.isPending || !query.data) return <Skeleton className="h-64 w-full" />

  const health = query.data
  if (!health.configured) return <NotConfigured />
  const workspace = health.workspace
  if (workspace === null || workspace.status === 'disconnected') {
    return <NotConnected workspace={workspace} />
  }
  return (
    <Connected
      workspace={workspace}
      members={health.members ?? []}
      problem={health.problem ?? null}
    />
  )
}

function ActionError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      {toProblem(error).detail}
    </p>
  )
}

function NotConfigured() {
  return (
    <section className="max-w-3xl space-y-2">
      <h2 className="text-lg font-medium">Slack</h2>
      <p className="text-muted-foreground">
        Slack non è configurato su questa installazione. Per attivarlo servono un’app Slack e le
        variabili <code>PIGROCRM_SLACK_CLIENT_ID</code>, <code>PIGROCRM_SLACK_CLIENT_SECRET</code>{' '}
        e <code>PIGROCRM_SLACK_TOKEN_KEY</code>.
      </p>
    </section>
  )
}

function NotConnected({ workspace }: { workspace: SlackWorkspace | null }) {
  return (
    <section className="max-w-3xl space-y-3">
      <h2 className="text-lg font-medium">Nessun workspace Slack collegato</h2>
      {workspace ? (
        <p className="text-sm text-muted-foreground">
          Il workspace {workspace.team_name} è stato scollegato il{' '}
          {formatInstant(workspace.disconnected_at)}.
        </p>
      ) : null}
      <p className="text-muted-foreground">
        Collegando Slack, i promemoria che scegli arrivano agli utenti dello spazio come messaggio
        diretto, e quelli che riguardano tutti possono andare anche in un canale. PigroCRM
        riconosce ogni utente dalla sua email e non legge nient’altro del workspace.
      </p>
      <Button asChild>
        <a href={SLACK_OAUTH_START}>Collega Slack</a>
      </Button>
    </section>
  )
}

function Connected({
  workspace,
  members,
  problem,
}: {
  workspace: SlackWorkspace
  members: SlackMember[]
  problem: string | null
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const disconnect = useDisconnectSlack()
  const rematch = useRematchSlack()
  const unmatched = members.filter((member) => member.state === 'unmatched').length

  return (
    <section className="max-w-3xl space-y-8">
      <header className="space-y-1">
        <h2 className="text-lg font-medium">Slack collegato</h2>
        <p className="font-medium">{workspace.team_name}</p>
        <p className="text-sm text-muted-foreground">
          Stato: {STATUS_LABEL[workspace.status]} · collegato il{' '}
          {formatInstant(workspace.connected_at)}
        </p>
      </header>

      {workspace.status === 'revoked' ? (
        <div
          role="alert"
          className="space-y-2 border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          <p>{workspace.last_error ?? 'Slack ha revocato l’accesso di PigroCRM.'}</p>
          <Button asChild variant="outline" size="sm">
            <a href={SLACK_OAUTH_START}>Ricollega Slack</a>
          </Button>
        </div>
      ) : null}
      {problem ? (
        <div
          role="alert"
          className="space-y-2 border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          <p>{problem}</p>
          {/* Reconnecting the same workspace keeps the matches made by hand; disconnecting
              first would drop them. */}
          <Button asChild variant="outline" size="sm">
            <a href={SLACK_OAUTH_START}>Ricollega Slack</a>
          </Button>
        </div>
      ) : null}

      <section aria-labelledby="slack-users" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h3 id="slack-users" className="font-medium">
              Utenti
            </h3>
            <p className="text-sm text-muted-foreground">
              Ogni utente riceve i suoi promemoria nel messaggio diretto del membro di Slack che ha
              il suo stesso indirizzo.
              {unmatched > 0
                ? ` ${unmatched === 1 ? 'Un utente non è stato trovato' : `${unmatched} utenti non sono stati trovati`} e su Slack non riceve nulla finché non lo correggi.`
                : ''}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={rematch.isPending || workspace.status !== 'active'}
            onClick={() => rematch.mutate()}
          >
            Cerca di nuovo
          </Button>
        </div>
        <ActionError error={rematch.error} />
        {members.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun utente attivo in questo spazio.</p>
        ) : (
          <ul className="divide-y border" aria-label="Utenti e Slack">
            {members.map((member) => (
              <MemberRow key={member.user_id} member={member} active={workspace.status === 'active'} />
            ))}
          </ul>
        )}
      </section>

      <ChannelSection workspace={workspace} />

      <div className="space-y-2 border-t pt-4">
        <Button variant="destructive" onClick={() => setConfirmOpen(true)}>
          Scollega Slack
        </Button>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Scollegare Slack?</DialogTitle>
            <DialogDescription>
              I promemoria non arriveranno più su Slack, né ai singoli utenti né nel canale, finché
              non colleghi di nuovo un workspace. Gli abbinamenti degli utenti, quelli fatti a mano
              compresi, vanno persi. Per togliere l’app anche dal workspace usa le impostazioni di
              Slack.
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
              Scollega Slack
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function MemberRow({ member, active }: { member: SlackMember; active: boolean }) {
  const [editing, setEditing] = useState(false)
  const [address, setAddress] = useState('')
  const link = useLinkSlackUser()
  const unlink = useUnlinkSlackUser()

  const save = () => {
    link.mutate(
      { userId: member.user_id, email: address.trim() },
      {
        onSuccess: () => {
          setEditing(false)
          setAddress('')
          toast.success(`${member.nome} abbinato a ${address.trim()}.`)
        },
      },
    )
  }

  return (
    <li className="space-y-2 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{member.nome}</p>
          <p className="truncate text-sm text-muted-foreground">
            {member.email}
            {member.state === 'manual' && member.slack_email
              ? ` · in Slack: ${member.slack_email}`
              : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill tone={MATCH_TONE[member.state]}>{MATCH_LABEL[member.state]}</StatusPill>
          {member.state === 'manual' ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={unlink.isPending || !active}
              aria-label={`Ripristina l’abbinamento di ${member.nome}`}
              onClick={() => unlink.mutate(member.user_id)}
            >
              Ripristina
            </Button>
          ) : null}
          {!editing ? (
            <Button
              variant="outline"
              size="sm"
              disabled={!active}
              aria-label={`Correggi l’abbinamento di ${member.nome}`}
              onClick={() => setEditing(true)}
            >
              Correggi
            </Button>
          ) : null}
        </div>
      </div>
      {member.state === 'unmatched' ? (
        <p className="text-sm text-muted-foreground">
          Nessuno nel workspace ha questo indirizzo: su Slack non riceve nulla.
        </p>
      ) : null}
      {editing ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          <div className="space-y-1">
            <Label htmlFor={`slack-address-${member.user_id}`}>
              Indirizzo di {member.nome} in Slack
            </Label>
            <Input
              id={`slack-address-${member.user_id}`}
              type="email"
              className="w-72"
              placeholder="nome@azienda.example"
              value={address}
              autoFocus
              onChange={(event) => setAddress(event.target.value)}
            />
          </div>
          <Button type="submit" size="sm" disabled={link.isPending || address.trim() === ''}>
            Abbina
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setEditing(false)
              setAddress('')
              link.reset()
            }}
          >
            Annulla
          </Button>
        </form>
      ) : null}
      <ActionError error={link.error ?? unlink.error} />
    </li>
  )
}

function ChannelSection({ workspace }: { workspace: SlackWorkspace }) {
  const [picking, setPicking] = useState(false)
  const channels = useSlackChannels(picking)
  const setChannel = useSetSlackChannel()

  const choose = (value: string) =>
    setChannel.mutate(value === NO_CHANNEL ? null : value, {
      onSuccess: () => setPicking(false),
    })

  return (
    <section aria-labelledby="slack-channel" className="space-y-3">
      <div className="space-y-1">
        <h3 id="slack-channel" className="font-medium">
          Canale dei promemoria dello spazio
        </h3>
        <p className="text-sm text-muted-foreground">
          I promemoria che riguardano tutti gli amministratori (la soglia del forfettario, i mesi non
          chiusi, i collegamenti in difficoltà) vanno anche in un canale, oltre ai messaggi diretti.{' '}
          {workspace.channel_name
            ? `Ora: #${workspace.channel_name}.`
            : 'Ora: nessun canale, solo i messaggi diretti.'}
        </p>
      </div>
      {picking ? (
        <div className="flex flex-wrap items-center gap-2">
          {channels.isError ? (
            <QueryErrorBanner error={channels.error} />
          ) : channels.isPending ? (
            <Skeleton className="h-9 w-72" />
          ) : (
            <Select
              value={workspace.channel_id ?? NO_CHANNEL}
              onValueChange={choose}
              disabled={setChannel.isPending}
            >
              <SelectTrigger className="w-72" aria-label="Canale dei promemoria dello spazio">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_CHANNEL}>Nessun canale</SelectItem>
                {channels.data.map((channel) => (
                  <SelectItem key={channel.id} value={channel.id}>
                    {channel.is_private ? `${channel.name} (privato)` : `#${channel.name}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant="ghost" size="sm" onClick={() => setPicking(false)}>
            Annulla
          </Button>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          disabled={workspace.status !== 'active'}
          onClick={() => setPicking(true)}
        >
          {workspace.channel_name ? 'Cambia canale' : 'Scegli un canale'}
        </Button>
      )}
      <ActionError error={setChannel.error} />
    </section>
  )
}
