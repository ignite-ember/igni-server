/**
 * An extension manifest, read for a person deciding whether to approve it.
 *
 * The portal shows the manifest exactly as the service sent it, and the
 * service is the party being vetted — so nothing here assumes a key is
 * present or has the type the schema says. A missing list reads as
 * empty, a missing value as `null`, and an entry of the wrong shape is
 * dropped rather than rendered half-way. The schema itself lives with
 * the host (`docs/extension-manifest.schema.json` in ember-code).
 */

type Json = Record<string, unknown>

export type WorkspaceAccess = 'none' | 'read' | 'write'
export type ToolAudience = 'all-agents' | 'own-agents'

export interface ManifestPage {
  kind: string
  label: string
  sidebar: boolean
}

export interface ManifestMenuEntry {
  label: string
  command: string | null
  desc: string | null
}

export interface ManifestCommand {
  command: string
  desc: string | null
}

export interface ManifestIndicator {
  id: string
  label: string
  source: string | null
  opens: string | null
  intervalSeconds: number | null
}

export interface ManifestSetting {
  key: string
  type: string | null
  label: string | null
  /** As declared; `undefined` when there is none. */
  default: unknown
}

export interface ManifestPromptFragment {
  anchor: string
  source: string | null
  from: string | null
}

/**
 * A file the extension runs that is not Python — a JVM, a database, a
 * model — shipped beside its bundle so a machine with no network can
 * install it. Pinned by size and digest in the signed manifest.
 */
export interface ManifestAsset {
  name: string
  /** `any`, or the one platform it is for. */
  platform: string
  file: string
  size: number | null
  sha256: string | null
  /** Where the service's build fetched it. */
  source: string | null
  unpack: boolean
}

/** How the extension runs on a machine: exactly one of these is declared. */
export interface ManifestPackaging {
  kind: 'python' | 'sidecar' | 'endpoint' | 'dev'
  /** What it runs or reaches, in the manifest's own words. */
  details: { label: string; value: string }[]
}

/**
 * A tool the agent may call, and the four MCP hints igni's permission
 * modes decide on: plan mode runs a read-only tool, Accept Edits a
 * contained write, the default mode asks before any write.
 */
export interface ManifestTool {
  name: string
  readOnly: boolean | null
  destructive: boolean | null
  idempotent: boolean | null
  openWorld: boolean | null
}

const HINTS = ['readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint'] as const

function flag(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

/** The tool's hints for a person: `read-only, local, idempotent`. `null` when one is missing. */
export function describeHints(tool: ManifestTool): string | null {
  const { readOnly, destructive, idempotent, openWorld } = tool
  if (readOnly === null || destructive === null || idempotent === null || openWorld === null) return null
  const parts = [readOnly ? 'read-only' : destructive ? 'destructive' : 'changes things']
  parts.push(openWorld ? 'reaches outside' : 'local')
  if (idempotent) parts.push('idempotent')
  return parts.join(', ')
}

/** igni's hook events whose answer decides something; the rest it is only told. */
export const DECIDING_HOOKS: ReadonlySet<string> = new Set([
  'PreToolUse',
  'PostToolUse',
  'UserPromptSubmit',
  'Stop',
  'PlanSubmit',
])

/** One of igni's hook events its process answers (`permissions.hooks`). */
export interface ManifestHook {
  event: string
  /** Its answer decides the event: it may stop or change it. Otherwise it is only told. */
  decides: boolean
}

export interface ManifestPermissions {
  workspace: WorkspaceAccess
  /** Empty when absent or empty: the whole project, as the host reads it. */
  workspacePaths: string[]
  egress: string[]
  secrets: string[]
  tools: ManifestTool[]
  toolAudience: ToolAudience
  rpc: string[]
  events: string[]
  /** What it may reach of igni on each machine: host-model grants (`chat.read`, `decide`, …). */
  host: string[]
  /** igni's hook events its process answers. */
  hooks: ManifestHook[]
}

const PACKAGINGS = ['python', 'sidecar', 'endpoint', 'dev'] as const

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  if (typeof value === 'string') return value.trim() === '' ? null : value
  if (typeof value === 'number') return String(value)
  return null
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter((v): v is string => v !== null) : []
}

function records(value: unknown): Json[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

export class ExtensionManifest {
  private readonly raw: Json
  private readonly contributes: Json
  private readonly granted: Json

  constructor(raw: unknown) {
    this.raw = isRecord(raw) ? raw : {}
    this.contributes = isRecord(this.raw.contributes) ? this.raw.contributes : {}
    this.granted = isRecord(this.raw.permissions) ? this.raw.permissions : {}
  }

  get id(): string | null {
    return text(this.raw.id)
  }

  get version(): string | null {
    return text(this.raw.version)
  }

  get name(): string | null {
    return text(this.raw.name)
  }

  get description(): string | null {
    return text(this.raw.description)
  }

  get vendor(): string | null {
    return text(this.raw.vendor)
  }

  get trust(): string | null {
    return text(this.raw.trust)
  }

  /** The range of igni's extension contract it is written against. */
  get contract(): string | null {
    return text(this.raw.contract)
  }

  get packaging(): ManifestPackaging | null {
    const kind = PACKAGINGS.find((name) => isRecord(this.raw[name]))
    if (!kind) return null
    const declared = this.raw[kind] as Json
    const details: { label: string; value: string }[] = []
    const add = (label: string, value: string | null) => {
      if (value) details.push({ label, value })
    }
    if (kind === 'python') {
      add('Package', text(declared.package))
      add('Runs', text(declared.entry))
      add('Wheels from', text(declared.wheels))
    } else if (kind === 'sidecar') {
      add('Image', text(declared.image))
    } else if (kind === 'endpoint') {
      add('Address', text(declared.url))
      add('Authenticates with', isRecord(declared.auth) ? text(declared.auth.kind) : null)
    } else {
      const command = strings(declared.command)
      add('Runs', command.length > 0 ? command.join(' ') : null)
      add('In', text(declared.cwd))
    }
    if (kind !== 'endpoint' && isRecord(declared.mcp)) add('Speaks MCP over', text(declared.mcp.transport))
    return { kind, details }
  }

  get assets(): ManifestAsset[] {
    return records(this.raw.assets).flatMap((item) => {
      const name = text(item.name)
      const file = text(item.file)
      if (!name || !file) return []
      return [
        {
          name,
          platform: text(item.platform) ?? 'any',
          file,
          size: typeof item.size === 'number' ? item.size : null,
          sha256: text(item.sha256),
          source: text(item.source),
          unpack: item.unpack === true,
        },
      ]
    })
  }

  get permissions(): ManifestPermissions {
    const p = this.granted
    const workspace = text(p.workspace)
    return {
      workspace: workspace === 'read' || workspace === 'write' ? workspace : 'none',
      workspacePaths: strings(p.workspacePaths),
      egress: strings(p.egress),
      secrets: strings(p.secrets),
      tools: records(p.tools).flatMap((tool) => {
        const name = text(tool.name)
        const hints = isRecord(tool.annotations) ? tool.annotations : {}
        const [readOnly, destructive, idempotent, openWorld] = HINTS.map((hint) => flag(hints[hint]))
        return name ? [{ name, readOnly, destructive, idempotent, openWorld }] : []
      }),
      toolAudience: text(p.toolAudience) === 'own-agents' ? 'own-agents' : 'all-agents',
      rpc: strings(p.rpc),
      events: strings(p.events),
      host: strings(p.host),
      hooks: strings(p.hooks).map((event) => ({ event, decides: DECIDING_HOOKS.has(event) })),
    }
  }

  /**
   * Whether an RPC grant reaches past the extension's own service.
   *
   * Its service answers under `ext.<id>.`; any other name asks for igni's
   * own data, which is the line worth reading twice.
   */
  reachesIgni(method: string): boolean {
    const own = this.id ? `ext.${this.id}.` : null
    return own === null || !method.startsWith(own)
  }

  get pages(): ManifestPage[] {
    return records(this.contributes.pages).flatMap((item) => {
      const kind = text(item.kind)
      const label = text(item.label) ?? kind
      return kind && label ? [{ kind, label, sidebar: item.sidebar === true }] : []
    })
  }

  get menu(): ManifestMenuEntry[] {
    return records(this.contributes.menu).flatMap((item) => {
      const label = text(item.label)
      return label ? [{ label, command: text(item.command), desc: text(item.desc) }] : []
    })
  }

  get commands(): ManifestCommand[] {
    return records(this.contributes.commands).flatMap((item) => {
      const command = text(item.command)
      return command ? [{ command, desc: text(item.desc) }] : []
    })
  }

  get indicators(): ManifestIndicator[] {
    return records(this.contributes.indicators).flatMap((item) => {
      const id = text(item.id)
      const label = text(item.label) ?? id
      if (!id || !label) return []
      const interval = typeof item.intervalSeconds === 'number' ? item.intervalSeconds : null
      return [{ id, label, source: text(item.source), opens: text(item.opens), intervalSeconds: interval }]
    })
  }

  get settings(): ManifestSetting[] {
    return records(this.contributes.settings).flatMap((item) => {
      const key = text(item.key)
      return key ? [{ key, type: text(item.type), label: text(item.label), default: item.default }] : []
    })
  }

  get promptFragments(): ManifestPromptFragment[] {
    return records(this.contributes.promptFragments).flatMap((item) => {
      const anchor = text(item.anchor)
      return anchor ? [{ anchor, source: text(item.source), from: text(item.from) }] : []
    })
  }

  /** `contributes.prompt`: the prompts it overrides whole, and the edits it makes to others. */
  get prompt(): ManifestPrompt {
    const prompt = isRecord(this.contributes.prompt) ? this.contributes.prompt : {}
    return {
      overrides: records(prompt.overrides).flatMap((item) => {
        const target = text(item.target)
        if (!target) return []
        return [
          {
            target,
            sections: Array.isArray(item.sections) ? item.sections.filter((s): s is string => typeof s === 'string') : [],
            exclusive: item.exclusive === true,
            source: text(item.source),
            from: text(item.from),
            when: text(item.when),
          },
        ]
      }),
      edits: records(prompt.edits).flatMap((item) => {
        const target = text(item.target)
        if (!target) return []
        const replace = text(item.replace)
        const insert = text(item.insert)
        const append = text(item.append)
        const op: ManifestPromptEdit['op'] | null = replace ? 'replace' : insert ? 'insert' : append ? 'append' : null
        if (!op) return []
        return [
          {
            target,
            op,
            section: replace ?? text(item.before) ?? text(item.after),
            position: text(item.before) ? 'before' : text(item.after) ? 'after' : null,
            name: insert ?? append,
            source: text(item.source),
            from: text(item.from),
            when: text(item.when),
          },
        ]
      }),
    }
  }
}

export interface ManifestPromptText {
  source: string | null
  from: string | null
  when: string | null
}

/** A whole prompt the extension brings. */
export interface ManifestPromptOverride extends ManifestPromptText {
  target: string
  sections: string[]
  exclusive: boolean
}

/** One change the extension makes to a prompt. */
export interface ManifestPromptEdit extends ManifestPromptText {
  target: string
  op: 'append' | 'insert' | 'replace'
  section: string | null
  position: 'before' | 'after' | null
  name: string | null
}

export interface ManifestPrompt {
  overrides: ManifestPromptOverride[]
  edits: ManifestPromptEdit[]
}

export interface PluginAgent {
  name: string
  description: string | null
  file: string | null
}

/** An agent the extension overrides, as its own file defines it while it runs. */
export interface PluginAgentOverride {
  agent: string
  description: string | null
  file: string | null
  exclusive: boolean
  when: string | null
}

export interface PluginSkill {
  name: string
  description: string | null
}

/** The plugin's agents, the agents it overrides and its skills, as the server summarised them — read as defensively as the manifest. */
export class ExtensionPluginContents {
  readonly agents: PluginAgent[]
  readonly agentOverrides: PluginAgentOverride[]
  readonly skills: PluginSkill[]

  constructor(raw: unknown) {
    const plugin = isRecord(raw) ? raw : {}
    this.agents = records(plugin.agents).flatMap((item) => {
      const file = text(item.file)
      const name = text(item.name) ?? file
      return name ? [{ name, description: text(item.description), file }] : []
    })
    this.agentOverrides = records(plugin.agent_overrides).flatMap((item) => {
      const agent = text(item.agent)
      return agent
        ? [
            {
              agent,
              description: text(item.description),
              file: text(item.file),
              exclusive: item.exclusive === true,
              when: text(item.when),
            },
          ]
        : []
    })
    this.skills = records(plugin.skills).flatMap((item) => {
      const name = text(item.name)
      return name ? [{ name, description: text(item.description) }] : []
    })
  }
}
