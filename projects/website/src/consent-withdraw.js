/* The "Ritira il consenso" links, on pages that carry no tracker.
 *
 * privacy.html cannot load consent.js (it has no tracker to gate, and `pixel.test.ts`
 * holds that), so withdrawing lives here, against the same storage keys: the current
 * one and the one it had before REB-668. `consent.test.ts` drives both scripts in one
 * DOM and fails when the keys stop agreeing.
 *
 * A click clears a stored yes and reloads, so the notice comes back on the next page
 * that carries one. It also removes what the trackers left behind after that yes,
 * which is more than the decision itself: PostHog's `ph_*` cookie and its `ph_*`
 * entries in localStorage and sessionStorage, and the ad pixel's `__obref` cookie.
 * Only a stored yes, never a denied: a refusal has to stay final, or this link would
 * put the notice back in front of a visitor who already said no.
 */
;(function () {
  var STORAGE_KEY = 'rebase.consent'
  var LEGACY_STORAGE_KEY = 'orbiters.consent'
  var GRANTED = 'granted'
  var COOKIE_DOMAIN = '.letsrebase.com'

  function isTrackerName(name) {
    return name === '__obref' || name.indexOf('ph_') === 0
  }

  /* A cookie is removed only by the (name, domain, path) it was set with, and the two
     trackers set theirs on the top-level domain, so it is expired on that domain and on
     this host both. */
  function clearCookies() {
    var domains = ['', '; Domain=' + window.location.hostname, '; Domain=' + COOKIE_DOMAIN]
    document.cookie.split(';').forEach(function (pair) {
      var name = pair.split('=')[0].trim()
      if (!isTrackerName(name)) return
      domains.forEach(function (domain) {
        document.cookie = name + '=; Max-Age=0; Path=/' + domain
      })
    })
  }

  function clearStorage(storage) {
    var names = []
    for (var i = 0; i < storage.length; i++) names.push(storage.key(i))
    names.forEach(function (name) {
      if (name && name.indexOf('ph_') === 0) storage.removeItem(name)
    })
  }

  function withdraw() {
    try {
      var stored = window.localStorage.getItem(STORAGE_KEY) || window.localStorage.getItem(LEGACY_STORAGE_KEY)
      if (stored === GRANTED) {
        window.localStorage.removeItem(STORAGE_KEY)
        window.localStorage.removeItem(LEGACY_STORAGE_KEY)
        clearStorage(window.localStorage)
        clearStorage(window.sessionStorage)
        clearCookies()
      }
    } catch {
      /* Nothing to be done: the decision stands for as long as the session does. */
    }
    window.location.reload()
  }

  document.querySelectorAll('[data-consent-withdraw]').forEach(function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault()
      withdraw()
    })
  })
})()
