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
 *
 * REB-557: `start` also guards `window.__typewriter` because the page's own script tag
 * order is not a build guarantee. index.html loads typewriter.js before this file so a
 * browser running the source directly always has it, but Vite's production bundle can
 * put the two in different chunks and evaluate the chunk holding this IIFE before the
 * one that assigns `window.__typewriter`, since a plain script tag's load order is not
 * a dependency Rollup's chunk graph has to respect. A side-effect import makes the
 * dependency real: static imports of a module finish evaluating before the importing
 * module's own top-level code runs, in a bundle and in a browser alike, so
 * `window.__typewriter` is always set by the time the code below reads it.
 *
 * REB-575: the same holds for the other two globals this file reads, `window.__utm`
 * (utm.js) and `window.__pigroField` (field.js), so they are imported the same way. The
 * three scripts still sit in index.html's markup for a browser running the source, and
 * Vite rewrites them into this one entry; the imports are what keep the order true when
 * Rollup splits the chunks differently. e2e/site.spec.ts checks it on the built bundle.
 *
 * REB-676: `window.__teamBuilder` (team.js) is the fourth, mounted on the landing's
 * `form[data-team]`; pigrocrm.html has no such form and skips it.
 */
import './utm.js'
import './team.js'
import './typewriter.js'
import './field.js'
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
    // The typed word: in the landing's claim since REB-676 (an h2 beside the team
    // builder), in the title before that. Found by what it is, not by where it sits.
    var role = document.querySelector('.role[data-roles]')
    if (role && window.__typewriter) window.__typewriter.mount(role)
    var team = document.querySelector('form[data-team]')
    if (team && window.__teamBuilder) window.__teamBuilder.mount(team)
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
