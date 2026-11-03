import { useCanWrite } from '@/lib/auth'

/**
 * The sentence a Google consent flow came back with (`?outcome=`), on whichever page the
 * callback landed: Impostazioni → Gmail or → Drive (which a collaboratore opens too since
 * REB-457; a readonly person reads the Drive one above the settings gate), the Home,
 * «Primi passi». The sentence is looked up by the caller in the one fixed table
 * (`consentOutcome`, `features/consent/outcome.ts`), never rendered from the address bar.
 *
 * `riprova` is where «Riprova» leads when the outcome asks for one: today only
 * `session_expired`, a consent that came back after the session had run out (REB-446), where
 * starting the flow again is all that is left to do. A plain anchor, because the flow
 * leaves for Google. Not offered to a readonly person, who could not have started the
 * flow and whose start would be refused.
 */
export function ConsentOutcome({
  message,
  retry,
  className,
}: {
  message: string | null
  retry: string | null
  className?: string
}) {
  const canWrite = useCanWrite()
  if (!message) return null
  return (
    <p role="status" className={`bg-muted/50 border px-3 py-2 text-sm ${className ?? ''}`.trim()}>
      {message}
      {retry && canWrite ? (
        <>
          {' '}
          <a href={retry} className="font-medium underline underline-offset-4">
            Riprova
          </a>
        </>
      ) : null}
    </p>
  )
}
