"""One detector for "does this logger call write user content".

F83 wrote this as an AST walk inside `test_webhook_failures_are_client_errors.py`
and pointed it at the two webhook directories. It was right, and F111
found what the scope cost: `/v1/chat/completions` logged `json.dumps(body)`
— the prompt — for months, three directories away from where the rule was
looking.

Moved here so both callers share one copy. Two copies of a rule is the
problem F100 had to solve for a backup script, and a rule is worse to
duplicate than a script: the copies diverge and each looks authoritative.

## What counts as a leak

A `logger.*` call that passes one of the raw request objects *as a whole*:

    logger.info('%s', json.dumps(body))        # the object, serialised
    logger.warning(f'{payload}')               # the object, interpolated
    logger.info('%s', body.decode())           # a method call on it
    logger.debug('x', extra={'p': payload})    # anywhere in the arguments

## What does not

Reaching *into* it for a scalar the operator needs:

    logger.info('repo %s', body.repository_id)
    logger.info('App %r registered', payload['slug'])
    logger.info('size=%d', len(body))
    logger.info('%s', describe(payload))

The first version flagged those too, because `ast.walk` finds the `Name`
node inside an attribute access. Six of the seven hits across `app/` were
that false positive — and a rule with six false positives is a rule
somebody deletes, so extending it to the whole application depended on
telling the two apart.

**Content keys are still flagged even when reached into**, because
`body['messages']` is the prompt and `payload['commits']` is every commit
message in a push. Field access narrows to a scalar only if the field is
not itself the content.
"""

from __future__ import annotations

import ast

#: Variable names that hold a raw request object.
RAW_NAMES = frozenset({'body', 'payload', 'raw', 'raw_body'})

#: Calls whose result is safe: a shape description or a size.
SAFE_WRAPPERS = frozenset({'describe', 'describe_payload', 'describe_error', 'describe_chat_request', 'len'})

#: Fields that *are* the content, so reaching into them leaks anyway.
CONTENT_FIELDS = frozenset(
    {'messages', 'content', 'prompt', 'text', 'commits', 'body', 'payload', 'choices', 'tool_calls'}
)


def _safely_wrapped(call: ast.Call) -> set[str]:
    """Names appearing only inside a safe wrapper within this call."""
    safe: set[str] = set()
    for node in ast.walk(call):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
        if name in SAFE_WRAPPERS:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name):
                    safe.add(inner.id)
    return safe


def _leaks(node: ast.AST, safe: set[str]) -> str | None:
    """The raw name this expression exposes as a whole object, if any."""
    if isinstance(node, ast.Name):
        return node.id if node.id in RAW_NAMES and node.id not in safe else None

    if isinstance(node, ast.Call):
        # A method call on the raw object — `.decode()`, `.json()`,
        # `.model_dump()` — hands over the whole thing.
        func = node.func
        if isinstance(func, ast.Attribute):
            inner = _leaks(func.value, safe)
            if inner:
                return inner
        for child in list(node.args) + [kw.value for kw in node.keywords]:
            found = _leaks(child, safe)
            if found:
                return found
        return None

    if isinstance(node, ast.Attribute):
        # Field access. A leak only when the field is itself content.
        if node.attr in CONTENT_FIELDS:
            return _leaks(node.value, safe)
        return None

    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if key.value in CONTENT_FIELDS:
                return _leaks(node.value, safe)
            return None
        # A non-literal key could be anything.
        return _leaks(node.value, safe)

    for child in ast.iter_child_nodes(node):
        found = _leaks(child, safe)
        if found:
            return found
    return None


def offenders_in(source: str, filename: str) -> list[str]:
    """Every logger call in *source* that writes a raw request object."""
    found: list[str] = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        if func.value.id != 'logger':
            continue

        safe = _safely_wrapped(node)
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            leaked = _leaks(arg, safe)
            if leaked:
                found.append(f'{filename}:{node.lineno} logs {leaked!r} via logger.{func.attr}')
                break

    return found
