"""Checks a value against a JSON Schema (draft 2020-12), for the keywords igni's contracts use.

igni-sdk's `contracts/` are the single source of the shapes other repos
read; a copy of each lives in `tests/fixtures/contracts`. The suite has no
JSON Schema library, and a contract test should not need a dependency the
server does not, so this covers what the contracts say and refuses
anything else rather than passing it unread: `$ref` (to `#/$defs/…`),
`type`, `enum`, `const`, `oneOf`, `anyOf`, `allOf`, `if`/`then`/`else`,
`not`, `required`, `properties`, `additionalProperties`, `pattern` and
`minLength`, beside the annotations (`title`, `description`, `examples`…).
"""

from __future__ import annotations

import re
from typing import Any

_ANNOTATIONS = frozenset({'$schema', '$id', '$defs', 'title', 'description', 'examples', 'default'})
_UNDERSTOOD = frozenset(
    {
        '$ref',
        'type',
        'enum',
        'const',
        'oneOf',
        'anyOf',
        'allOf',
        'if',
        'then',
        'else',
        'not',
        'required',
        'properties',
        'additionalProperties',
        'pattern',
        'minLength',
    }
)
_TYPES: dict[str, tuple[type, ...]] = {
    'string': (str,),
    'object': (dict,),
    'array': (list,),
    'null': (type(None),),
    'boolean': (bool,),
    'integer': (int,),
    'number': (int, float),
}


class JsonSchema:
    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    def problems(self, value: object, schema: dict[str, Any] | None = None, where: str = '$') -> list[str]:
        """Every way *value* breaks *schema* (the whole document when None); empty when it holds."""
        schema = self._document if schema is None else schema
        unknown = set(schema) - _UNDERSTOOD - _ANNOTATIONS
        if unknown:
            raise ValueError(f'{where}: this checker does not read {sorted(unknown)}')
        found: list[str] = []
        if '$ref' in schema:
            found += self.problems(value, self._resolve(schema['$ref']), where)
        if 'type' in schema:
            kinds = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
            if not any(self._is(value, kind) for kind in kinds):
                found.append(f'{where}: {value!r} is not {kinds}')
        if 'enum' in schema and not any(_same(value, option) for option in schema['enum']):
            found.append(f'{where}: {value!r} is not one of {schema["enum"]}')
        if 'const' in schema and not _same(value, schema['const']):
            found.append(f'{where}: {value!r} is not {schema["const"]!r}')
        if 'oneOf' in schema:
            holding = [branch for branch in schema['oneOf'] if not self.problems(value, branch, where)]
            if len(holding) != 1:
                found.append(f'{where}: {value!r} holds for {len(holding)} of oneOf, not exactly one')
        if 'anyOf' in schema and all(self.problems(value, branch, where) for branch in schema['anyOf']):
            found.append(f'{where}: {value!r} holds for none of anyOf')
        for branch in schema.get('allOf', []):
            found += self.problems(value, branch, where)
        if 'if' in schema:
            follows = schema.get('then') if not self.problems(value, schema['if'], where) else schema.get('else')
            if follows is not None:
                found += self.problems(value, follows, where)
        if 'not' in schema and not self.problems(value, schema['not'], where):
            found.append(f'{where}: {value!r} holds for what it must not')
        if isinstance(value, str):
            if 'pattern' in schema and re.search(schema['pattern'], value) is None:
                found.append(f'{where}: {value!r} does not match {schema["pattern"]}')
            if len(value) < schema.get('minLength', 0):
                found.append(f'{where}: {value!r} is shorter than {schema["minLength"]}')
        if isinstance(value, dict):
            found += self._object(value, schema, where)
        return found

    def defined(self, name: str) -> JsonSchema:
        """The same checker, rooted at one of the document's `$defs`."""
        return _Rooted(self._document, self._document['$defs'][name])

    def _object(self, value: dict[str, Any], schema: dict[str, Any], where: str) -> list[str]:
        found = [f'{where}: {name} is missing' for name in schema.get('required', []) if name not in value]
        properties = schema.get('properties', {})
        for name, inner in properties.items():
            if name in value:
                found += self.problems(value[name], inner, f'{where}.{name}')
        if schema.get('additionalProperties') is False:
            found += [f'{where}: {name} is not allowed' for name in value if name not in properties]
        return found

    def _resolve(self, ref: str) -> dict[str, Any]:
        if not ref.startswith('#/$defs/'):
            raise ValueError(f'this checker resolves only #/$defs/ references, not {ref}')
        return dict(self._document['$defs'][ref.removeprefix('#/$defs/')])

    @staticmethod
    def _is(value: object, kind: str) -> bool:
        if isinstance(value, bool) and kind in ('integer', 'number'):
            return False
        return isinstance(value, _TYPES[kind])


class _Rooted(JsonSchema):
    def __init__(self, document: dict[str, Any], root: dict[str, Any]) -> None:
        super().__init__(document)
        self._root = root

    def problems(self, value: object, schema: dict[str, Any] | None = None, where: str = '$') -> list[str]:
        return super().problems(value, self._root if schema is None else schema, where)


def _same(value: object, expected: object) -> bool:
    """JSON equality: `true` is not `1`."""
    return type(value) is type(expected) and value == expected
