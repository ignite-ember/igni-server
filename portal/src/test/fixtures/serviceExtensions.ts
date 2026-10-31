/**
 * Extensions as `GET /v1/portal/clients/{client_id}` returns them.
 *
 * The manifests are copies of the real ones —
 * `igni__artifacts/extension/manifest.json` and
 * `igni__code-index/extension/manifest.json` — so the detail page is
 * tested against what services actually send rather than a shape made
 * up to suit it. The plugins are what igni server's summary of those
 * bundles says: Artifacts' plugin carries its own archivist, and
 * CodeIndex overrides igni's main prompt and some of its agents with
 * files in its plugin's `overrides/`, and edits others from `edits/`.
 * The skill is made up: neither plugin ships one yet, and the page has
 * to show them when one does.
 */
import type { ServiceExtensionDetail } from '../../types'

export const ARTIFACTS_MANIFEST: Record<string, unknown> = {
  "$schema": "https://igni.sh/schema/extension-manifest-1.json",
  "id": "igni.artifacts",
  "version": "0.1.0",
  "contract": ">=1.0 <2",
  "name": "Artifacts",
  "description": "Documents the agent writes and cites, with versions, lineage and comments.",
  "vendor": "Ignite Ember",
  "trust": "unsigned",
  "dev": {
    "command": [
      "igni-artifacts-extension"
    ]
  },
  "permissions": {
    "workspace": "read",
    "workspacePaths": [],
    "egress": [],
    "secrets": [
      "artifacts-service-token"
    ],
    "tools": [
      { "name": "igni.artifacts.artifact_ask", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_confirm_source", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_create", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_cypher", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_delete", "annotations": { "readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_dismiss_source", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_edit_draft", "annotations": { "readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_lineage", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_list", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_pending_sources", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_publish_draft", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_read", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_reconcile", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_repin", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_revise", "annotations": { "readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_search", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_stale", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_status", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } },
      { "name": "igni.artifacts.artifact_versions", "annotations": { "readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false } }
    ],
    "rpc": [
      "ext.igni.artifacts.*"
    ],
    "events": [
      "workspace.head-changed"
    ],
    "host": [
      "links",
      "decide"
    ],
    "hooks": ["PlanSubmit", "SessionStart"],
    "toolAudience": "own-agents"
  },
  "contributes": {
    "pages": [
      {
        "kind": "igni.artifacts",
        "label": "Artifacts",
        "sidebar": true
      }
    ],
    "menu": [
      {
        "label": "Artifacts",
        "command": "/artifacts",
        "desc": "documents the agent writes and cites"
      }
    ],
    "commands": [
      {
        "command": "/artifacts",
        "desc": "project documents"
      }
    ],
    "indicators": [
      {
        "id": "igni.artifacts.waiting",
        "label": "Artifacts",
        "source": "indicator",
        "opens": "igni.artifacts",
        "intervalSeconds": 30
      }
    ],
    "promptFragments": [
      {
        "anchor": "ARTIFACT_GRAPH_SCHEMA",
        "source": "tool",
        "from": "graph_schema"
      }
    ],
    "settings": [
      {
        "key": "serviceUrl",
        "type": "string",
        "label": "Artifacts service",
        "default": ""
      },
      {
        "key": "requireSources",
        "type": "boolean",
        "label": "Refuse a document with no cited source",
        "default": true
      }
    ],
    "plugin": "plugin",
    "prompt": {
      "edits": [
        { "target": "main", "replace": "project_knowledge", "source": "file", "from": "plugin/prompts/persisting.md" },
        { "target": "main", "replace": "durable_knowledge", "source": "file", "from": "plugin/prompts/when-to-persist.md" },
        { "target": "main", "replace": "reading", "source": "file", "from": "plugin/prompts/reading.md" },
        { "target": "main", "replace": "project_documents", "source": "file", "from": "plugin/prompts/delegation.md" }
      ]
    }
  }
}

export const CODEINDEX_MANIFEST: Record<string, unknown> = {
  "$schema": "https://igni.sh/schema/extension-manifest-1.json",
  "id": "igni.codeindex",
  "version": "0.1.0",
  "contract": ">=1.0 <2",
  "name": "CodeIndex",
  "description": "Semantic search across your repository, from an index built per commit.",
  "vendor": "Ignite Ember",
  "trust": "unsigned",
  "dev": {
    "command": [
      "igni-codeindex-extension"
    ]
  },
  "assets": [
    {
      "name": "neo4j",
      "platform": "any",
      "file": "neo4j-community-5.26.0-unix.tar.gz",
      "size": 158822080,
      "sha256": "ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7",
      "source": "https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz",
      "unpack": true
    },
    {
      "name": "jre",
      "platform": "macos-arm64",
      "file": "OpenJDK21U-jre_aarch64_mac_hotspot_21.0.12.1_1.tar.gz",
      "size": 48144965,
      "sha256": "dec50fc6f9fcd4fe3ae8cabf5a5fa68f6afc48841f7698e468e9aa5d54beed84",
      "source": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_aarch64_mac_hotspot_21.0.12.1_1.tar.gz",
      "unpack": true
    },
    {
      "name": "jre",
      "platform": "linux-x86_64",
      "file": "OpenJDK21U-jre_x64_linux_hotspot_21.0.12.1_1.tar.gz",
      "size": 52059408,
      "sha256": "2413149700df0f7d440500a84a8f764c535f21e5a5e87d38328b64eec2c5b500",
      "source": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_linux_hotspot_21.0.12.1_1.tar.gz",
      "unpack": true
    },
    {
      "name": "jre",
      "platform": "windows-x86_64",
      "file": "OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip",
      "size": 48999141,
      "sha256": "d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636",
      "source": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip",
      "unpack": true
    },
    {
      "name": "minilm",
      "platform": "any",
      "file": "model.onnx",
      "size": 90405214,
      "sha256": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
      "source": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx/model.onnx",
      "unpack": false
    },
    {
      "name": "minilm-tokenizer",
      "platform": "any",
      "file": "tokenizer.json",
      "size": 466247,
      "sha256": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
      "source": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer.json",
      "unpack": false
    }
  ],
  "permissions": {
    "workspace": "read",
    "egress": [],
    "secrets": [
      "codeindex-token"
    ],
    "tools": [],
    "rpc": [
      "ext.igni.codeindex.*"
    ],
    "events": [
      "workspace.head-changed"
    ]
  },
  "contributes": {
    "pages": [
      {
        "kind": "igni.codeindex",
        "label": "CodeIndex",
        "sidebar": true
      }
    ],
    "menu": [
      {
        "label": "CodeIndex",
        "command": "/codeindex",
        "desc": "semantic code search"
      }
    ],
    "commands": [
      {
        "command": "/codeindex",
        "desc": "semantic code search"
      }
    ],
    "indicators": [
      {
        "id": "igni.codeindex.freshness",
        "label": "CodeIndex",
        "source": "indicator",
        "opens": "igni.codeindex",
        "intervalSeconds": 30
      }
    ],
    "promptFragments": [
      {
        "anchor": "CODEINDEX_GRAPH_SCHEMA",
        "source": "tool",
        "from": "graph_schema"
      }
    ],
    "prompt": {
      "overrides": [
        { "target": "main", "sections": ["identity", "code_intelligence", "workflow"], "source": "file", "from": "plugin/overrides/main.md", "when": "index-covers-head" },
        { "target": "agent:data-architect", "sections": ["intro", "schema"], "source": "file", "from": "plugin/overrides/data-architect.md", "when": "index-covers-head" },
        { "target": "agent:explorer", "sections": ["intro", "search_strategy"], "exclusive": true, "source": "file", "from": "plugin/overrides/explorer.md", "when": "index-covers-head" }
      ],
      "edits": [
        { "target": "plan_mode", "append": "code_intelligence", "source": "file", "from": "plugin/edits/plan_mode.md", "when": "index-covers-head" },
        { "target": "agent:architect", "replace": "frontmatter", "source": "file", "from": "plugin/edits/architect/frontmatter.md", "when": "index-covers-head" },
        { "target": "agent:architect", "insert": "index_stores", "after": "intro", "source": "file", "from": "plugin/edits/architect/index_stores.md", "when": "index-covers-head" }
      ]
    },
    "plugin": "plugin"
  }
}

export const ARTIFACTS_EXTENSION: ServiceExtensionDetail = {
  extension_id: 'igni.artifacts',
  version: '0.1.0',
  name: 'Artifacts',
  description: 'Documents the agent writes and cites, with versions, lineage and comments.',
  published_at: '2026-09-01T10:01:00Z',
  sha256: '9f2c4e1ab07d53c8e6f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7',
  manifest: ARTIFACTS_MANIFEST,
  plugin: {
    agents: [
      {
        file: 'plugin/agents/archivist.md',
        name: 'archivist',
        description:
          "The only agent that can reach the project's document store, for reading as well as writing.",
      },
    ],
    agent_overrides: [],
    skills: [],
  },
  review: {
    impact: {
      extension_id: 'igni.artifacts',
      name: 'Artifacts',
      version: '0.1.0',
      prompt_overrides: [],
      prompt_edits: ['project_knowledge', 'durable_knowledge', 'reading', 'project_documents'].map((section) => ({
        target: 'main',
        op: 'replace' as const,
        section,
        position: null,
        name: null,
        when: null,
      })),
      anchors: ['ARTIFACT_GRAPH_SCHEMA'],
      commands: ['/artifacts'],
    },
    unknown_targets: [],
    igni_commands: [],
    collisions: [],
    switched_off: [],
    dropped: [],
    drops_others: {},
  },
}

export const CODEINDEX_EXTENSION: ServiceExtensionDetail = {
  extension_id: 'igni.codeindex',
  version: '0.1.0',
  name: 'CodeIndex',
  description: 'Semantic search across your repository, from an index built per commit.',
  published_at: '2026-08-01T10:01:00Z',
  sha256: '0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e',
  manifest: CODEINDEX_MANIFEST,
  plugin: {
    agents: [],
    agent_overrides: [
      {
        agent: 'data-architect',
        file: 'plugin/overrides/data-architect.md',
        description: "Cypher author for igni's CodeIndex. Read-only — cannot modify files.",
        exclusive: false,
        when: 'index-covers-head',
      },
      {
        agent: 'explorer',
        file: 'plugin/overrides/explorer.md',
        description:
          'Deeply analyzes existing codebase features by tracing execution paths, mapping architecture layers, and documenting dependencies. Read-only — cannot modify files. CodeIndex-first variant.',
        exclusive: true,
        when: 'index-covers-head',
      },
    ],
    skills: [{ name: 'cypher-recipes', description: 'Worked Cypher queries for the per-commit graph.' }],
  },
  review: {
    impact: {
      extension_id: 'igni.codeindex',
      name: 'CodeIndex',
      version: '0.1.0',
      prompt_overrides: [
        { target: 'agent:data-architect', sections: ['intro', 'schema'], exclusive: false, when: 'index-covers-head' },
        { target: 'agent:explorer', sections: ['intro', 'search_strategy'], exclusive: true, when: 'index-covers-head' },
        { target: 'main', sections: ['identity', 'code_intelligence', 'workflow'], exclusive: false, when: 'index-covers-head' },
      ],
      prompt_edits: [
        { target: 'plan_mode', op: 'append', section: null, position: null, name: 'code_intelligence', when: 'index-covers-head' },
      ],
      anchors: ['CODEINDEX_GRAPH_SCHEMA'],
      commands: ['/codeindex'],
    },
    unknown_targets: [],
    igni_commands: [],
    collisions: [
      {
        claim: 'prompt:agent:explorer',
        extensions: ['acme.scout', 'igni.codeindex'],
        winner: 'acme.scout',
        losers: ['igni.codeindex'],
      },
      {
        claim: 'command:/codeindex',
        extensions: ['acme.search', 'igni.codeindex'],
        winner: 'igni.codeindex',
        losers: ['acme.search'],
      },
    ],
    switched_off: [
      {
        claim: 'prompt:agent:explorer',
        reason: 'lower priority than acme.scout, which also overrides agent:explorer',
      },
    ],
    dropped: [],
    drops_others: {
      'acme.docs': [
        {
          claim: 'prompt:main#reading',
          reason: "igni.codeindex overrides main while igni.codeindex holds index-covers-head, and its prompt has no section 'reading'",
          by: 'igni.codeindex',
        },
      ],
    },
  },
}
