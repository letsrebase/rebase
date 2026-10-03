import { createFileRoute, redirect } from '@tanstack/react-router'

// «Emittente» and «Fiscale» became one page, Impostazioni → Aziende, when the emitter
// profile became one row per azienda (REB-617, spec 2026-10-03 §5). The old path keeps
// answering, the way the Italian ones retired on 2026-09-21 still do.
export const Route = createFileRoute('/app/settings/fiscal')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/aziende' })
  },
})
