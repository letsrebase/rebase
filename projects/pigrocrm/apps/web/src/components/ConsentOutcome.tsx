import { useCanWrite } from '@/lib/auth'

/**
 * The sentence a Google consent flow came back with (`?esito=`), on whichever page the
 * callback landed: Impostazioni → Gmail or → Drive, the Home, «Primi passi». The sentence
 * is looked up by the caller in its product's fixed table (`features/gmail/esito.ts`,
 * `features/drive/esito.ts`), never rendered from the address bar.
 *
 * `riprova` is where «Riprova» leads when the outcome asks for one: today only
 * `sessione`, a consent that came back after the session had run out (REB-446), where
 * starting the flow again is all that is left to do. A plain anchor, because the flow
 * leaves for Google. Not offered to a readonly person, who could not have started the
 * flow and whose start would be refused.
 */
export function ConsentOutcome({
  messaggio,
  riprova,
  className,
}: {
  messaggio: string | null
  riprova: string | null
  className?: string
}) {
  const canWrite = useCanWrite()
  if (!messaggio) return null
  return (
    <p role="status" className={`bg-muted/50 border px-3 py-2 text-sm ${className ?? ''}`.trim()}>
      {messaggio}
      {riprova && canWrite ? (
        <>
          {' '}
          <a href={riprova} className="font-medium underline underline-offset-4">
            Riprova
          </a>
        </>
      ) : null}
    </p>
  )
}
