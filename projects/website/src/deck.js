/* The deck: the field of tiles behind every slide, the reveals, the counters, the
   typewriter on the cover and the navigation. One script for the two decks the site
   serves, `/pitch` (ORB-153) and `/company` (REB-553), where until REB-553 it sat
   inline at the foot of pitch.html; a module now, so Vite emits it once and both
   pages fetch the same file. It reads only the markup the decks share: `.slide`,
   `[data-reveal]`, `[data-count]`, `.role` and the four controls at the end of the
   body. Without JavaScript each page's own <noscript> block puts it in static mode. */

/* --- The field of tiles, deterministic per slide (from the site's field.js). */
function hash(x, y) { var n = (x * 374761393 + y * 668265263) | 0; n = ((n ^ (n >>> 13)) * 1274126177) | 0; return ((n ^ (n >>> 16)) >>> 0) / 4294967296 }
function lerp(a, b, t) { return a + (b - a) * t }
function noise(x, y) { var x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0; fx = fx * fx * (3 - 2 * fx); fy = fy * fy * (3 - 2 * fy);
  return lerp(lerp(hash(x0, y0), hash(x0 + 1, y0), fx), lerp(hash(x0, y0 + 1), hash(x0 + 1, y0 + 1), fx), fy) }
var bands = {
  diagonal: function (u, v) { return 1 - Math.abs((u + v - 1) * 1.6) },
  topright: function (u, v) { return (u - 0.35) * 1.3 - v * 0.9 },
  bottomleft: function (u, v) { return (0.65 - u) * 1.3 - (1 - v) * 0.9 },
}
// Read off :root rather than restated (REB-248): the same tokens field.js
// reads for the landing's own canvas.
var root = getComputedStyle(document.documentElement)
var colours = ['--ink', '--gold', '--quiet', '--melon'].map(function (name) {
  return root.getPropertyValue(name).trim()
})
var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'))
slides.forEach(function (slide) {
  var canvas = slide.querySelector('canvas'), ctx = canvas.getContext('2d')
  var W = 1920, H = 1080, cell = 24, dpr = 2
  canvas.width = W * dpr; canvas.height = H * dpr; ctx.scale(dpr, dpr)
  var seed = +slide.dataset.seed || 0, band = bands[slide.dataset.band] || bands.diagonal
  var dark = slide.classList.contains('dark')
  var cols = Math.ceil(W / cell), rows = Math.ceil(H / cell)
  for (var y = 0; y < rows; y++) for (var x = 0; x < cols; x++) {
    var density = band(x / cols, y / rows)
    if (density <= 0) continue
    var coarse = noise(x * 0.07 + seed, y * 0.07 + seed * 0.3), fine = noise(x * 0.45 - seed, y * 0.45)
    var v = (coarse * 0.6 + fine * 0.4) * density * 1.4
    if (v < 0.36) continue
    if (fine > 0.8 && v < 0.55) continue
    var pick = v < 0.52 ? 0 : v < 0.6 ? 2 : v < 0.76 ? (fine > 0.55 ? 1 : 0) : 1
    if (v > 0.5 && hash(x + seed, y) > 0.985) pick = 3
    if (dark && pick === 0) continue
    ctx.fillStyle = colours[pick]
    ctx.fillRect(x * cell + 1, y * cell + 1, cell - 2, cell - 2)
  }
})

/* --- Static mode for export: everything visible, counters at their final value. */
var isStatic = /[?&]static/.test(location.search)
if (isStatic) document.body.classList.add('static')

/* --- Counters. Italian figures: a comma before the decimals and a dot every three
   digits, so `data-count="1000"` reads 1.000 (the company deck's top day rate, REB-553)
   and `18.5` with one decimal reads 18,5 as it always has. */
function figure(value, dec) {
  var parts = value.toFixed(dec).split('.')
  return parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (parts[1] ? ',' + parts[1] : '')
}
function runCounters(slide) {
  slide.querySelectorAll('[data-count]').forEach(function (el) {
    var target = +el.dataset.count, dec = +(el.dataset.decimals || 0), start = null, dur = 1400
    var delay = parseFloat(getComputedStyle(el.closest('[data-reveal]') || el).getPropertyValue('--d')) || 0
    el.textContent = figure(0, dec)
    setTimeout(function () {
      function tick(t) {
        if (start === null) start = t
        var p = Math.min(1, (t - start) / dur); p = 1 - Math.pow(1 - p, 3)
        el.textContent = figure(target * p, dec)
        if (p < 1) requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    }, delay * 1000 + 200)
  })
}
if (isStatic) document.querySelectorAll('[data-count]').forEach(function (el) { el.textContent = figure(+el.dataset.count, +(el.dataset.decimals || 0)) })

/* --- Typewriter on the cover, the landing's own gesture. */
var role = document.querySelector('.role')
if (role && !isStatic) {
  var words = role.dataset.roles.split('|'), wi = 0, ci = words[0].length, deleting = false
  role.textContent = words[0]
  setTimeout(function step() {
    var w = words[wi]
    if (!deleting) { ci++; if (ci > w.length) { deleting = true; setTimeout(step, 1600); return } }
    else { ci--; if (ci < 0) { deleting = false; wi = (wi + 1) % words.length; ci = 0 } }
    role.textContent = words[wi].slice(0, Math.max(0, ci))
    setTimeout(step, deleting ? 45 : 90)
  }, 2200)
}

/* --- Navigation. */
var current = -1
var progress = document.getElementById('progress'), counter = document.getElementById('counter'), hint = document.getElementById('hint')
function go(i) {
  if (isStatic) return
  i = Math.max(0, Math.min(slides.length - 1, i))
  if (i === current) return
  if (current >= 0) slides[current].classList.remove('active')
  current = i
  slides[i].classList.add('active')
  runCounters(slides[i])
  progress.style.width = ((i + 1) / slides.length * 100) + '%'
  counter.textContent = (i + 1) + ' / ' + slides.length
  location.hash = '#' + (i + 1)
  if (i > 0) hint.style.opacity = 0
}
function fit() {
  var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080)
  document.documentElement.style.setProperty('--s', s)
}
window.addEventListener('resize', fit); fit()
document.getElementById('prev').addEventListener('click', function () { go(current - 1) })
document.getElementById('next').addEventListener('click', function () { go(current + 1) })
document.addEventListener('keydown', function (e) {
  if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown' || e.key === 'Enter') { e.preventDefault(); go(current + 1) }
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || e.key === 'Backspace') { e.preventDefault(); go(current - 1) }
  else if (e.key === 'Home') go(0)
  else if (e.key === 'End') go(slides.length - 1)
  else if (e.key === 'f' || e.key === 'F') { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen() }
})
var tx = null
document.addEventListener('touchstart', function (e) { tx = e.touches[0].clientX }, { passive: true })
document.addEventListener('touchend', function (e) { if (tx === null) return; var dx = e.changedTouches[0].clientX - tx; if (Math.abs(dx) > 50) go(current + (dx < 0 ? 1 : -1)); tx = null }, { passive: true })
var wheelLock = 0
document.addEventListener('wheel', function (e) { var now = Date.now(); if (now - wheelLock < 900 || Math.abs(e.deltaY) < 20) return; wheelLock = now; go(current + (e.deltaY > 0 ? 1 : -1)) }, { passive: true })
if (!isStatic) go(Math.max(0, (parseInt(location.hash.slice(1), 10) || 1) - 1))
