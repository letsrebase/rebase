/**
 * What every jsdom test of an application that renders `@rebase/ui` needs, in one place:
 * both SPAs name it in their vitest `setupFiles` (`@rebase/ui/test-setup`).
 */
import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library's own `findBy*`/`waitFor` default (1000ms) is unrelated to vitest's
// `testTimeout` (20s, each app's `vite.config.ts`): it is how long a query keeps
// retrying before it reports "unable to find", and on this machine, under the full
// `pnpm --filter hub test` run (~35 files in parallel, load average in the double or
// triple digits on 10 cores), the first render after a mocked `fetch` regularly takes
// longer than that to commit. That is not a broken page, just a starved CPU: the same
// pages render fine alone. Raised well past the worst full-run timings seen so far
// (REB-409), staying comfortably under `testTimeout` so a genuine hang still fails.
configure({ asyncUtilTimeout: 10_000 })

afterEach(() => cleanup())

// jsdom has no layout: the router's scroll restoration would otherwise log on every navigation.
window.scrollTo = () => {}

// jsdom implements no pointer-capture API at all (not even a stub), but Radix UI's
// Select calls `hasPointerCapture` on every pointer interaction with its trigger,
// unconditionally: without this, opening a Select under `userEvent.click` throws
// `target.hasPointerCapture is not a function`. A gap in jsdom's coverage of the browser
// API, not a defect any component owns; global and permanent, since every test of a
// Radix trigger (Select, Dropdown, ...) hits it.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

// jsdom has no ResizeObserver at all. Radix UI's `Checkbox` constructs one
// unconditionally, so without this every test that mounts it dies with an uncaught
// `ReferenceError` instead of a failed assertion; so does `cmdk`, the CRM's command
// palette.
if (typeof window.ResizeObserver !== 'function') {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}
