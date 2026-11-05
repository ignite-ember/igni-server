"""Catch a mistyped tool name while somebody is still looking at it.

An agent names its tools in frontmatter::

    tools: WebSearch, Bash

A name igni does not know is accepted by every step until the agent is
actually invoked, and then it raises — mid-task, for whoever happened to
call it, possibly weeks after the typo was saved. The admin who wrote
``WebSarch`` never sees it.

So the write path says something. **A warning, not a refusal**: this
list is a copy of igni's, and a copy can lag. Refusing an unknown name
would mean a deployment running a newer igni could not use its newer
tools until somebody refreshed this file — a false rejection is worse
than a missed typo, because it blocks work that is correct.

**A module's or extension's tools are not names here, and that is
right.** An agent naming ``CodeIndex`` (or any tool a paid module or an
approved extension provides) in ``tools:`` is warned about, and the
warning is true: igni resolves ``tools:`` against its own catalogue
only, and an extension's tools reach an agent over MCP — the
extension's server, offered to the agents it ships and to any whose
``mcp_servers:`` names it — never by a name in this list. igni's own
agents panel reports such a name as unknown, and the agent raises when
called. Accepting the names approved extensions declare would silence
the one warning that says so; the agent is what needs changing.
"""

from __future__ import annotations

import difflib
import re

import yaml

#: What ``ToolRegistry.available_tools`` answers. Refresh with
#: ``make refresh-agent-tools``.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        'Bash',
        'BashOutput',
        'Edit',
        'NotebookEdit',
        'Orchestrate',
        'Schedule',
        'Visualize',
        'WebFetch',
        'WebSearch',
        'Write',
    }
)

#: Names that resolve to something without being in the catalogue: an
#: MCP server's tools, and the pseudo-tools igni reserves.
_ALWAYS_ALLOWED = ('MCP:', 'mcp:')

_FRONTMATTER = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)


def declared_tools(content: str) -> list[str]:
    """The tool names an agent's frontmatter asks for.

    Accepts both spellings the loader does — a comma-joined string and a
    YAML list — and answers an empty list for anything it cannot parse,
    since a malformed agent has a bigger problem than its tool names.
    """
    match = _FRONTMATTER.match(content)
    if not match:
        return []
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return []
    if not isinstance(frontmatter, dict):
        return []

    raw = frontmatter.get('tools')
    if isinstance(raw, str):
        return [name.strip() for name in raw.split(',') if name.strip()]
    if isinstance(raw, list):
        return [str(name).strip() for name in raw if str(name).strip()]
    return []


def unknown_tools(content: str) -> list[str]:
    """Tool names igni will not recognise, in the order they appear."""
    return [
        name for name in declared_tools(content) if name not in KNOWN_TOOLS and not name.startswith(_ALWAYS_ALLOWED)
    ]


def warning_for(content: str) -> str | None:
    """One line about the mistyped tools, or None if there are none.

    Names the likely intent where there is one: a typo is nearly always
    one character from the tool it meant, and "did you mean WebSearch?"
    is the whole message somebody needs.
    """
    unknown = unknown_tools(content)
    if not unknown:
        return None

    parts: list[str] = []
    for name in unknown:
        close = difflib.get_close_matches(name, sorted(KNOWN_TOOLS), n=1, cutoff=0.6)
        parts.append(f'{name!r} (did you mean {close[0]!r}?)' if close else repr(name))

    subject = 'is not a tool igni knows' if len(parts) == 1 else 'are not tools igni knows'
    return f'{", ".join(parts)} {subject}. The agent will fail when something calls it.'
