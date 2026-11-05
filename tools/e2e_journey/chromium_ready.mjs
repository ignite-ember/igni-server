// Whether the portal's Playwright has its Chromium: `node chromium_ready.mjs <portal dir>`.
// journey.sh's `up` asks before it starts anything; without it every
// browser step failed with a stack trace and the steps after them for want
// of their sign-ins.
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'

const portal = process.argv[2]
let path
try {
  path = createRequire(`${portal}/package.json`)('@playwright/test').chromium.executablePath()
} catch (error) {
  console.error(`the portal's Playwright did not load (${error.message}) — \`npm ci\` in portal/`)
  process.exit(1)
}
if (!path || !existsSync(path)) {
  console.error(`no Chromium for the portal's Playwright at ${path} — \`npx playwright install chromium\` in portal/`)
  process.exit(1)
}
