import {
  CopilotIcon,
  FileCodeIcon,
  LawIcon,
  PlayIcon,
  PlugIcon,
  ServerIcon,
  SlidersIcon,
  TerminalIcon,
  TypographyIcon,
  WorkflowIcon,
  type Icon,
} from '@primer/octicons-react'
import type { ContentType, EntryKind } from '../types'

/**
 * What a group can hold.
 *
 * This list was spelled out in four places — the new-entry form, the
 * edit form, the group page's labels and its icons — so adding a kind
 * meant finding all four. Mirrors app/services/portal/entry_kinds.py,
 * which is the one the server enforces.
 *
 * `contentType` is not a choice: igni's loaders honour exactly one
 * format per kind, and sending anything else is dropped rather than
 * rejected.
 */
export interface EntryKindSpec {
  /** Wire value. Never rename one: the CLI matches on it. */
  id: EntryKind
  label: string
  /** Singular, for "New <thing>" and confirmations. */
  singular: string
  contentType: ContentType
  icon: Icon
  /** What the admin is choosing, in one line. */
  description: string
  /** Where igni reads it, shown so the effect is not a mystery. */
  path: string
  /** Starter content, so nobody begins from an empty box. */
  template: string
}

const AGENT_TEMPLATE = `\
---
name: <entry-name>
description: <what this agent does, in one sentence>
tools: WebSearch, Bash
color: cyan
tags:
  - <tag>
---

You are a <role>. <What you are for, and what you refuse to do.>

## Process

1. <first step>
2. <second step>
`

const SKILL_TEMPLATE = `\
---
name: <entry-name>
description: <when to reach for this, in one sentence>
category: development
argument-hint: <what to pass after the name>
---

<The routine, written as instructions. $ARGUMENTS is whatever followed
the command; $1 and $2 are the first and second words of it.>
`

const COMMAND_TEMPLATE = `\
---
description: <what /entry-name does>
argument-hint: <expected arguments>
---

<The prompt this command sends. $ARGUMENTS interpolates what was typed,
!\`git status\` runs a command and inlines its output, @path inlines a file.>
`

const WORKFLOW_TEMPLATE = `\
export const meta = {
  name: '<entry-name>',
  description: '<what this workflow produces>',
  whenToUse: '<the situation that calls for it>',
  phases: [
    { title: 'Explore', detail: 'read the code that matters' },
    { title: 'Change', detail: 'make it, one file at a time' },
  ],
}

phase('Explore')
const findings = await agent('Read <area> and report what it does.')

phase('Change')
await agent(\`Apply the change described here: \\n\${findings}\`)
`

const RULE_TEMPLATE = `\
---
# Drop the paths line to make this apply everywhere.
paths:
  - '**/*.py'
---

<The standing instruction. Written as something to follow, not describe.>
`

const OUTPUT_STYLE_TEMPLATE = `\
---
name: <entry-name>
description: <the register this sets>
---

<How to write. Length, tone, what to lead with, what to leave out.>
`

const TOOL_TEMPLATE = `\
"""A tool the group's agents can call."""

from agno.tools import Toolkit


class <EntryName>Tools(Toolkit):
    def __init__(self):
        super().__init__(name="<entry_name>", tools=[self.lookup])

    def lookup(self, query: str) -> str:
        """<What this returns, and when to call it.>"""
        return "..."
`

export const ENTRY_KINDS: EntryKindSpec[] = [
  {
    id: 'agents',
    label: 'Agents',
    singular: 'agent',
    contentType: 'markdown',
    icon: CopilotIcon,
    description: 'A named agent with its own prompt, tools and model.',
    path: '.igni/agents/<name>.md',
    template: AGENT_TEMPLATE,
  },
  {
    id: 'skills',
    label: 'Skills',
    singular: 'skill',
    contentType: 'markdown',
    icon: FileCodeIcon,
    description: 'A reusable routine, invoked as /name.',
    path: '.igni/skills/<name>/SKILL.md',
    template: SKILL_TEMPLATE,
  },
  {
    id: 'commands',
    label: 'Slash commands',
    singular: 'command',
    contentType: 'markdown',
    icon: TerminalIcon,
    description: 'A prompt behind /name, with arguments and shell interpolation.',
    path: '.igni/commands/<name>.md',
    template: COMMAND_TEMPLATE,
  },
  {
    id: 'workflows',
    label: 'Workflows',
    singular: 'workflow',
    contentType: 'javascript',
    icon: WorkflowIcon,
    description: 'An ES module that orchestrates agents across phases.',
    path: '.igni/workflows/<name>.mjs',
    template: WORKFLOW_TEMPLATE,
  },
  {
    id: 'rules',
    label: 'Rules',
    singular: 'rule',
    contentType: 'markdown',
    icon: LawIcon,
    description: 'Standing instructions, optionally scoped to paths.',
    path: '.igni/rules/<name>.md',
    template: RULE_TEMPLATE,
  },
  {
    id: 'output-styles',
    label: 'Output styles',
    singular: 'output style',
    contentType: 'markdown',
    icon: TypographyIcon,
    description: 'How answers are written — tone, format, length.',
    path: '.igni/output-styles/<name>.md',
    template: OUTPUT_STYLE_TEMPLATE,
  },
  {
    id: 'hooks',
    label: 'Hooks',
    singular: 'hook',
    contentType: 'json',
    icon: PlugIcon,
    // F39: the trust relationship an admin takes on by writing one. igni
    // runs it with `bash -c` on every member's machine, on a path no
    // permission gates — a developer's --strict constrains the agent, not this.
    description:
      'A command run on every member’s machine on an event, such as before a tool call. It runs outside the agent’s permissions, --strict included.',
    path: 'merged into the group’s settings.json',
    template: JSON.stringify(
      {
        event: 'PreToolUse',
        type: 'command',
        command: '<command to run; the payload arrives on stdin>',
        matcher: '<tool name, or a regex>',
        timeout: 15000,
      },
      null,
      2,
    ),
  },
  {
    id: 'tools',
    label: 'Tools',
    singular: 'tool',
    contentType: 'python',
    icon: SlidersIcon,
    description: 'A Python toolkit the group’s agents can call.',
    path: '.igni/tools/<name>.py',
    template: TOOL_TEMPLATE,
  },
  {
    id: 'mcps',
    label: 'MCP Servers',
    singular: 'MCP server',
    contentType: 'json',
    icon: ServerIcon,
    description: 'An MCP server the group can call.',
    path: '.igni/.mcp.json',
    template: JSON.stringify(
      { type: 'stdio', command: '<command>', args: [], env: {} },
      null,
      2,
    ),
  },
  {
    id: 'plugins',
    label: 'Plugins',
    singular: 'plugin',
    contentType: 'yaml',
    icon: PlugIcon,
    description: 'A plugin bundle, inline or installed from git.',
    path: '.igni/plugins/<name>/',
    template: ['name: <entry-name>', 'description: <what it bundles>', '', 'commands: []'].join('\n'),
  },
  {
    id: 'scripts',
    label: 'Scripts',
    singular: 'script',
    contentType: 'shell',
    // Not TerminalIcon: commands already own that, and telling kinds
    // apart at a glance is the point of the column.
    icon: PlayIcon,
    description: 'A shell script a hook can run.',
    path: '~/.igni/group-policy/scripts/<name>.sh',
    template: [
      '#!/bin/bash',
      '# Runs from a hook. The tool payload arrives as JSON on stdin.',
      '#',
      '# Exit 0 to continue, 2 to block the tool call. Any other code is',
      '# treated as "did not block", so a crash is invisible — check your',
      '# own error paths.',
      '',
      'payload=$(cat)',
      '',
      'echo \'{"continue": true}\'',
      '',
    ].join('\n'),
  },
  {
    id: 'settings',
    label: 'Settings',
    singular: 'settings file',
    contentType: 'json',
    icon: SlidersIcon,
    description: 'Settings merged into every session in the group, above each member\u2019s own config.',
    // The template shows the two switches an admin reaches for most,
    // set to their defaults — so it teaches the shape and the key names
    // while saving it unedited changes nothing. `{"<key>": "<value>"}`
    // taught neither. Only igni's own keys: an extension documents its
    // settings itself.
    path: '.igni/settings.json',
    template: [
      '{',
      '  "learning": { "enabled": true },',
      '  "display": { "show_tool_calls": true }',
      '}',
      '',
    ].join('\n'),
  },
]

export const KIND_BY_ID = Object.fromEntries(ENTRY_KINDS.map((kind) => [kind.id, kind])) as Record<
  EntryKind,
  EntryKindSpec
>

/** The order kinds are shown in, most-reached-for first. */
export const KIND_ORDER: EntryKind[] = ENTRY_KINDS.map((kind) => kind.id)

/** Monaco's language id for a kind's content. */
export const EDITOR_LANGUAGE: Record<ContentType, string> = {
  markdown: 'markdown',
  json: 'json',
  yaml: 'yaml',
  javascript: 'javascript',
  python: 'python',
  shell: 'shell',
}

/** The file extension shown on the drop zone. */
export const CONTENT_EXTENSION: Record<ContentType, string> = {
  markdown: '.md',
  json: '.json',
  yaml: '.yaml/.yml',
  javascript: '.mjs',
  python: '.py',
  shell: '.sh',
}

/** `owner/repo` from a clone URL — the part anybody recognises. */
export function prettySource(url: string): string {
  const trimmed = url.replace(/\.git$/, '')
  const parts = trimmed.split('/').filter(Boolean)
  return parts.slice(-2).join('/') || trimmed
}
