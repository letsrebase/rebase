/* Sends a signed-in visitor from `Accedi` straight to their own area (REB-349).
 *
 * Lorenzo, 2026-09-22: the Accedi button on the landing always sends a visitor to
 * login even with a session already open; it should send them straight to their own
 * area instead, in a way that is smart and safe. The hub already answers
 * exactly this question: `GET /api/hub/me` is same-origin and credentialed, and
 * answers the signed-in person's `Me` or a 401 when there is none (hub's own
 * `useMe`, "not signed in, not an error"). This script asks that same question on
 * every page and, only on a real answer, points the login link at the door that
 * answers already knows: `/hub/me` for a member, `/hub/admin` for an admin
 * (`Me.role`, `apps/web/src/lib/api.ts`).
 *
 * Nothing new is read or stored here: no endpoint of its own, no cookie of its own,
 * the static pages never hold the session, only the one redirect target this run
 * computes and forgets. A signed-out visitor's browser sees the same single 401 it
 * would have sent anyway, and both links stay exactly where they are today,
 * `/hub/login`.
 *
 * The fetch is asynchronous and `utm.js`'s `carryUtm()` is not: on `/` and
 * `/pigrocrm`, `landing.js` has already appended `?da=...` (and any campaign keys)
 * to the login link by the time this script's answer comes back, so the selector
 * matches the link by its path prefix rather than its exact value, and the rewrite
 * changes only the path, keeping whatever query string is already there.
 *
 * An IIFE publishing `window.__session`, like `utm.js` and `field.js`: no page calls
 * it, it runs on its own, and its test loads it with `new Function`.
 */
;(function () {
  var SELECTOR = 'a[href="/hub/login"], a[href^="/hub/login?"]'

  /** `/hub/admin` for an admin, `/hub/me` for anyone else the hub already knows. */
  function landingRoute(me) {
    return me && me.role === 'admin' ? '/hub/admin' : '/hub/me'
  }

  /** Points every `Accedi`/«Entra nella tua area» link at `to`'s path, keeping
   *  whatever query string `utm.js` already gave it. Returns how many links were
   *  rewritten, for the test. */
  function rewrite(root, to) {
    var rewritten = 0
    ;(root || document).querySelectorAll(SELECTOR).forEach(function (link) {
      var url = new URL(link.getAttribute('href'), window.location.origin)
      url.pathname = to
      link.setAttribute('href', url.pathname + url.search + url.hash)
      rewritten += 1
    })
    return rewritten
  }

  function start() {
    if (typeof window.fetch !== 'function') return
    window
      .fetch('/api/hub/me', { credentials: 'same-origin' })
      .then(function (response) {
        return response.ok ? response.json() : null
      })
      .then(function (me) {
        if (me) rewrite(document, landingRoute(me))
      })
      .catch(function () {
        /* Offline, blocked, or the hub is down: the visitor keeps the login link. */
      })
  }

  window.__session = { start: start, rewrite: rewrite, landingRoute: landingRoute, SELECTOR: SELECTOR }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start)
  } else {
    start()
  }
})()
