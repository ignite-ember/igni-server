/**
 * Vitest setup — runs once before tests. Wires up RTL custom matchers
 * and the process-wide MSW server.
 */
import '@testing-library/jest-dom/vitest'
import { LiveRegionElement } from '@primer/live-region-element'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'
import { server } from './server'

// Register Primer's <live-region> custom element.
//
// The package defines it at module load, but that `customElements.define`
// does not take effect here — after importing the package,
// `customElements.get('live-region')` is still undefined, while a class
// defined directly in a test upgrades fine. So jsdom is not the problem;
// the package's registration simply does not reach this realm.
//
// The cost of it missing is not a missing announcement. Primer's `node`
// build has a NODE_ENV === 'test' branch that calls
// `liveRegion.announceFromElement(...)` on the element it just created,
// assuming it was upgraded. Un-upgraded, that is a plain HTMLElement, the
// call throws, and because it runs inside a `useEffectOnce` the whole
// page render goes down with it — which is why page tests have reported
// "unable to find the text": nothing had rendered at all.
if (!customElements.get('live-region')) {
  customElements.define('live-region', LiveRegionElement)
}

// jsdom in vitest 4 stripped browser storage by default — restore an
// in-memory shim so AuthProvider's `localStorage.getItem('igni_token')`
// doesn't crash on mount.
if (typeof window.localStorage === 'undefined' || typeof window.localStorage.getItem !== 'function') {
  const store = new Map<string, string>()
  const storage = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => {
      store.set(key, String(value))
    },
    removeItem: (key: string) => {
      store.delete(key)
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size
    },
  }
  Object.defineProperty(window, 'localStorage', { value: storage, writable: true })
  Object.defineProperty(window, 'sessionStorage', { value: storage, writable: true })
}

// jsdom doesn't implement matchMedia / ResizeObserver — Primer's responsive
// layout queries crash without these stubs. Force-set unconditionally
// because jsdom in vitest 4 sometimes presents matchMedia as `undefined`
// rather than missing.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

window.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver

// Primer's TooltipV2 pulls in @oddbird/popover-polyfill, which iterates
// `document.adoptedStyleSheets`. jsdom exposes that property but as a
// non-iterable getter, so the polyfill throws on mount. Provide an empty
// mutable array.
if (!Array.isArray((document as unknown as { adoptedStyleSheets?: unknown }).adoptedStyleSheets)) {
  Object.defineProperty(document, 'adoptedStyleSheets', {
    configurable: true,
    writable: true,
    value: [],
  })
}
if (typeof ShadowRoot !== 'undefined') {
  Object.defineProperty(ShadowRoot.prototype, 'adoptedStyleSheets', {
    configurable: true,
    writable: true,
    value: [],
  })
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

/**
 * Monaco, as a textarea.
 *
 * The real editor mounts a canvas-backed view that jsdom cannot type
 * into, so a test that wants to check what gets submitted has no way to
 * put content in. Both entry pages use it now, so the stand-in lives
 * here rather than in each suite. It keeps the value/onChange contract
 * — everything a test needs — and nothing else.
 */
vi.mock('@monaco-editor/react', () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string
    onChange?: (value: string | undefined) => void
  }) => (
    <textarea
      data-testid="content-editor"
      aria-label="Content"
      value={value ?? ''}
      onChange={(e) => onChange?.(e.currentTarget.value)}
    />
  ),
}))
