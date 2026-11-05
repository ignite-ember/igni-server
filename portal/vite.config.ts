import { defineConfig, type Plugin } from 'vitest/config'
import react from '@vitejs/plugin-react'

/**
 * Take the CDN out of `@monaco-editor/loader`'s defaults.
 *
 * The portal hands the loader its bundled Monaco (src/lib/monaco.ts), so
 * the loader's default — `paths.vs` on cdn.jsdelivr.net — is never used.
 * It is still a string in the bundle, though, and a default is what
 * anything falls back to: were `loader.config({ monaco })` ever lost, an
 * air-gapped browser would sit waiting on jsdelivr. Emptied, the fallback
 * fails at once, on the portal's own origin, and `scripts/check-no-cdn.mjs`
 * can insist on no CDN host anywhere in `dist/` without an exception.
 *
 * The build fails if the default is not where this expects it, rather
 * than quietly shipping the URL after a loader upgrade moves it.
 */
function monacoLoaderWithoutCdn(): Plugin {
  const file = /@monaco-editor[\\/]loader[\\/]lib[\\/]es[\\/]config[\\/]index\.js$/
  const cdn = /(vs:\s*)'https:\/\/cdn\.jsdelivr\.net\/[^']*'/
  return {
    name: 'igni:monaco-loader-without-cdn',
    enforce: 'pre',
    transform(code, id) {
      if (!file.test(id)) return null
      if (!cdn.test(code)) {
        this.error(`${id} no longer has the CDN default this plugin removes; update it in vite.config.ts`)
      }
      return { code: code.replace(cdn, "$1''"), map: null }
    },
  }
}

export default defineConfig({
  plugins: [react(), monacoLoaderWithoutCdn()],
  server: {
    proxy: {
      '/v1': {
        // IGNI_API_URL lets the E2E stack (tools/portal-e2e-stack.sh) run
        // on another port beside a developer's own stack.
        target: process.env.IGNI_API_URL ?? 'http://localhost:7777',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.tsx'],
    // Primer (and its peer deps) ship CSS imports inside their JS. Inline
    // these packages so vitest's transformer handles the .css side-effects
    // instead of Node trying to load them as ESM.
    server: {
      deps: {
        inline: [/@primer\//, /@radix-ui\//, /styled-components/],
      },
    },
    // Playwright specs live under tests/e2e and run via `playwright test`
    exclude: ['node_modules', 'dist', 'tests/e2e/**', '**/*.spec.ts'],
  },
})
