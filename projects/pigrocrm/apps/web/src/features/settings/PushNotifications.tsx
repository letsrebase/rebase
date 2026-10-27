import { Button } from '@rebase/ui/button'
import { usePush } from '@/pwa/usePush'
import type { PushState } from '@/pwa/push'

/** What each state tells the person; the two with a button are `off` and `on`. */
const EXPLANATION: Record<Exclude<PushState, 'loading' | 'off' | 'on'>, string> = {
  error: 'Non riesco a leggere lo stato delle notifiche. Ricarica la pagina per riprovare.',
  'worker-missing':
    'Il browser non ha ancora attivato l’app per le notifiche. Ricarica la pagina e riprova.',
  unavailable: 'Le notifiche push non sono attive su questa installazione.',
  unsupported: 'Questo browser non supporta le notifiche push.',
  'ios-needs-install':
    'Su iPhone e iPad le notifiche arrivano solo all’app installata: aggiungi PigroCRM alla schermata Home e riapri questa pagina da lì.',
  denied:
    'Hai bloccato le notifiche di questo sito nel browser. Riattivale dalle impostazioni del browser e poi torna qui.',
}

/**
 * Notifiche push su questo browser (REB-738): chiede il permesso, iscrive il browser e dice
 * in che stato è. Vale per il browser in cui sei, per te soltanto: su un altro telefono o
 * computer si attiva da lì. Quali promemoria arrivano, e a che ora, si sceglie qui sotto.
 */
export function PushNotifications() {
  const { state, busy, error, subscribe, unsubscribe } = usePush()
  if (state === 'loading') return null

  return (
    <div className="flex items-start justify-between gap-4 border p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">Notifiche su questo browser</p>
        <p className="text-sm text-muted-foreground">
          {state === 'on'
            ? 'Le notifiche sono attive su questo browser: i promemoria arrivano subito, anche con PigroCRM chiuso, e un tocco apre il record.'
            : state === 'off'
              ? 'Ricevi i promemoria come notifica, anche con PigroCRM chiuso. Un tocco apre il record.'
              : EXPLANATION[state]}
        </p>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      {state === 'off' && (
        <Button type="button" size="sm" disabled={busy} onClick={() => void subscribe()}>
          Attiva
        </Button>
      )}
      {state === 'on' && (
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void unsubscribe()}>
          Disattiva
        </Button>
      )}
    </div>
  )
}
