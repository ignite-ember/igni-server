"""What a group can hold — one table, because it was five.

The kinds were spelled out separately in the service, the wire schemas
and four places in the portal, so adding one meant finding all of them
and adding it five times. This is the list; everything else derives from
it.

``content_type`` is not a choice the admin makes. igni's loaders
honour exactly one format per kind — an agent is markdown with YAML
frontmatter, an MCP server is JSON, a workflow is an ES module — and
sending anything else is silently dropped rather than rejected, so the
kind decides it.

Four of these ship executable code: a workflow is JavaScript run in
Node, a tool is Python imported into the session's own process, a hook
is a command run on tool use, and a script is a shell file a hook
invokes. That was already true of plugins,
which a group has always been able to send — but it is worth saying
plainly that whoever can edit a group can run code on every machine in
it.

Left out, for now: **LSP servers, monitors and knowledge**. Each is a
single-file config rather than a directory of named entries, so shipping
one entry at a time would need merge semantics this shape does not have.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryKind:
    """One thing an org can ship to a group."""

    #: Wire and storage value. Never rename one of these: it is what the
    #: CLI matches on, and a rename would orphan every existing row.
    id: str
    #: What the portal calls it.
    label: str
    #: The one format igni will read for this kind.
    content_type: str
    #: What the admin is looking at when they pick it.
    description: str


ENTRY_KINDS: tuple[EntryKind, ...] = (
    EntryKind(
        id='agents',
        label='Agents',
        content_type='markdown',
        description='A named agent with its own prompt, tools and model.',
    ),
    EntryKind(
        id='skills',
        label='Skills',
        content_type='markdown',
        description='A reusable prompted routine, invoked as /name.',
    ),
    EntryKind(
        id='commands',
        label='Slash commands',
        content_type='markdown',
        description='A prompt behind /name, with arguments and shell interpolation.',
    ),
    EntryKind(
        id='workflows',
        label='Workflows',
        content_type='javascript',
        description='An ES module that orchestrates agents across phases.',
    ),
    EntryKind(
        id='rules',
        label='Rules',
        content_type='markdown',
        description='Standing instructions, optionally scoped to paths.',
    ),
    EntryKind(
        id='output-styles',
        label='Output styles',
        content_type='markdown',
        description='How answers are written — tone, format, length.',
    ),
    EntryKind(
        id='hooks',
        label='Hooks',
        content_type='json',
        # Said in the picker because it is the trust relationship an admin
        # takes on by writing one (F39): igni runs the command with
        # ``bash -c`` on every member's machine, on a path no permission
        # gates — a developer's ``--strict`` constrains the agent, not this.
        description=(
            'A command run on every member\u2019s machine on an event, such as before a tool '
            'call. It runs outside the agent\u2019s permissions, --strict included.'
        ),
    ),
    EntryKind(
        id='tools',
        label='Tools',
        content_type='python',
        description='A Python toolkit the group\u2019s agents can call.',
    ),
    EntryKind(
        id='mcps',
        label='MCP Servers',
        content_type='json',
        description='An MCP server the group can call.',
    ),
    EntryKind(
        id='plugins',
        label='Plugins',
        content_type='yaml',
        description='A plugin bundle, inline or installed from git.',
    ),
    EntryKind(
        id='scripts',
        label='Scripts',
        content_type='shell',
        description='A shell script a hook can run. Referenced as {group_scripts}/<name>.',
    ),
    EntryKind(
        id='settings',
        label='Settings',
        content_type='json',
        description='Settings merged into every session in the group.',
    ),
)

BY_ID: dict[str, EntryKind] = {kind.id: kind for kind in ENTRY_KINDS}

VALID_KINDS: set[str] = set(BY_ID)
VALID_CONTENT_TYPES: set[str] = {kind.content_type for kind in ENTRY_KINDS}
ALLOWED_CONTENT_TYPE: dict[str, str] = {kind.id: kind.content_type for kind in ENTRY_KINDS}

#: Only agents run against a model of their own.
KINDS_WITH_MODEL: set[str] = {'agents'}

#: Materialised with the executable bit set. A hook's command is run
#: through ``bash -c``, so a script arriving mode 0644 fails with 127 —
#: which the hook runner treats as non-blocking, making it a silent
#: no-op on every matching tool call rather than an error anyone sees.
EXECUTABLE_KINDS: set[str] = {'scripts'}

#: What a hook writes to reach a script the group ships. Expanded by the
#: CLI to the directory it materialised them into.
#:
#: A placeholder rather than an environment variable, though ``bash -c``
#: would expand one for free: this is a string an admin reads in the
#: portal, and a variable's value is invisible there. Spelled out, the
#: reference can be checked against the group's own entries at save
#: time — so "this hook needs a script you have not added" is caught
#: while somebody is looking at it.
GROUP_SCRIPTS_PLACEHOLDER = '{group_scripts}'
