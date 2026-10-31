"""Does this agent's frontmatter name it, or still say ``<entry-name>``?

The loader keys agents on the ``name:`` in their frontmatter, not on the
filename the entry is stored under. So two entries whose frontmatter
carries the same name are one agent by the time a session sees them, and
the loser vanishes without a word — no error, no warning, just an agent
the group thinks it ships and nobody has.

The template the portal scaffolds a new agent from opens with
``name: <entry-name>``, and it is genuinely easy to fill in the body and
never touch that line. Found in the demo group during the production
check: three agents — ``coder``, ``designer``, ``party-maker`` — all
still carrying the placeholder, so the session loaded exactly one thing
called ``<entry-name>``.

Advisory rather than a refusal, matching :mod:`agent_tools` and
:mod:`hook_scripts`. Entries are saved one at a time, so refusing on a
collision would make the order somebody creates them in matter; and an
admin part-way through editing should be told, not blocked.
"""

from __future__ import annotations

import re

import yaml

#: What the portal's "new agent" template puts on the ``name:`` line.
#: Kept in sync with ``portal/src/lib/entryKinds.ts``.
PLACEHOLDER_NAME = '<entry-name>'

_FRONTMATTER = re.compile(r'^\s*---\s*\n(.*?)\n---', re.S)


def frontmatter_name(content: str) -> str | None:
    """The ``name:`` an agent declares, or None if it declares none.

    None covers a missing frontmatter block, unparseable YAML, and a
    block with no ``name`` key. All three mean the same thing to the
    caller — nothing to compare — and telling them apart would not
    change the advice.
    """
    match = _FRONTMATTER.search(content or '')
    if not match:
        return None
    try:
        block = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(block, dict):
        return None
    name = block.get('name')
    return str(name) if name is not None and str(name).strip() else None


def warning_for(content: str, *, other_names: set[str] | None = None) -> str | None:
    """What to tell an admin about the name this agent will load under.

    *other_names* is the set of frontmatter names already used by the
    group's other agent entries, so a collision can be named. Omit it to
    check only the placeholder.
    """
    name = frontmatter_name(content)

    if name is None:
        return (
            'This agent has no `name:` in its frontmatter, so a session will skip it '
            'entirely — the loader identifies an agent by that field, not by the entry '
            'name here.'
        )

    if name == PLACEHOLDER_NAME:
        return (
            f'The `name:` line still reads `{PLACEHOLDER_NAME}` from the template. A '
            f'session identifies an agent by that field rather than by the entry name, '
            f'so every agent left like this collapses into one and the rest are silently '
            f'dropped. Change it to the name the agent should load under.'
        )

    if other_names and name in other_names:
        return (
            f'Another agent in this group already declares `name: {name}`. A session '
            f'keeps one of them and drops the other without reporting it, because the '
            f'loader identifies an agent by that field rather than by the entry name.'
        )

    return None
