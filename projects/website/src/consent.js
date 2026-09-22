/* The cookie notice, and the only thing that can load a tracker: the measurement
 * pixel and, since 2026-09-12, PostHog.
 *
 * Neither is on the page until somebody says yes. That is the whole design: both SDKs
 * are *injected* here rather than sitting in the markup, so before a decision the
 * browser makes no request to OpenAI or to PostHog at all -- no script, no cookie, no
 * ping, and nothing to explain. A snippet in the head with `oaiq('consent', false)` or
 * `posthog.opt_out_capturing()` after it would still have fetched the script and handed
 * a third party the visitor's address.
 *
 * Consequences worth knowing:
 *   - with JavaScript off, nothing here runs, so there is no notice and no tracker,
 *     which is the correct pair;
 *   - a refusal is remembered and the notice does not come back.
 *
 * The two pages that carry this are the two an ad can land on. privacy.html and
 * terms.html have no tracker and therefore nothing to ask about.
 *
 * The PostHog key and hosts are the ones in `shared/analytics/posthog.ts`, copied here
 * as literals because this file runs without a bundler; `pixel.test.ts` compares the
 * two, so they cannot drift quietly.
 */
;(function () {
  var STORAGE_KEY = 'orbiters.consent'
  var GRANTED = 'granted'
  var DENIED = 'denied'
  var PIXEL_ID = '9r6qrnPxBV8WDVGtpuaqxh'
  var SDK_URL = 'https://bzrcdn.openai.com/sdk/oaiq.min.js'
  var POSTHOG_KEY = 'phc_BEfHvXF3DHU4ZGrJc9QJFPDrR2PxXuGDnZL4Kf6XabLb'
  var POSTHOG_HOST = 'https://eu.i.posthog.com'
  var POSTHOG_ASSET_HOST = 'https://eu-assets.i.posthog.com'
  /* The same two rules as `shared/analytics/posthog.ts`: a developer's machine and the
     e2e suite (Playwright against `vite preview` on localhost) send nothing at all, and
     the preview stack's events are marked internal rather than counted as visitors.
     The pixel has no such rule because it fires only on a signup, which the suite
     never completes against a real API. */
  var SILENT_HOSTS = ['', 'localhost', '127.0.0.1', '[::1]', '0.0.0.0']
  var INTERNAL_HOSTS = /^preview\./

  function measured(hostname) {
    return SILENT_HOSTS.indexOf(hostname) === -1
  }

  function internal(hostname) {
    return INTERNAL_HOSTS.test(hostname)
  }

  /* localStorage throws rather than returning null in a browser set to block site data,
     and in Safari's private mode. A visitor whose browser refuses to remember anything
     is a visitor who sees the notice again, which is a nuisance; a page that breaks on
     it is worse. */
  function remembered() {
    try {
      return window.localStorage.getItem(STORAGE_KEY)
    } catch {
      return null
    }
  }

  function remember(decision) {
    try {
      window.localStorage.setItem(STORAGE_KEY, decision)
    } catch {
      /* Niente da fare: la scelta vale per questa visita. */
    }
  }

  /* OpenAI's own loader, minus the part that runs without being asked: the queue stub
     so nothing said between here and the SDK's arrival is lost, then the script, then
     the init. Called only from `accept`. */
  function loadPixel() {
    if (window.oaiq) return
    var queue = function () {
      queue.q.push(arguments)
    }
    queue.q = []
    window.oaiq = queue
    var script = document.createElement('script')
    script.async = true
    script.src = SDK_URL
    var first = document.getElementsByTagName('script')[0]
    if (first && first.parentNode) first.parentNode.insertBefore(script, first)
    else document.head.appendChild(script)
    window.oaiq('init', { pixelId: PIXEL_ID, debug: true })
  }

  /* PostHog's own loader, written out: a stub that queues every call made before
     `array.js` arrives, the script, then the init entry the SDK reads on load (`_i`, one
     `[key, config, name]` per instance; the SDK recognises the stub by that array, and
     `__SV` is only the official snippet's own re-entry guard, kept for fidelity).
     Called only from `accept`, like `loadPixel`. Anonymous visitors stay anonymous
     (`identified_only`): the CRM and the hub identify a person after the login, and
     the cookie is on the top-level domain so that person is this same visitor. */
  function loadPostHog() {
    if (window.posthog) return
    if (!measured(window.location.hostname)) return
    var stub = []
    stub.__SV = 1
    stub._i = []
    var methods =
      'capture identify group reset register opt_in_capturing opt_out_capturing setInternalOrTestUser'
    methods.split(' ').forEach(function (name) {
      stub[name] = function () {
        stub.push([name].concat(Array.prototype.slice.call(arguments)))
      }
    })
    stub.init = function (key, config) {
      stub._i.push([key, config, undefined])
    }
    window.posthog = stub
    var script = document.createElement('script')
    script.async = true
    script.crossOrigin = 'anonymous'
    script.src = POSTHOG_ASSET_HOST + '/static/array.js'
    var first = document.getElementsByTagName('script')[0]
    if (first && first.parentNode) first.parentNode.insertBefore(script, first)
    else document.head.appendChild(script)
    stub.init(POSTHOG_KEY, {
      api_host: POSTHOG_HOST,
      defaults: '2026-08-30',
      person_profiles: 'identified_only',
      session_recording: { maskAllInputs: true },
    })
    /* Queued on the stub and replayed by the SDK when it lands. A call rather than the
       `internal_or_test_user_hostname` option, which did not take effect when tried live
       (shared/analytics/browser.ts has the date and the version). */
    if (internal(window.location.hostname)) stub.setInternalOrTestUser()
  }

  function button(label, kind, onClick) {
    var element = document.createElement('button')
    element.type = 'button'
    element.textContent = label
    element.setAttribute('data-kind', kind)
    element.addEventListener('click', onClick)
    return element
  }

  /* Built here rather than written into both pages: with JavaScript off there is no
     tracker to consent to, so a notice in the markup would be a question about nothing.
     Text nodes only -- no innerHTML -- because that is the habit worth keeping even
     when every string is a literal. */
  function notice(onDecision) {
    var box = document.createElement('section')
    box.className = 'consent'
    box.setAttribute('role', 'region')
    box.setAttribute('aria-label', 'Cookie e misurazione')

    var text = document.createElement('p')
    text.appendChild(
      document.createTextNode(
        'Cookie di misurazione, per sapere se un annuncio funziona e come usi il sito. ',
      ),
    )
    var link = document.createElement('a')
    link.href = '/privacy'
    link.textContent = 'Dettagli'
    text.appendChild(link)
    text.appendChild(document.createTextNode('.'))
    box.appendChild(text)

    var buttons = document.createElement('div')
    buttons.className = 'consent-actions'
    buttons.appendChild(button('No', 'no', function () { onDecision(DENIED, box) }))
    buttons.appendChild(button('Va bene', 'si', function () { onDecision(GRANTED, box) }))
    box.appendChild(buttons)
    return box
  }

  /* The notice is fixed over the bottom of the viewport, and on a phone a short page
     can fit in one screen, so nothing scrolls out from under it: whatever it
     is given the same room under its content. `--consent-room` on the root element is
     the distance from the notice's top edge to the bottom of the viewport, measured
     rather than guessed because the sentence wraps to one, two or three lines depending
     on the width; system.css spends it at the end of the body. Measured again when the
     notice changes size or the viewport does, and taken away with the notice. */
  var ROOM = '--consent-room'

  function room(box) {
    var root = document.documentElement
    var observer = null
    function stop() {
      if (observer) observer.disconnect()
      window.removeEventListener('resize', measure)
      root.style.removeProperty(ROOM)
    }
    function measure() {
      /* Gone some other way than a click: no room for what is not there. */
      if (!box.isConnected) return stop()
      var covered = box.offsetHeight ? window.innerHeight - box.getBoundingClientRect().top : 0
      root.style.setProperty(ROOM, Math.max(0, Math.ceil(covered)) + 'px')
    }
    if (typeof ResizeObserver === 'function') {
      observer = new ResizeObserver(measure)
      observer.observe(box)
    }
    window.addEventListener('resize', measure)
    measure()
    return stop
  }

  function accept() {
    loadPixel()
    loadPostHog()
  }

  function decide(decision, box) {
    remember(decision)
    if (decision === GRANTED) accept()
    if (box && box.parentNode) box.parentNode.removeChild(box)
  }

  /* Refusing costs one click; withdrawing used to cost a trip into the browser's own
     site-data settings, which is not equally easy (GDPR art. 7(3) asks for it to be).
     Only a stored yes: a refusal stays on record, or withdrawing would put the notice
     back in front of somebody who already said no, which is the dark pattern `start`'s
     own DENIED branch exists to rule out. Reloads on either outcome, so a click always
     does something. No page calls this today -- the three pages that load this script
     carry no withdraw control, and privacy.html, which does, cannot load this script
     (it carries no tracker to gate) and reimplements the same steps against the same
     key instead; renaming STORAGE_KEY here has to carry over there too, and
     pixel.test.ts holds the two literals equal. This export is for the day one of the
     three notice pages grows its own control. */
  function withdraw() {
    try {
      if (window.localStorage.getItem(STORAGE_KEY) === GRANTED) {
        window.localStorage.removeItem(STORAGE_KEY)
      }
    } catch {
      /* Niente da fare: la scelta resta finché dura la sessione. */
    }
    window.location.reload()
  }

  function start() {
    var decision = remembered()
    if (decision === GRANTED) return accept()
    /* A refusal is final until the visitor clears their own storage: no pixel, and no
       second ask. */
    if (decision === DENIED) return
    /* The room is released by the same click that removes the notice, here, so that a
       second start() cannot orphan the first notice's observer and `decide` keeps no
       state of its own. */
    var stop = null
    var box = notice(function (decision, clicked) {
      if (stop) stop()
      decide(decision, clicked)
    })
    document.body.appendChild(box)
    stop = room(box)
  }

  window.__consent = {
    start: start,
    decide: decide,
    withdraw: withdraw,
    measured: measured,
    internal: internal,
    STORAGE_KEY: STORAGE_KEY,
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start)
  } else {
    start()
  }
})()
