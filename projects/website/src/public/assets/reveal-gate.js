/* The reveal gate (ORB-145): landing.css hides `[data-reveal]` only under `html.js`, so
 * a page whose script never runs shows everything, and the timer is the net for a
 * script that loads but fails: whatever happened, after three seconds nothing is hidden.
 *
 * An external file, not an inline snippet, so the site's CSP can drop `'unsafe-inline'`
 * from `script-src` (SITE-A-01). It is a blocking `<script src>` in the head, which is
 * what lets it run before the first paint, and it sits in `public/assets/` so the build
 * copies it untouched to `/assets/reveal-gate.js`, a path `deploy/nginx.conf` already
 * serves.
 */
document.documentElement.classList.add('js')
setTimeout(function () {
  document.documentElement.classList.add('reveal-all')
}, 3000)
