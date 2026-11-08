"""A new organisation's Default group holds the agents and skills igni ships today.

The seed is a copy of the CLI's `bundled_agents` and `bundled_skills`,
made by `make refresh-default-seed`. It had drifted: every agent
differed, `plan_researcher` named tools (`Read`, `Grep`, `Glob`, `LS`)
igni no longer has, so a new member was warned about an agent nobody in
their organisation had touched, and the prompts still told the model to
look for `ember.md`. The refresh pointed at `../ember-code`, the CLI's
old name; it points at `../igni` now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.portal.default_group_seed import BUNDLED_SEED

_ROOT = Path(__file__).resolve().parents[1]
#: The CLI checkout beside this one, where `make refresh-default-seed` reads.
_IGNI = _ROOT.parent / 'igni' / 'src' / 'ember_code'


def _seeded(kind: str) -> dict[str, str]:
    return {entry.entry_name: entry.content for entry in BUNDLED_SEED.entries() if entry.kind == kind}


def test_the_refresh_reads_the_igni_checkout():
    assert 'IGNI_CLI ?= ../igni\n' in (_ROOT / 'Makefile').read_text()


def test_nothing_seeded_uses_igni_s_old_name():
    stale = sorted(
        name
        for kind in ('agents', 'skills')
        for name, content in _seeded(kind).items()
        if 'ember.md' in content or 'ember-code' in content
    )
    assert stale == []


@pytest.mark.skipif(not _IGNI.is_dir(), reason='no igni checkout beside this one — skipped, not passed')
def test_the_agents_are_igni_s():
    shipped = {path.stem: path.read_text() for path in (_IGNI / 'bundled_agents').glob('*.md')}

    assert _seeded('agents') == shipped


@pytest.mark.skipif(not _IGNI.is_dir(), reason='no igni checkout beside this one — skipped, not passed')
def test_the_skills_are_igni_s():
    shipped = {path.parent.name: path.read_text() for path in (_IGNI / 'bundled_skills').glob('*/SKILL.md')}

    assert _seeded('skills') == shipped
