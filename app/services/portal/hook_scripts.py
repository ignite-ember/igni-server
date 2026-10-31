"""Does a hook reach a script the group actually ships?

A hook's ``command`` is run through ``bash -c``. If it names a file that
is not there, bash exits 127, and the hook runner reads any non-zero
exit other than 2 as "did not block" — so a broken reference is a
**silent no-op on every matching tool call**. Nobody sees an error: not
the admin who wrote it, not the developer whose hook never fires.

That is the whole argument for ``{group_scripts}/<name>`` being a
literal placeholder rather than an environment variable bash would
expand for free. Written out, the reference can be compared against the
group's own ``scripts`` entries while somebody is still looking at the
form.

**A warning, not a refusal.** The script may be added in the next
minute, entries are saved one at a time, and refusing would make the
order they are created in matter. It is also legitimate for a command to
reference a path this deployment cannot see — a hook may deliberately
call something baked into a customer's image.
"""

from __future__ import annotations

import json
import re

#: ``{group_scripts}/name.sh``, however it is quoted.
_REFERENCE = re.compile(r'\{group_scripts\}/([A-Za-z0-9._-]+)')


def referenced_scripts(hook_content: str) -> list[str]:
    """Entry names a hook's command expects to find under scripts/.

    Returns entry *names*, so ``{group_scripts}/pre-pr-review.sh`` gives
    ``pre-pr-review`` — the extension belongs to the materialised file,
    not to the entry.
    """
    try:
        declaration = json.loads(hook_content)
    except (TypeError, ValueError):
        return []
    if not isinstance(declaration, dict):
        return []

    command = declaration.get('command')
    if not isinstance(command, str):
        return []

    names: list[str] = []
    for match in _REFERENCE.finditer(command):
        name = match.group(1)
        names.append(name[:-3] if name.endswith('.sh') else name)
    return names


def warning_for(hook_content: str, *, available_scripts: set[str]) -> str | None:
    """One line about scripts this hook needs and the group lacks."""
    missing = [name for name in referenced_scripts(hook_content) if name not in available_scripts]
    if not missing:
        return None

    listed = ', '.join(repr(name) for name in missing)
    subject = 'a script' if len(missing) == 1 else 'scripts'
    return (
        f'This hook runs {listed}, which the group does not ship. Add {subject} entry of that '
        'name, or the hook will do nothing on every machine in the group — a missing command '
        'fails silently rather than reporting an error.'
    )
