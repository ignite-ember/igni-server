"""What an extension asks for, and whether a new version asks for more.

`GrantsComparison.between` is the one place the rule is written
(`app.services.identity.grants_comparison`). Every grant has a case that
widens it and, where it can narrow, one that narrows it: an approved
service's version that asks for more is held for a super admin, and one
that asks for the same or less goes live — so a case missing here is a
way past the review.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.services.identity.extension_grants import ExtensionGrants
from app.services.identity.extension_manifest import Manifest, PluginSummary
from app.services.identity.grants_comparison import GrantKind, GrantsComparison

READS = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}

BASE: dict[str, Any] = {
    'id': 'acme.docs',
    'version': '1.0.0',
    'permissions': {
        'workspace': 'read',
        'workspacePaths': ['docs/**'],
        'egress': ['api.acme.example'],
        'secrets': ['ACME_TOKEN'],
        'tools': [{'name': 'acme.docs.search', 'annotations': READS}],
        'toolAudience': 'own-agents',
        'host': ['sessions.read'],
        'rpc': ['index'],
        'hooks': ['PreToolUse'],
        'service': ['messages'],
    },
    'contributes': {
        'pages': [{'kind': 'acme.docs'}],
        'commands': [{'command': '/docs'}],
        'menu': [{'label': 'Docs', 'command': '/docs'}],
        'indicators': [{'id': 'acme.docs.status'}],
        'settings': [{'key': 'acme.docs.space'}],
        'promptFragments': [{'anchor': 'DOCS'}],
        'prompt': {
            'overrides': [{'target': 'agent:explorer', 'sections': ['body'], 'from': 'x.md', 'when': 'docs-mode'}],
            'edits': [{'target': 'main', 'append': 'docs_tips', 'from': 'tips.md', 'when': 'docs-mode'}],
        },
    },
}
PLUGIN: dict[str, Any] = {
    'agents': [{'file': 'p/agents/writer.md', 'name': 'writer'}],
    'skills': [{'name': 'summarise'}],
}


def _grants(manifest: dict[str, Any], plugin: dict[str, Any] | None = None) -> ExtensionGrants:
    return ExtensionGrants.of(
        Manifest.model_validate(manifest), PluginSummary.model_validate(PLUGIN if plugin is None else plugin)
    )


def _changed(**at: Any) -> dict[str, Any]:
    """BASE with `permissions.<key>` or `contributes.<key>` replaced; `path='a.b'` style keys."""
    manifest = copy.deepcopy(BASE)
    for path, value in at.items():
        node = manifest
        *parents, leaf = path.split('__')
        for part in parents:
            node = node[part]
        node[leaf] = value
    return manifest


def _compare(candidate: dict[str, Any], plugin: dict[str, Any] | None = None):
    return GrantsComparison.between(_grants(BASE), _grants(candidate, plugin))


class TestTheSame:
    def test_the_same_version_again_is_not_wider(self):
        comparison = _compare(BASE)

        assert not comparison.wider
        assert comparison.added == []
        assert comparison.reason is None

    def test_what_is_not_a_grant_does_not_count(self):
        """Version, name, menu entries, indicators and settings are shown, but reach nothing."""
        manifest = _changed(
            version='2.0.0',
            name='Acme Docs, renamed',
            contributes__menu=[{'label': 'More', 'command': '/docs'}],
            contributes__indicators=[{'id': 'acme.docs.other'}],
            contributes__settings=[{'key': 'acme.docs.other'}],
        )

        assert not _compare(manifest).wider


WIDER: list[tuple[str, dict[str, Any], dict[str, Any] | None, GrantKind, str]] = [
    # (case, change, plugin, kind, item)
    ('a host', {'permissions__egress': ['api.acme.example', 'evil.example']}, None, GrantKind.EGRESS, 'evil.example'),
    (
        'a host on another port',
        {'permissions__egress': ['api.acme.example', 'api.acme.example:8443']},
        None,
        GrantKind.EGRESS,
        'api.acme.example:8443',
    ),
    ('a path', {'permissions__workspacePaths': ['docs/**', 'src/**']}, None, GrantKind.WORKSPACE_PATHS, 'src/**'),
    ('the whole project', {'permissions__workspacePaths': []}, None, GrantKind.WORKSPACE_PATHS, 'the whole project'),
    ('write access', {'permissions__workspace': 'write'}, None, GrantKind.WORKSPACE, 'write'),
    ('a secret', {'permissions__secrets': ['ACME_TOKEN', 'AWS_KEY']}, None, GrantKind.SECRET, 'AWS_KEY'),
    (
        'a tool',
        {
            'permissions__tools': [
                {'name': 'acme.docs.search', 'annotations': READS},
                {'name': 'acme.docs.delete', 'annotations': READS},
            ]
        },
        None,
        GrantKind.TOOL,
        'acme.docs.delete',
    ),
    (
        'a tool made destructive',
        {
            'permissions__tools': [
                {'name': 'acme.docs.search', 'annotations': {**READS, 'readOnlyHint': False, 'destructiveHint': True}}
            ]
        },
        None,
        GrantKind.TOOL_HINTS,
        'acme.docs.search: no longer read-only, destructive',
    ),
    (
        'a tool reaching the open world',
        {'permissions__tools': [{'name': 'acme.docs.search', 'annotations': {**READS, 'openWorldHint': True}}]},
        None,
        GrantKind.TOOL_HINTS,
        'acme.docs.search: reaches the open world',
    ),
    (
        'a tool no longer idempotent',
        {'permissions__tools': [{'name': 'acme.docs.search', 'annotations': {**READS, 'idempotentHint': False}}]},
        None,
        GrantKind.TOOL_HINTS,
        'acme.docs.search: no longer idempotent',
    ),
    ('tools for every agent', {'permissions__toolAudience': 'all-agents'}, None, GrantKind.TOOL_AUDIENCE, 'all-agents'),
    (
        'a host capability',
        {'permissions__host': ['sessions.read', 'sessions.post']},
        None,
        GrantKind.HOST,
        'sessions.post',
    ),
    ('an rpc method', {'permissions__rpc': ['index', 'wipe']}, None, GrantKind.RPC, 'wipe'),
    ('a hook', {'permissions__hooks': ['PreToolUse', 'UserPromptSubmit']}, None, GrantKind.HOOK, 'UserPromptSubmit'),
    ('a service grant', {'permissions__service': ['messages', 'chats']}, None, GrantKind.SERVICE, 'chats'),
    (
        'a prompt section',
        {
            'contributes__prompt__edits': [
                {'target': 'main', 'append': 'docs_tips', 'from': 'tips.md', 'when': 'docs-mode'},
                {'target': 'main', 'replace': 'rules', 'from': 'rules.md'},
            ]
        },
        None,
        GrantKind.PROMPT_EDIT,
        'main#rules (replace)',
    ),
    (
        'an inserted section',
        {
            'contributes__prompt__edits': [
                {'target': 'main', 'append': 'docs_tips', 'from': 'tips.md', 'when': 'docs-mode'},
                {'target': 'plan_mode', 'insert': 'docs_plan', 'before': 'rules', 'from': 'plan.md'},
            ]
        },
        None,
        GrantKind.PROMPT_EDIT,
        'plan_mode+docs_plan (insert)',
    ),
    (
        'an edit no longer conditional',
        {'contributes__prompt__edits': [{'target': 'main', 'append': 'docs_tips', 'from': 'tips.md'}]},
        None,
        GrantKind.PROMPT_EDIT,
        'main+docs_tips, always',
    ),
    (
        'a prompt override',
        {
            'contributes__prompt__overrides': [
                {'target': 'agent:explorer', 'sections': ['body'], 'from': 'x.md', 'when': 'docs-mode'},
                {'target': 'plan_mode', 'sections': ['body'], 'from': 'plan.md'},
            ]
        },
        None,
        GrantKind.PROMPT_OVERRIDE,
        'plan_mode',
    ),
    (
        'an override made exclusive',
        {
            'contributes__prompt__overrides': [
                {
                    'target': 'agent:explorer',
                    'sections': ['body'],
                    'from': 'x.md',
                    'when': 'docs-mode',
                    'exclusive': True,
                }
            ]
        },
        None,
        GrantKind.PROMPT_OVERRIDE,
        'agent:explorer becomes exclusive',
    ),
    (
        'an override with another section',
        {
            'contributes__prompt__overrides': [
                {'target': 'agent:explorer', 'sections': ['body', 'tools'], 'from': 'x.md', 'when': 'docs-mode'}
            ]
        },
        None,
        GrantKind.PROMPT_EDIT,
        'agent:explorer#tools',
    ),
    (
        'an override no longer conditional',
        {'contributes__prompt__overrides': [{'target': 'agent:explorer', 'sections': ['body'], 'from': 'x.md'}]},
        None,
        GrantKind.PROMPT_OVERRIDE,
        'agent:explorer, always',
    ),
    (
        'an anchor',
        {'contributes__promptFragments': [{'anchor': 'DOCS'}, {'anchor': 'RULES'}]},
        None,
        GrantKind.ANCHOR,
        'RULES',
    ),
    (
        'a command',
        {'contributes__commands': [{'command': '/docs'}, {'command': '/publish'}]},
        None,
        GrantKind.COMMAND,
        '/publish',
    ),
    (
        'a page',
        {'contributes__pages': [{'kind': 'acme.docs'}, {'kind': 'acme.admin'}]},
        None,
        GrantKind.PAGE,
        'acme.admin',
    ),
    (
        'an agent',
        {},
        {'agents': [*PLUGIN['agents'], {'file': 'p/agents/admin.md', 'name': 'admin'}], 'skills': PLUGIN['skills']},
        GrantKind.AGENT,
        'admin',
    ),
    (
        'a skill',
        {},
        {'agents': PLUGIN['agents'], 'skills': [*PLUGIN['skills'], {'name': 'deploy'}]},
        GrantKind.SKILL,
        'deploy',
    ),
]


class TestWider:
    @pytest.mark.parametrize('case,change,plugin,kind,item', WIDER, ids=[w[0] for w in WIDER])
    def test_asking_for_more(self, case, change, plugin, kind, item):
        comparison = _compare(_changed(**change), plugin)

        assert comparison.wider, case
        assert [(g.kind, g.item) for g in comparison.added] == [(kind, item)]
        assert comparison.reason is not None and item in comparison.reason

    def test_every_kind_of_grant_has_a_case(self):
        covered = {kind for *_, kind, _ in WIDER}
        assert set(GrantKind) - covered == set()

    def test_what_it_widened_from_is_said(self):
        comparison = _compare(_changed(permissions__workspace='write'))

        assert comparison.added[0].was == 'read'
        assert comparison.added[0].text == "Access to people's projects: write (was read)"

    def test_several_at_once_are_all_listed(self):
        comparison = _compare(
            _changed(permissions__egress=['api.acme.example', 'a.example', 'b.example'], permissions__secrets=[])
        )

        assert [g.item for g in comparison.added] == ['a.example', 'b.example']

    def test_from_no_workspace_to_some(self):
        before = _grants(_changed(permissions__workspace='none', permissions__workspacePaths=[]))
        after = _grants(_changed(permissions__workspace='read', permissions__workspacePaths=['src/**']))

        comparison = GrantsComparison.between(before, after)

        assert [(g.kind, g.item) for g in comparison.added] == [(GrantKind.WORKSPACE, 'read')]

    def test_an_unknown_workspace_level_counts_as_wider(self):
        assert _compare(_changed(permissions__workspace='admin')).wider


NARROWER: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = [
    ('a host fewer', {'permissions__egress': []}, None),
    ('read-only to none', {'permissions__workspace': 'none'}, None),
    ('a secret fewer', {'permissions__secrets': []}, None),
    ('a tool fewer', {'permissions__tools': []}, None),
    (
        'a tool made safer',
        {'permissions__tools': [{'name': 'acme.docs.search', 'annotations': READS}]},
        None,
    ),
    ('a hook fewer', {'permissions__hooks': []}, None),
    ('a host capability fewer', {'permissions__host': []}, None),
    ('a service grant fewer', {'permissions__service': []}, None),
    ('a prompt edit fewer', {'contributes__prompt__edits': []}, None),
    ('a prompt override fewer', {'contributes__prompt__overrides': []}, None),
    ('a command fewer', {'contributes__commands': []}, None),
    ('a page fewer', {'contributes__pages': []}, None),
    ('an agent and a skill fewer', {}, {'agents': [], 'skills': []}),
]


class TestTheSameOrNarrower:
    def test_a_narrower_glob_is_still_a_new_path(self):
        """Conservative on purpose: igni does not reason about one glob covering another."""
        assert _compare(_changed(permissions__workspacePaths=['docs/api/**'])).wider

    @pytest.mark.parametrize('case,change,plugin', NARROWER, ids=[n[0] for n in NARROWER])
    def test_asking_for_less(self, case, change, plugin):
        comparison = _compare(_changed(**change), plugin)

        assert not comparison.wider, (case, comparison.added)

    def test_a_conditional_edit_that_was_always_is_narrower(self):
        before = _grants(_changed(contributes__prompt__edits=[{'target': 'main', 'append': 'docs_tips', 'from': 't'}]))

        assert not GrantsComparison.between(before, _grants(BASE)).wider

    def test_a_path_fewer(self):
        before = _grants(_changed(permissions__workspacePaths=['docs/**', 'src/**']))

        assert not GrantsComparison.between(before, _grants(BASE)).wider

    def test_tools_for_own_agents_only_is_narrower(self):
        before = _grants(_changed(permissions__toolAudience='all-agents'))

        assert not GrantsComparison.between(before, _grants(BASE)).wider

    def test_paths_where_the_whole_project_was_approved_is_narrower(self):
        before = _grants(_changed(permissions__workspacePaths=[]))

        assert not GrantsComparison.between(before, _grants(BASE)).wider
