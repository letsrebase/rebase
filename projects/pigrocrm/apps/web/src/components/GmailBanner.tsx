import { Link } from '@tanstack/react-router'
import { useGmailHealth } from '@/features/gmail/queries'

/**
 * The Gmail problem band, persistent, across the whole authenticated shell.
 *
 * A toast that scrolls away is a silent failure with extra steps: a consent that was
 * revoked stays revoked until somebody re-authorises, so the notice stays on screen
 * until the state that produced it is gone. There is nothing to dismiss, on purpose --
 * a close button would be a way to hide a stopped sync from yourself.
 *
 * Three causes, three texts, and none of them composed here. `GmailHealth.banner` and
 * `banner_text` arrive already decided by `GoogleAccountService.health`, which knows
 * the order of actionability (revoked, then expired, then expiring, then a missing
 * `gmail.readonly`) and owns the wording; the REST surface and the MCP surface read the
 * same two fields. Re-deriving a sentence in the browser is precisely how the three
 * interfaces start telling the same person different things about one credential.
 *
 * Held in no state of its own. The band is a function of the latest health response, so
 * the moment a re-authorisation makes `banner_text` null the band is gone on the next
 * render -- the stale-error defect `CostCategoriesPanel` and `RatesPanel` were both
 * fixed for, which a *persistent* banner is the easiest possible shape to reintroduce.
 *
 * Mounted in `routes/app.tsx` behind its session guard and not in `__root.tsx`: an
 * unauthenticated visitor has no `google_accounts` row to have an opinion about, and
 * the health query would 401 on every load of the login screen.
 */
export function GmailBanner() {
  const health = useGmailHealth()

  // The failure branch first, the same order every panel in this codebase now uses:
  // on an error `isPending` is false while `data` is still undefined, so the branches
  // below have to be read knowing which one a failed read falls into. Here all three
  // answers are the same -- render nothing. That is deliberate and not laziness: this
  // component is on every page of the application, and a blip in one query must not
  // put a red bar over an otherwise working CRM. Impostazioni -> Gmail is where a
  // failed read of the Gmail state is reported, with the server's own sentence.
  if (health.isError || health.isPending) return null

  const text = health.data?.banner_text
  if (!text) return null

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 border-b border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive"
    >
      <span>{text}</span>
      {/* The cure is always on the same page: re-authorise, or reconnect the mailbox. */}
      <Link to="/app/settings/gmail" className="underline underline-offset-2">
        Vai a Impostazioni → Gmail
      </Link>
    </div>
  )
}
