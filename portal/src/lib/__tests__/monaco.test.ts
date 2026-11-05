/**
 * Monaco comes from the bundle, not from a CDN.
 *
 * `@monaco-editor/react` fetches Monaco from cdn.jsdelivr.net unless it
 * is handed an instance with `loader.config({ monaco })`. On a laptop
 * that is invisible; on an air-gapped deployment the content editor was
 * a spinner that never finished. This checks the hand-over happens, and
 * that the one language with a worker gets its own, from the bundle.
 */
import { afterEach, describe, expect, test, vi } from 'vitest'

const config = vi.fn()

vi.mock('@monaco-editor/react', () => ({ loader: { config } }))

// The real modules need a browser with layout; only identity matters here.
const monacoApi = { editor: {}, languages: {} }
vi.mock('monaco-editor/editor/editor.api', () => monacoApi)
vi.mock('monaco-editor/features/register.all', () => ({}))
vi.mock('monaco-editor/languages/definitions/markdown/register', () => ({}))
vi.mock('monaco-editor/languages/definitions/yaml/register', () => ({}))
vi.mock('monaco-editor/languages/definitions/javascript/register', () => ({}))
vi.mock('monaco-editor/languages/definitions/python/register', () => ({}))
vi.mock('monaco-editor/languages/definitions/shell/register', () => ({}))
vi.mock('monaco-editor/languages/features/json/register', () => ({}))
vi.mock('monaco-editor/editor/editor.worker?worker', () => ({
  default: class EditorWorker {
    kind = 'editor'
  },
}))
vi.mock('monaco-editor/languages/features/json/json.worker?worker', () => ({
  default: class JsonWorker {
    kind = 'json'
  },
}))

afterEach(() => {
  delete (globalThis as { MonacoEnvironment?: unknown }).MonacoEnvironment
})

type Env = { getWorker: (id: string, label: string) => { kind: string } }

describe('the portal’s Monaco', () => {
  test('is handed to the loader, so nothing is fetched from a CDN', async () => {
    vi.resetModules()
    config.mockClear()

    await import('../monaco')

    expect(config).toHaveBeenCalledTimes(1)
    const [arg] = config.mock.calls[0]
    // The bundled instance itself — not a `paths` override that still
    // has the loader inject a <script>.
    expect(Object.keys(arg)).toEqual(['monaco'])
    expect(arg.monaco.editor).toBe(monacoApi.editor)
  })

  test('starts its workers from the bundle', async () => {
    vi.resetModules()

    await import('../monaco')
    const env = (globalThis as { MonacoEnvironment?: Env }).MonacoEnvironment

    expect(env).toBeDefined()
    expect(env!.getWorker('workerMain.js', 'json').kind).toBe('json')
    expect(env!.getWorker('workerMain.js', 'editorWorkerService').kind).toBe('editor')
  })
})
