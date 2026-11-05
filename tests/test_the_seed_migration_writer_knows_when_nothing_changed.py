"""`make default-seed-migration` still runs, and at head finds nothing to write.

The head test (`test_an_existing_default_group_is_brought_to_what_igni_ships`)
fails when the seed moves without a migration; this keeps the tool that
writes that migration working, so the fix stays one command.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / 'alembic_tenant' / 'versions'


def test_at_head_it_writes_nothing_and_says_so():
    before = sorted(VERSIONS.glob('t*.py'))

    done = subprocess.run(
        [sys.executable, 'tools/write_default_seed_migration.py', '--slug', 'nothing_to_do'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 1, done.stderr
    assert 'no migration needed' in done.stdout
    assert sorted(VERSIONS.glob('t*.py')) == before


def test_a_slug_that_is_not_snake_case_is_refused():
    done = subprocess.run(
        [sys.executable, 'tools/write_default_seed_migration.py', '--slug', 'Not A Slug'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode != 0
    assert 'snake' in done.stderr or 'underscores' in done.stderr
