/* The landing's own script: it mounts the field behind the page and the typewriter on
 * the title, and that is all.
 *
 * The field is fixed and the page scrolls over it; `animate` drifts it slowly and
 * field.js itself stands still when the reader asked for reduced motion. The title's
 * first word is typed by the shared typewriter.js, and a screen reader hears the same
 * line either way (see the sr-only span in the h1).
 *
 * Since ORB-145 (Ivan, 2026-09-11) the blocks below the hero rise in as they scroll
 * into view, the pitch deck's own gesture: each `[data-reveal]` gets `in` when it
 * enters the viewport, once. The initial state is still the final state for anyone the
 * script does not reach: landing.css hides a block only under `html.js`, which the
 * inline gate in the page's head sets, and shows everything again after three seconds
 * whatever happened here. Under reduced motion every block is marked at once.
 *
 * Carrying the campaign into the hub (ORB-166, ORB-167) moved to the shared `utm.js`
 * in REB-247: `start` below calls `window.__utm.carryUtm()`, guarded, the same way it
 * guards the shared field and the shared typewriter.
 */
;(function () {
  function reveal() {
    var blocks = document.querySelectorAll('[data-reveal]')
    if (!blocks.length) return
    var reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced || typeof window.IntersectionObserver !== 'function') {
      blocks.forEach(function (el) { el.classList.add('in') })
      return
    }
    var seen = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return
          entry.target.classList.add('in')
          seen.unobserve(entry.target)
        })
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.08 },
    )
    blocks.forEach(function (el) { seen.observe(el) })
  }

  function start() {
    if (window.__utm) window.__utm.carryUtm()
    reveal()
    var role = document.querySelector('h1 .role')
    if (role && window.__typewriter) window.__typewriter.mount(role)
    var field = window.__pigroField
    var canvas = document.getElementById('field')
    if (!field || !canvas || typeof canvas.getContext !== 'function') return
    var cell = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--landing-cell'))
    field.mount(canvas, { cell: cell || 16, animate: true })
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start)
  } else {
    start()
  }
})()
