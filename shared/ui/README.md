# @rebase/ui

The tokens every rebase application renders on. One file, `tokens.css`, imported by
each SPA in place of a copy of its own.

Squared by default: radius zero across the derived scale, 1px full-strength ink lines
with the site's 2px reserved for the primary button and the focus ring, one ink step
shadow of 4px with no blur on the surfaces that float and nothing on the surfaces that
sit in the page, the site's 16px grid behind the page at 4% of the ink, the Prussian
Blue sidebar. The decision and its reasons are
`docs/design/2026-09-18-application-variant-brief.md` and the two rows dated
2026-09-18 in `docs/design/DECISIONS.md`; this package is where those values live, and
a component that types a hex, a px or a shadow instead of reading a token is a defect
even when it looks right.

## How an application imports it

```css
@import 'tailwindcss';
@import '@rebase/ui/tokens.css';
```

After `tailwindcss`, never before: the `@theme` block here has to win over Tailwind's
own defaults for the same keys. The palette and the typeface arrive through this file,
from `@rebase/brand`, so an application does not import them a second time.

## What stays in the application

- **The hub's `.site` scope.** The chooser, the two wizards and the thanks page are
  the landing continued, not the application, so they keep its own 2px line and 8px
  step and its 7% grid. They are the one surface that overrides what is here, which is
  why the shadow slots are declared as `--shadow-app-*` and read through a bare
  `var()` in `@theme inline`: Tailwind v4 bakes the lengths of a compound theme value
  into every utility at build time, so a scope can only repoint the indirection, never
  `--shadow-xs` itself.
- **Anything one product has and the other does not.** The CRM's `tw-animate-css` and
  `shadcn/tailwind.css` imports back its generated primitives and belong to it.

## What moved in here, and what it replaced

Both applications declared the same semantic slots, the hub's file saying in its own
header that it was a copy. The values are now declared once, at the record's weights.
Five things changed while moving, all of them the record's decision and not a port:
the radius scale is zero rather than derived from 10px, the border and input slots are
the ink at full strength rather than 12% and 20% tints, the resting shadows are `none`
and the floating ones an offset rather than four blurred tints, the grid is drawn on
the body again, and there is no `.dark` block. The `dark:` variant stays bound to a
class nobody sets, which is what keeps the primitives' `dark:` utilities inert instead
of letting a visitor's OS apply them.

## The contract test

`tokens.test.ts` is the gate: it resolves every colour token back through the brand
palette (so a hand-mixed hex is mechanically impossible), evaluates the five chart
`color-mix()` expressions the way a browser does and measures their contrast, and pins
the record's rules, zero radius, ink lines, no blur in any shadow, no `.dark`. It runs
in this package, once, instead of once per application.
