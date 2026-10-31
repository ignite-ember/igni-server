import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Primer design tokens (base spacing, typography, motion)
import '@primer/primitives/dist/css/primitives.css'
// Primer color themes
import '@primer/primitives/dist/css/functional/themes/light.css'
import '@primer/primitives/dist/css/functional/themes/dark.css'

import App from './App'
import { adoptLegacyKeys } from './auth/storageKeys'

// Before anything reads localStorage: carry a session stored under
// the pre-rename key names across, so the rename is not a logout.
adoptLegacyKeys()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
