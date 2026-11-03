import { createFileRoute, redirect } from '@tanstack/react-router'

// «Emittente» and «Fiscale» became one page, Settings → Legal entities, when the emitter
// profile became one row per legal entity (REB-617, spec 2026-10-03 §5). The old path keeps
// answering, the way the Italian ones retired on 2026-09-21 still do.
export const Route = createFileRoute('/app/settings/issuer')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/legal-entities' })
  },
})
