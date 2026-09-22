/* Carries a visitor's campaign into the hub, wherever the site opens a door there.
 *
 * Since ORB-166 an ad can land on the site with the six UTM keys in the URL, and the
 * doors into the hub are plain paths, so the first click used to lose them. `carryUtm`
 * appends the keys the page was opened with to every link into `/hub/` (never
 * overriding one a link already carries, `perk` and the like untouched) and writes them
 * in `sessionStorage` under `orbiters.utm`, the key the hub's `resolveUtm` reads when
 * its own URL has none. Session storage on purpose: it dies with the tab, and the only
 * thing it holds is the campaign, never the person. Without JavaScript the links are
 * what the markup says and the attribution is lost, which is what happened before and
 * is the honest fallback.
 *
 * Campaign or not, every door also says which page it is on (ORB-167): `da=home` from
 * `/`, `da=pigrocrm` from `/pigrocrm`, so the hub can tell who came through which
 * page. Remembered for the tab as `orbiters.da`.
 *
 * An IIFE publishing `window.__utm`, like `field.js` and `typewriter.js`: `landing.js`,
 * the page script that calls it, stays self-contained and its tests can load it with
 * `new Function`, and this one only has to run first -- module scripts execute in
 * document order, which is enough.
 */
;(function () {
  var UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'utm_id']
  var UTM_STORAGE_KEY = 'orbiters.utm'
  var ORIGIN_STORAGE_KEY = 'orbiters.da'

  /** The page's slug for `da=`: `home` for the front door, the path's own name otherwise. */
  function pageSlug(pathname) {
    var slug = (pathname || window.location.pathname).replace(/^\/+|\/+$/g, '')
    return (slug || 'home').toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 40)
  }

  /** The UTM keys in `search`, trimmed and capped at 200 characters like the hub does. */
  function readUtm(search) {
    var params = new URLSearchParams(search)
    var utm = new URLSearchParams()
    UTM_KEYS.forEach(function (key) {
      var value = (params.get(key) || '').trim().slice(0, 200)
      if (value) utm.set(key, value)
    })
    return utm
  }

  /** Appends the page's UTM keys and its own `da=` slug to every link into the hub, and
   *  remembers both for the tab. Returns how many links were rewritten, for the test. */
  function carryUtm(root, search, pathname) {
    var utm = readUtm(search === undefined ? window.location.search : search)
    var keys = Array.from(utm.keys())
    var page = pageSlug(pathname)
    try {
      if (keys.length) window.sessionStorage.setItem(UTM_STORAGE_KEY, utm.toString())
      window.sessionStorage.setItem(ORIGIN_STORAGE_KEY, page)
    } catch {
      /* storage refused: the links still carry everything */
    }
    var rewritten = 0
    ;(root || document).querySelectorAll('a[href^="/hub/"]').forEach(function (link) {
      var url = new URL(link.getAttribute('href'), window.location.origin)
      keys.forEach(function (key) {
        if (!url.searchParams.has(key)) url.searchParams.set(key, utm.get(key))
      })
      if (!url.searchParams.has('da')) url.searchParams.set('da', page)
      link.setAttribute('href', url.pathname + url.search + url.hash)
      rewritten += 1
    })
    return rewritten
  }

  window.__utm = { carryUtm: carryUtm, readUtm: readUtm, pageSlug: pageSlug, UTM_KEYS: UTM_KEYS, UTM_STORAGE_KEY: UTM_STORAGE_KEY, ORIGIN_STORAGE_KEY: ORIGIN_STORAGE_KEY }
})()
