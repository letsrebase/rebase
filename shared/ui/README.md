# @rebase/ui

The squared system every rebase application renders on: the tokens, the eighteen
primitives that read them, the class merger they compose with, and a gallery that shows
the lot on one page. Each SPA imports this package instead of keeping a copy of its
own.

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
@import '@rebase/ui/before-tailwind.css';
@import 'tailwindcss';
@import '@rebase/ui/tokens.css';
```

Three lines, in that order, and both positions are load-bearing. `before-tailwind.css`
has to precede Tailwind's expansion, since an `@import` after it sits behind non-import
rules and the optimiser drops it. `tokens.css` has to follow it, since its `@theme`
block has to win over Tailwind's own defaults for the same keys. The palette and the
typeface arrive through that file, from `@rebase/brand`, so an application does not
import them a second time, and it carries `@source './*.tsx'` so the classes used only
by a primitive are emitted for every consumer: Tailwind scans no path under
`node_modules`, which is where an application sees this package.

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

## What this package holds

- **`tokens.css`**, the values (REB-299).
- **`before-tailwind.css`**, the two stylesheets the primitives need *before* Tailwind
  expands: `tw-animate-css` for their enter and exit transitions and
  `shadcn/tailwind.css` for the `data-open`/`data-checked` custom variants their class
  names use. Its own header explains why they cannot live in `tokens.css`.
- **Eighteen primitives**, flat beside this file, one export path each
  (`@rebase/ui/button`): button, input, textarea, select, checkbox, label, badge, card,
  dialog, sheet, dropdown-menu, popover, tooltip, tabs, table, separator, skeleton and
  the sonner toaster.
- **`cn.ts`**, the class merger they compose with and the applications import for their
  own components (`@rebase/ui/cn`).
- **`gallery/`**, every primitive in every variant and state on one page.

## What stays in the application, and why

**The hub's `.site` scope.** The chooser, the two wizards and the thanks page are the
landing continued, not the application, so they keep its own 2px line and 8px step and
its 7% grid. They are the one surface that overrides what is here, which is why the
shadow slots are declared as `--shadow-app-*` and read through a bare `var()` in
`@theme inline`: Tailwind v4 bakes the lengths of a compound theme value into every
utility at build time, so a scope can only repoint the indirection, never `--shadow-xs`
itself. Those overrides target `[data-slot=...]`, so they still reach the primitives
after the move, which is what the hub's wizard proves on every render.

**Five of the CRM's generated components.** They did not move: `avatar`, `calendar`, `command`,
`input-group` and `sidebar`. None of them has a second consumer. The hub's shell is its
own (REB-279 rewrites it) and its screens have no command palette, no date picker and no
avatar, so moving those would be moving one application's code into a package for three.
They import what they need from here, which is what makes the boundary visible: a file
under `projects/pigrocrm/apps/web/src/components/ui/` that imports `@rebase/ui/button`
is CRM-only on purpose. The day the hub needs one, it moves.

The hub's seven hand-written primitives (button, input, textarea, card, label, badge,
dialog) were deleted rather than merged: the CRM's radix versions are the ones with the
states, the variants and the tests behind them.

`sonner` is the one dependency an application may not declare for itself. Its toast
queue is module state, so two resolutions mean the `<Toaster>` this package renders
listens to one queue while `toast()` pushes onto the other and every toast disappears
with a green build: `@rebase/ui/sonner` exports both, and the CRM's callers import
`toast` from there.

`components.json` here is the generator's own configuration, so a nineteenth primitive
is added in this package (`pnpm --filter @rebase/ui exec shadcn add <name>`) rather than
in an application and moved afterwards. The CRM keeps its own, pointing at the five that
stayed, with its `utils` alias now naming `@rebase/ui/cn`.

## The gallery

```
pnpm --filter @rebase/ui dev     # serves gallery/index.html
pnpm --filter @rebase/ui build   # emits gallery/dist
```

One page, every primitive, every variant, size and state it declares, in the order a
reader expects rather than the order the files are in. Overlays cannot be shown by
rendering them, since each lives behind a trigger and a portal, so
`?open=dialog|sheet|sheet-left|sheet-top|sheet-bottom|menu|popover|select` opens exactly
one on load: a screenshot of an open surface is a URL rather than a
sequence of clicks. It renders on the same three imports an application uses, in the
same order, so a difference between this page and a product screen is a difference in
how that product imports the package.

## What the primitives may not do

A hex, a px or a shadow typed into a component is a defect even when it looks right:
every value reads a token. Two consequences of the record that are easy to undo by
accident:

- **No `dark:` utility.** They were stripped when the primitives moved: there is no dark
  theme and no `.dark` block, so every one of them was dead weight that would come back
  to life the day somebody bound the variant to the OS preference. `tokens.css` keeps
  the variant bound to a class nobody sets, which is what keeps the five components
  still in the CRM harmless until they are cleaned up too.
- **No literal corner.** `--radius` is zero and the derived scale with it, so
  `rounded-lg` is correct and `rounded-[10px]` is not, even though the second one
  looked identical the day it was written.

## The contract test

`tokens.test.ts` is the gate: it resolves every colour token back through the brand
palette (so a hand-mixed hex is mechanically impossible), evaluates the five chart
`color-mix()` expressions the way a browser does and measures their contrast, and pins
the record's rules, zero radius, ink lines, no blur in any shadow, no `.dark`. It runs
in this package, once, instead of once per application.
