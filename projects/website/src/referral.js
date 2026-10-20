/* The referral band's figures, read from the hub (REB-711, REB-645 option B).
 *
 * The two rates are an admin's to edit, so no number of theirs is typed into
 * index.html: `GET /api/hub/referral-rates` (same origin, no cookie, REB-709) answers
 * `{"rate_freelancer": "0.1000", "rate_company": "0.3000"}` and this script turns each
 * rate into a figure, a bar whose fill is its share, a sentence and, from the two, one
 * worked example. A rate the page cannot read (a non-200, a body that is not JSON, a
 * value that is not a fraction between 0 and 1, no `fetch` at all) is dropped: the
 * markup stands without it and says «una quota del margine di rebase», and when
 * neither reads, the example stays hidden too. Nothing is ever shown from a default.
 *
 * The arithmetic is the member area's, copied from `projects/hub/apps/web/src/lib/
 * referral.ts` (`formatRate`, `rateWithArticle`, `roundedShare`, `referralExample`):
 * the site has no package shared with the hub, and both read the same four-place
 * strings, integers all the way so `0.1250` never meets binary floating point. A change
 * to the Italian rules there is a change here.
 *
 * An IIFE publishing `window.__referral`, like `session.js`: it starts on its own and
 * its test loads it with `new Function`.
 */
;(function () {
  var URL_RATES = '/api/hub/referral-rates'

  /** The API stores a rate as a fraction with four decimals: `0.1250` is 1250 units. */
  var RATE_SCALE = 10000

  /** The share, in ten-thousandths, from which a bar no longer leaves room for its caption. */
  var WIDE_FROM = 6000

  /** The invented numbers of the example, in whole euro and days; the same as the
   *  member area's popover. They are no engagement's. */
  var EXAMPLE = { budget: 500, compenso: 400, giorni: 20 }

  var percent = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2 })
  // `useGrouping: 'always'`: it-IT writes 2000,00 by default (CLDR groups from five
  // digits), and the member area's example reads 2.000,00 €.
  var euro = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', useGrouping: 'always' })
  var euroWhole = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })

  /** A rate as the API answers it (`"0.1000"`) in ten-thousandths, `null` for anything
   *  that is not a fraction between 0 and 1. */
  function rateUnits(rate) {
    if (typeof rate !== 'string' || !/^\d+(\.\d+)?$/.test(rate)) return null
    var units = Math.round(Number(rate) * RATE_SCALE)
    return units >= 0 && units <= RATE_SCALE ? units : null
  }

  /** `"0.1000"` as `10%`, `"0.1250"` as `12,5%`; `null` when there is no valid rate. */
  function formatRate(rate) {
    var units = rateUnits(rate)
    return units === null ? null : percent.format(units / 100) + '%'
  }

  /** The rate with the article its Italian reading takes: `il 10%`, `l'8%`, `l'11%`,
   *  `l'80%`, `l'1%`, `lo 0,5%`; `null` when there is no valid rate. */
  function rateWithArticle(rate) {
    var units = rateUnits(rate)
    if (units === null) return null
    var whole = Math.floor(units / 100)
    var article =
      whole === 0 ? 'lo ' : whole === 1 || whole === 8 || whole === 11 || (whole >= 80 && whole <= 89) ? "l'" : 'il '
    return article + percent.format(units / 100) + '%'
  }

  /** `cents * units / RATE_SCALE`, in cents, rounded half to even: the mode of the
   *  backend's `Decimal.quantize(Decimal('0.01'))`. */
  function roundedShare(cents, units) {
    var product = cents * units
    var whole = Math.floor(product / RATE_SCALE)
    var rest = product - whole * RATE_SCALE
    if (rest * 2 < RATE_SCALE) return whole
    if (rest * 2 > RATE_SCALE) return whole + 1
    return whole % 2 === 0 ? whole : whole + 1
  }

  /** The example's margin and what each rate makes of it, in cents; `null` for a rate
   *  that does not read. */
  function referralExample(rates) {
    var marginCents = (EXAMPLE.budget - EXAMPLE.compenso) * EXAMPLE.giorni * 100
    function share(rate) {
      var units = rateUnits(rate)
      return units === null ? null : roundedShare(marginCents, units)
    }
    return { margin: marginCents, freelancer: share(rates.rate_freelancer), company: share(rates.rate_company) }
  }

  /** The example as a sentence, naming only the rewards that exist. `null` when no rate
   *  reads. */
  function exampleText(rates) {
    var example = referralExample(rates)
    var rewards = []
    if (example.freelancer !== null) rewards.push(euro.format(example.freelancer / 100) + ' per un talento')
    if (example.company !== null) rewards.push(euro.format(example.company / 100) + " per un'azienda")
    if (!rewards.length) return null
    return (
      'Un incarico da ' + euroWhole.format(EXAMPLE.budget) + ' al giorno, ' +
      euroWhole.format(EXAMPLE.compenso) + ' al freelance, ' + EXAMPLE.giorni + ' giorni: il margine di rebase è ' +
      euro.format(example.margin / 100) + ". Se l'hai portato tu, ricevi " + rewards.join(', ') + '.'
    )
  }

  /** Both rates from the endpoint; an unreadable answer is two nulls. */
  function readRates(fetchImpl) {
    var none = { rate_freelancer: null, rate_company: null }
    if (typeof fetchImpl !== 'function') return Promise.resolve(none)
    return fetchImpl(URL_RATES, { credentials: 'omit' })
      .then(function (response) {
        return response.ok ? response.json() : null
      })
      .then(function (body) {
        if (!body || typeof body !== 'object') return none
        return {
          rate_freelancer: rateUnits(body.rate_freelancer) === null ? null : body.rate_freelancer,
          rate_company: rateUnits(body.rate_company) === null ? null : body.rate_company,
        }
      })
      .catch(function () {
        return none
      })
  }

  /** Fills the band: each `[data-rate="rate_…"]` row gets its figure, its bar and its
   *  sentence, or keeps the markup's own «una quota» wording; `[data-example]` appears
   *  only when at least one rate read. Returns how many rates were shown. */
  function render(root, rates) {
    var shown = 0
    root.querySelectorAll('[data-rate]').forEach(function (row) {
      var rate = rates[row.getAttribute('data-rate')]
      var units = rateUnits(rate)
      var figure = row.querySelector('[data-figure]')
      var bar = row.querySelector('[data-bar]')
      var fallback = row.querySelector('[data-fallback]')
      if (units === null) return
      shown += 1
      figure.textContent = formatRate(rate)
      figure.hidden = false
      bar.style.setProperty('--share', units / 100 + '%')
      // The caption lives in the part the fill leaves; from 60% on that part is too
      // short to hold it, so the bar wraps and the caption takes a line under the fill.
      bar.toggleAttribute('data-wide', units >= WIDE_FROM)
      // A rate of nothing draws no fill, not the two pixels of its border.
      bar.querySelector('.split-fill').hidden = units === 0
      bar.setAttribute('aria-label', 'Ricevi ' + rateWithArticle(rate) + ' del margine di rebase sul primo incarico')
      bar.hidden = false
      fallback.hidden = true
    })
    var example = root.querySelector('[data-example]')
    var text = exampleText(rates)
    if (example && text) {
      example.querySelector('[data-example-text]').textContent = text
      example.hidden = false
    }
    return shown
  }

  function start() {
    var root = document.querySelector('[data-referral]')
    if (!root) return
    readRates(window.fetch && window.fetch.bind(window)).then(function (rates) {
      render(root, rates)
    })
  }

  window.__referral = {
    RATE_SCALE: RATE_SCALE,
    EXAMPLE: EXAMPLE,
    URL_RATES: URL_RATES,
    formatRate: formatRate,
    rateWithArticle: rateWithArticle,
    roundedShare: roundedShare,
    referralExample: referralExample,
    exampleText: exampleText,
    readRates: readRates,
    render: render,
    start: start,
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start)
  } else {
    start()
  }
})()
