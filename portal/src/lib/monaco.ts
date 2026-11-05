/**
 * Monaco, from the bundle.
 *
 * `@monaco-editor/react` does not ship Monaco: left to itself its loader
 * injects a <script> for Monaco on jsdelivr's CDN. An air-gapped
 * deployment cannot reach that, so the content editor never appeared. Handing the loader this instance stops it fetching anything.
 *
 * Only what the portal edits is imported — `EDITOR_LANGUAGE` in
 * `entryKinds.ts`: Markdown, YAML, JSON, JavaScript, Python and shell.
 * The full `monaco-editor` entry registers ~80 languages and the
 * TypeScript service, whose worker alone is several megabytes. Each
 * language below is highlighting only, loaded on first use, except JSON,
 * which validates in a worker. JavaScript is highlighted without the
 * TypeScript service, so nothing asks for its worker.
 *
 * This module is reached only through `components/MonacoEditor.tsx`,
 * which `ContentEditor` loads lazily, so none of it is in the chunk
 * every page downloads.
 */
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/editor/editor.api'
import 'monaco-editor/features/register.all'
import 'monaco-editor/languages/definitions/markdown/register'
import 'monaco-editor/languages/definitions/yaml/register'
import 'monaco-editor/languages/definitions/javascript/register'
import 'monaco-editor/languages/definitions/python/register'
import 'monaco-editor/languages/definitions/shell/register'
import 'monaco-editor/languages/features/json/register'
import EditorWorker from 'monaco-editor/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/languages/features/json/json.worker?worker'

// Vite bundles each `?worker` import as its own file under assets/, so
// the workers come from the portal's origin too.
self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    return label === 'json' ? new JsonWorker() : new EditorWorker()
  },
}

loader.config({ monaco })
