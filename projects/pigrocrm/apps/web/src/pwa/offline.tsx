import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { OfflinePage } from './OfflinePage'
import '../styles/tokens.css'

// The entry of `offline.html`. A reload asks the network again, and the worker answers
// with the real page the moment there is one.
const reload = () => window.location.reload()
window.addEventListener('online', reload)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OfflinePage onRetry={reload} />
  </StrictMode>,
)
