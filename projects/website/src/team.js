/* The team builder on the landing's first screen (REB-676).
 *
 * Ivan, 2026-10-07: the first thing a visitor sees is the box with the examples and the
 * chance to build their team, «in una forma semplificata ... con un output minimo che poi
 * può essere visto in modo completo all'interno della pagina di dettaglio». So this is
 * the hub's team builder (projects/hub, TeamBuilder.tsx) cut to what a static page can
 * carry without a framework: four examples that fill the box, one call to the hub's own
 * POST /api/hub/team/proposals on the same origin, and the least of the answer that
 * says whether it is worth a click: the summary, each role with its seniority and its
 * day band, the team's day band, and one door into /hub/team?proposta=<id>, where the
 * hub reads the proposal back (REB-675) and the whole page is. No «Rigenera», no
 * «Assumi team», no headcount: the page sends the description alone and the engine
 * sizes the team, and everything past the minimum lives on the hub's page.
 *
 * The words are the hub's: the examples, the placeholder, «Proponi il team», «Sto
 * leggendo i profili…», the band labels (`bands.ts`, byte for byte: a non-breaking
 * space before «€», an en dash between the bounds), the sentence under forty characters,
 * and the API's own sentence whenever it answered one (the builder off, too many
 * requests, Claude not answering), ours only when nothing answered at all.
 *
 * Without the script the form is what the markup says: a GET to /hub/team with the
 * description, which the hub's page puts in its own box. The script turns that into a
 * call here, and sets `noValidate` so the sentence a visitor reads is this file's, in
 * Italian, and not the browser's bubble.
 *
 * An IIFE publishing `window.__teamBuilder`, like `utm.js` and `typewriter.js`:
 * `landing.js` imports it and mounts it on the form, and the tests load the source
 * with `new Function` and drive `mount` on a fixture with a fetch of their own.
 */
;(function () {
  var API = '/api/hub/team/proposals'
  var PAGE = '/hub/team'
  var DESCRIZIONE_MIN = 40
  var PENDING = 'Sto leggendo i profili…'
  var TOO_SHORT = 'Raccontaci qualcosa in più: servono almeno ' + DESCRIZIONE_MIN + ' caratteri.'
  var FAILED = 'Non siamo riusciti a proporre un team. Riprova.'
  // The hub's own sentence for a 429 (`api.ts`): the speed bump answers no JSON.
  var THROTTLED = 'Troppe richieste da qui. Riprova tra un minuto.'
  var NO_BAND = 'Tariffa da definire'
  var SENIORITY = { junior: 'Junior', mid: 'Mid', senior: 'Senior', lead: 'Lead' }
  var NBSP = ' '
  var DASH = '–'

  /** Whole euro the Italian way, `17.600`, as the hub's `bands.ts` writes it. */
  function euro(amount) {
    return String(amount).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
  }

  /** «400–500 € al giorno», «oltre 800 € al giorno», «fino a 300 € al giorno»; the
   *  hub's «Tariffa da definire» for a band that is not one. */
  function bandLabel(band) {
    if (!band || typeof band.min !== 'number' || (band.max !== null && typeof band.max !== 'number')) {
      return NO_BAND
    }
    var bounds =
      band.max === null
        ? 'oltre ' + euro(band.min)
        : band.min === 0
          ? 'fino a ' + euro(band.max)
          : euro(band.min) + DASH + euro(band.max)
    return bounds + NBSP + '€ al giorno'
  }

  /** «Senior, 9 anni di esperienza», from the card, as the hub's `format.ts` says it. */
  function experience(scheda) {
    var parts = []
    if (scheda && scheda.seniority) parts.push(SENIORITY[scheda.seniority] || scheda.seniority)
    if (scheda && typeof scheda.anni === 'number') {
      parts.push(
        scheda.anni === 0
          ? 'meno di un anno di esperienza'
          : scheda.anni === 1
            ? '1 anno di esperienza'
            : scheda.anni + ' anni di esperienza',
      )
    }
    return parts.join(', ')
  }

  /** The API's own sentence whenever it gave one: `detail` as a string (503, 502), or
   *  FastAPI's list for a 422, the first item's `msg`; the hub's for a 429; ours when
   *  nothing answered at all. */
  function sentence(status, body) {
    if (status === 429) return THROTTLED
    var detail = body && body.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === 'string') return detail[0].msg
    return FAILED
  }

  function el(tag, className, text) {
    var element = document.createElement(tag)
    if (className) element.className = className
    if (text !== undefined) element.textContent = text
    return element
  }

  /** The minimal result into `result`, then the door, decorated by utm.js like every
   *  other link into the hub so the campaign and `da=home` follow the visitor. Text
   *  nodes only, no innerHTML: the summary and the roles are the model's words. */
  function render(result, proposal) {
    while (result.firstChild) result.removeChild(result.firstChild)
    var title = el('h2', 'team-title', 'La nostra proposta')
    title.tabIndex = -1
    result.appendChild(title)
    result.appendChild(el('p', 'team-summary', proposal.riassunto))
    var members = Array.isArray(proposal.team) ? proposal.team : []
    if (members.length) {
      var list = el('ul', 'team-roles')
      list.setAttribute('aria-label', 'Il team')
      members.forEach(function (member) {
        var item = el('li', 'team-member')
        item.appendChild(el('strong', 'team-role', member.ruolo))
        var meta = experience(member.scheda)
        if (meta) item.appendChild(el('span', 'team-meta', meta))
        item.appendChild(el('span', 'team-band', bandLabel(member.fascia)))
        list.appendChild(item)
      })
      result.appendChild(list)
      var giorno = proposal.economia && proposal.economia.giorno
      var total = giorno ? bandLabel(giorno) : NO_BAND
      result.appendChild(
        el('p', 'team-total', 'Tutto il team: ' + (total === NO_BAND ? NO_BAND.toLowerCase() : total)),
      )
    }
    var actions = el('p', 'actions')
    var door = el('a', 'cta', members.length ? 'Vedi il team completo' : 'Riprova nel team builder')
    door.href = PAGE + '?proposta=' + encodeURIComponent(proposal.id)
    actions.appendChild(door)
    result.appendChild(actions)
    if (window.__utm) window.__utm.carryUtm(result)
    result.hidden = false
    // The result lands under the box, on a phone below the fold: the focus follows it,
    // so a keyboard or a screen reader is not left on a button that has finished.
    title.focus()
  }

  /** A 200 whose body is a proposal: an id to link and a summary to show. Anything
   *  else (no JSON, an empty object) is treated as no answer at all, so the region is
   *  never a door to `?proposta=undefined`. */
  function isProposal(body) {
    return Boolean(body) && typeof body.id === 'string' && typeof body.riassunto === 'string'
  }

  /** Mounts the builder on `form`: the examples fill the box, the submit asks the hub.
   *  `fetchImpl` is for the tests; the page uses `window.fetch`. Returns whether the
   *  form had everything it needs. */
  function mount(form, fetchImpl) {
    var box = form.querySelector('textarea[name="descrizione"]')
    var button = form.querySelector('button[type="submit"]')
    var error = form.querySelector('.team-error')
    var result = document.querySelector(form.getAttribute('data-result') || '')
    if (!box || !button || !error || !result) return false
    form.noValidate = true
    var label = button.textContent
    var busy = false
    var chips = form.querySelectorAll('button[data-example]')

    /** The result under the form is the answer to the description above it, or nothing:
     *  a new ask takes the old team down before it leaves, so a refusal never leaves a
     *  team on screen that was asked about another project. */
    function clear() {
      while (result.firstChild) result.removeChild(result.firstChild)
      result.hidden = true
    }

    /** While the hub reads the profiles the box and the examples hold still, so the
     *  answer lands under the words it was asked about. */
    function hold(on) {
      box.disabled = on
      button.disabled = on
      chips.forEach(function (chip) {
        chip.disabled = on
      })
    }

    /** The sentence under the form, and the box's own state with it: a sentence about
     *  the description marks the box invalid and described by it, as the hub's box
     *  is; any other sentence, or none, leaves the box as it was. */
    function say(text, aboutTheBox) {
      error.textContent = text
      if (aboutTheBox && text) {
        box.setAttribute('aria-invalid', 'true')
        if (error.id) box.setAttribute('aria-describedby', error.id)
      } else {
        box.removeAttribute('aria-invalid')
        box.removeAttribute('aria-describedby')
      }
    }

    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        box.value = chip.getAttribute('data-example')
        say('')
        box.focus()
      })
    })

    function settle() {
      busy = false
      hold(false)
      button.textContent = label
      form.removeAttribute('aria-busy')
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault()
      if (busy) return
      var text = box.value.trim()
      if (text.length < DESCRIZIONE_MIN) {
        say(TOO_SHORT, true)
        box.focus()
        return
      }
      say('')
      clear()
      busy = true
      hold(true)
      button.textContent = PENDING
      form.setAttribute('aria-busy', 'true')
      var call = fetchImpl || window.fetch.bind(window)
      call(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ descrizione: text }),
      })
        .then(function (response) {
          return response
            .json()
            .catch(function () {
              return null
            })
            .then(function (body) {
              if (!response.ok) {
                say(sentence(response.status, body))
                return
              }
              if (!isProposal(body)) {
                say(FAILED)
                return
              }
              render(result, body)
            })
        })
        .catch(function () {
          say(FAILED)
        })
        .then(settle)
    })
    return true
  }

  window.__teamBuilder = { mount: mount, render: render, bandLabel: bandLabel, sentence: sentence }
})()
