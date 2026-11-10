import { KIND_BY_ID } from './entryKinds'
import type { CreateOrgGroupEntryRequest, EntryKind } from '../types'

/**
 * Where a plugin is installed from — shown only for plugins. A plugin
 * with a URL is installed from git on the runner instead of being
 * written to disk; without one, its YAML content is written as before.
 */
export interface PluginSource {
  url: string
  ref: string
  subdir: string
}

export const EMPTY_PLUGIN_SOURCE: Readonly<PluginSource> = Object.freeze({ url: '', ref: '', subdir: '' })

/** Everything the new-entry form holds, as typed. */
export interface NewEntryForm {
  kind: EntryKind
  entryName: string
  content: string
  /** Agents only. Null inherits the group's model, then the deployment's. */
  model: string | null
  pluginSource: PluginSource
}

export const INITIAL_NEW_ENTRY_FORM: Readonly<NewEntryForm> = Object.freeze({
  kind: 'agents',
  entryName: '',
  content: KIND_BY_ID.agents.template,
  model: null,
  pluginSource: EMPTY_PLUGIN_SOURCE,
})

export type NewEntryField = 'name' | 'content' | 'sourceUrl'

export type NewEntryErrors = Record<NewEntryField, string | null>

export const NO_NEW_ENTRY_ERRORS: Readonly<NewEntryErrors> = Object.freeze({
  name: null,
  content: null,
  sourceUrl: null,
})

/** A submit that can go, or the first field that stops it. */
export type NewEntryCheck =
  | { ok: true; request: CreateOrgGroupEntryRequest }
  | { ok: false; field: NewEntryField; message: string }

/** The schemes the server accepts for a plugin source; file:// and plain paths are not among them. */
const SOURCE_SCHEMES = ['http:', 'https:', 'git:', 'ssh:']

/**
 * The form after picking `kind`.
 *
 * The content reloads to the new kind's template rather than keeping a
 * scaffold for the old one. The model belongs to agents and the source
 * to plugins, so each is dropped on the way out rather than riding along
 * into a payload for a kind that has neither.
 */
export function withKind(form: NewEntryForm, kind: EntryKind): NewEntryForm {
  if (kind === form.kind) return form
  return {
    ...form,
    kind,
    content: KIND_BY_ID[kind].template,
    model: kind === 'agents' ? form.model : null,
    pluginSource: form.kind === 'plugins' || kind === 'plugins' ? EMPTY_PLUGIN_SOURCE : form.pluginSource,
  }
}

/** Validate `form` the way the server will, and build the request it accepts. */
export function checkNewEntry(form: NewEntryForm): NewEntryCheck {
  const entryName = form.entryName.trim()
  if (!entryName) return { ok: false, field: 'name', message: 'Entry name is required' }

  const source = form.kind === 'plugins' ? checkPluginSource(form.pluginSource) : null
  if (source && 'message' in source) return { ok: false, field: 'sourceUrl', message: source.message }

  // A plugin installed from git needs no content: the runner fetches it.
  if (!form.content.trim() && !source?.source_url) {
    return { ok: false, field: 'content', message: 'Content is required' }
  }
  return {
    ok: true,
    request: {
      kind: form.kind,
      entry_name: entryName,
      content: form.content,
      content_type: KIND_BY_ID[form.kind].contentType,
      enabled: true,
      ...source,
      ...(form.kind === 'agents' && form.model ? { model: form.model } : {}),
    },
  }
}

type PluginSourceRequest = Required<Pick<CreateOrgGroupEntryRequest, 'source_url' | 'source_ref' | 'source_subdir'>>

/** The trimmed source fields, or why the URL is refused. A ref or subdir without a URL is ignored. */
function checkPluginSource(source: PluginSource): PluginSourceRequest | { message: string } {
  const url = source.url.trim()
  if (!url) return { source_url: null, source_ref: null, source_subdir: null }
  let protocol: string
  try {
    protocol = new URL(url).protocol
  } catch {
    return { message: 'Source URL is not a valid URL' }
  }
  if (!SOURCE_SCHEMES.includes(protocol)) return { message: 'Source URL must use http, https, git, or ssh' }
  return { source_url: url, source_ref: source.ref.trim() || null, source_subdir: source.subdir.trim() || null }
}
