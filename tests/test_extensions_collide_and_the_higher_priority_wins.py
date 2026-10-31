"""igni server reads an extension's impact and settles its collisions as igni does.

The rule — the higher in the super admin's priority order wins — is stated
twice: here, where the Services page shows who wins, and in igni, where
every machine applies it. Both run the same cases out
of `tests/fixtures/extension_impact_cases.json`. This file fails when the
copy beside a sibling igni checkout differs.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest

from app.services.identity.impact import (
    DECIDING_HOOKS,
    ExtensionImpact,
    HOOK_EVENTS,
    IGNI_COMMANDS_FILE,
    IgniCommands,
    ImpactRefused,
    ImpactResolver,
    PROMPT_TARGETS_FILE,
    PromptTargets,
)

FIXTURE = Path(__file__).parent / 'fixtures' / 'extension_impact_cases.json'
CASES: dict[str, Any] = json.loads(FIXTURE.read_text())
IGNI = Path(__file__).resolve().parents[2] / 'ember-code'


def _resolver(case: dict[str, Any]) -> ImpactResolver:
    return ImpactResolver(
        [ExtensionImpact.of(m) for m in case['manifests']], case['priority'], reserved=case.get('reserved', [])
    )


def test_an_extensions_impact_is_read_off_its_manifest():
    expected = dict(CASES['impact']['expected'])
    claims = expected.pop('claims')
    items = expected.pop('items')
    impact = ExtensionImpact.of(CASES['impact']['manifest'])
    assert impact.model_dump(mode='json') == expected
    assert impact.claims() == claims
    assert impact.items() == items


@pytest.mark.parametrize('case', CASES['cases'], ids=[c['name'] for c in CASES['cases']])
def test_every_collision_goes_to_the_higher_priority(case):
    outcome = _resolver(case).resolve()
    assert [c.model_dump() for c in outcome.collisions] == case['collisions']
    assert {ext: [o.claim for o in entries] for ext, entries in outcome.off.items()} == case['off']
    assert {ext: [o.claim for o in entries] for ext, entries in outcome.dropped.items()} == case['dropped']


def test_a_loss_by_priority_names_who_won():
    case = next(c for c in CASES['cases'] if c['name'].startswith('each collision is settled'))
    reasons = [o.reason for o in _resolver(case).resolve().off['acme.a']]
    assert reasons and all(r.startswith('lower priority than acme.b') for r in reasons)


def test_hooks_without_a_process_are_refused():
    manifest = {
        'id': 'acme.a',
        'version': '1.0.0',
        'endpoint': {'url': 'https://a.example.com/mcp'},
        'permissions': {'hooks': ['PreToolUse']},
    }
    with pytest.raises(ImpactRefused, match='needs a process igni starts'):
        ExtensionImpact.of(manifest)


def test_an_event_igni_does_not_have_is_refused():
    manifest = {'id': 'acme.a', 'version': '1.0.0', 'permissions': {'hooks': ['tools.calling']}}
    with pytest.raises(ImpactRefused, match='not a hook event of igni'):
        ExtensionImpact.of(manifest)


def test_the_hook_events_are_igni_s():
    """The list here is igni's HookEvent, and the deciding five its DECIDING."""
    igni = Path(__file__).resolve().parents[2] / 'ember-code' / 'src' / 'ember_code' / 'core' / 'hooks'
    if not igni.is_dir():
        pytest.skip('igni is not checked out beside igni server')
    events = set(re.findall(r'= "([A-Za-z]+)"', (igni / 'events.py').read_text()))
    assert events == set(HOOK_EVENTS)
    deciding = set(re.findall(r'event=HookEvent\.([A-Z_]+)', (igni / 'extension_gate.py').read_text()))
    assert {name.title().replace('_', '') for name in deciding} == set(DECIDING_HOOKS)


def test_a_malformed_prompt_contribution_is_refused():
    manifest = {'id': 'acme.a', 'version': '1.0.0', 'contributes': {'prompt': {'overrides': [{'target': 'system'}]}}}
    with pytest.raises(ImpactRefused):
        ExtensionImpact.of(manifest)


def test_a_prompt_key_igni_does_not_have_is_refused():
    """The old spelling, or a typo: igni would refuse it at install, so it is refused at upload."""
    manifest = {'id': 'acme.a', 'version': '1.0.0', 'contributes': {'prompt': {'slots': []}}}
    with pytest.raises(ImpactRefused):
        ExtensionImpact.of(manifest)


def test_an_edit_is_exactly_one_change():
    edit = {'target': 'main', 'replace': 'workflow', 'append': 'x', 'source': 'file', 'from': 'x.md'}
    manifest = {'id': 'acme.a', 'version': '1.0.0', 'contributes': {'prompt': {'edits': [edit]}}}
    with pytest.raises(ImpactRefused):
        ExtensionImpact.of(manifest)


def test_overriding_and_editing_one_prompt_is_refused():
    override = {'target': 'main', 'sections': ['identity'], 'source': 'file', 'from': 'x.md'}
    edit = {'target': 'main', 'append': 'x', 'source': 'file', 'from': 'y.md'}
    manifest = {
        'id': 'acme.a',
        'version': '1.0.0',
        'contributes': {'prompt': {'overrides': [override], 'edits': [edit]}},
    }
    with pytest.raises(ImpactRefused, match='both overrides and edits'):
        ExtensionImpact.of(manifest)


def test_a_target_this_igni_does_not_have_is_named():
    """igni's own prompts are checked; a plugin's agent is not igni's to know."""
    manifest = {
        'id': 'acme.a',
        'version': '1.0.0',
        'contributes': {
            'prompt': {
                'edits': [
                    {'target': 'main', 'replace': 'nonesuch', 'source': 'file', 'from': 'x.md'},
                    {'target': 'main', 'insert': 'mine', 'after': 'safety', 'source': 'file', 'from': 'y.md'},
                    {'target': 'agent:explorer', 'replace': 'frontmatter', 'source': 'file', 'from': 'z.md'},
                    {'target': 'agent:acme.a:helper', 'append': 'x', 'source': 'file', 'from': 'w.md'},
                ],
                'overrides': [{'target': 'agent:nobody', 'sections': ['a'], 'source': 'file', 'from': 'v.md'}],
            }
        },
    }
    assert ExtensionImpact.of(manifest).unknown_targets(PromptTargets.igni()) == ['main#nonesuch']


@pytest.mark.skipif(not (IGNI / 'tests' / 'fixtures').is_dir(), reason='no igni checkout beside this one')
def test_igni_s_copy_of_the_cases_is_the_same():
    assert json.loads((IGNI / 'tests' / 'fixtures' / 'extension_impact_cases.json').read_text()) == CASES


@pytest.mark.skipif(not (IGNI / 'docs').is_dir(), reason='no igni checkout beside this one')
def test_igni_s_commands_are_the_ones_checked_against():
    assert json.loads((IGNI / 'docs' / 'igni-commands.json').read_text()) == json.loads(IGNI_COMMANDS_FILE.read_text())


def test_an_extension_declaring_one_of_igni_s_commands_clashes_with_igni():
    manifest = {
        'id': 'acme.tidy',
        'version': '1.0.0',
        'contributes': {'commands': [{'command': '/clear', 'desc': 'x'}, {'command': '/tidy', 'desc': 'y'}]},
    }
    assert IgniCommands.igni().clashes(ExtensionImpact.of(manifest)) == ['/clear']


@pytest.mark.skipif(not (IGNI / 'docs').is_dir(), reason='no igni checkout beside this one')
def test_igni_s_prompt_targets_are_the_ones_checked_against():
    assert json.loads((IGNI / 'docs' / 'prompt-targets.json').read_text()) == json.loads(
        PROMPT_TARGETS_FILE.read_text()
    )
