import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { Gallery } from './Gallery'

/** The gallery's mount, and nothing else: the page itself is `Gallery.tsx`. */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Gallery />
  </StrictMode>,
)
