"""A backup run covers every organisation, one archive each.

Every organisation has its own logical database (`app.db.sql.tenancy`),
and the backup script used to dump only `POSTGRES_DB` — the shared plane.
Every organisation's groups, usage, notifications and audit trail were
in no backup at all, and every check the script ran still passed,
because the shared plane on its own is a perfectly valid archive.

The decision: one archive per organisation plus one for the shared plane,
per run. Isolation holds in the backups (one organisation's archive never
contains another's rows), one organisation can be restored without
rolling back the others, and a deleted organisation's archive can be
handed over, kept or destroyed on its own schedule.

These run the shipped script itself, with `psql`, `pg_dump` and
`pg_restore` replaced on `PATH` by stand-ins that record what they were
asked for. `tools/restore-drill.sh` does the same against a real Postgres
and restores the result; this is the part that runs on every commit.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import time

import pytest

from app.db.sql.models.tenant import ACTIVE, DETACHED
from app.db.sql.tenant_names import TenantNames

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / 'containers/backup.sh'

_ORG_A = '22222222-2222-2222-2222-222222222222'
_ORG_B = '66666666-6666-6666-6666-666666666666'

#: `psql` answers the tenants query from a file, or fails if told to.
_PSQL = """#!/bin/sh
case "$*" in
  *"FROM tenants ORDER BY number"*)
    [ -z "${FAKE_PSQL_FAILS:-}" ] || { echo 'psql: relation "tenants" does not exist' >&2; exit 1; }
    cat "$FAKE_TENANTS" ;;
  *) cat >/dev/null ;;
esac
"""

#: `pg_dump` records each database and writes an "archive" naming it.
_PG_DUMP = """#!/bin/sh
for a in "$@"; do
  case "$a" in --dbname=*) db="${a#--dbname=}" ;; --file=*) file="${a#--file=}" ;; esac
done
echo "$db" >>"$FAKE_DUMPED"
if grep -qx "$db" "$FAKE_BROKEN" 2>/dev/null; then echo "pg_dump: database \\"$db\\" does not exist" >&2; exit 1; fi
printf 'archive of %s\\n' "$db" >"$file"
"""

#: `pg_restore --list` reads the "archive" back as a table of contents.
_PG_RESTORE = """#!/bin/sh
db="$(sed -n 's/^archive of //p' "$2")"
[ -n "$db" ] || exit 1
echo "; Archive created from $db"
echo "215; 1259 16390 TABLE public audit_events igni"
echo "3301; 1259 16400 INDEX public idx_audit_events_org igni"
"""


class _Backups:
    """The shipped script, a backup directory, and a fake Postgres."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.dir = tmp_path / 'backups'
        self.dir.mkdir()
        self.bin = tmp_path / 'bin'
        self.bin.mkdir()
        for name, body in (('psql', _PSQL), ('pg_dump', _PG_DUMP), ('pg_restore', _PG_RESTORE)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.tenants = tmp_path / 'tenants.tsv'
        self.tenants.write_text('')
        self.dumped = tmp_path / 'dumped.log'
        self.broken = tmp_path / 'broken.txt'
        self.broken.write_text('')

    def register(self, *rows: tuple[str, int, str, str]) -> None:
        """Rows of `tenants`: org_id, number, state, database."""
        self.tenants.write_text(''.join('\t'.join(map(str, row)) + '\n' for row in rows))

    def break_database(self, database: str) -> None:
        self.broken.write_text(database + '\n')

    def run(self, **extra: str) -> subprocess.CompletedProcess[str]:
        self.dumped.write_text('')
        env = {
            'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
            'POSTGRES_HOST': 'fake',
            'POSTGRES_USER': 'igni',
            'POSTGRES_DB': 'igni',
            'IGNI_BACKUP_DIR': str(self.dir),
            'FAKE_TENANTS': str(self.tenants),
            'FAKE_DUMPED': str(self.dumped),
            'FAKE_BROKEN': str(self.broken),
            **extra,
        }
        return subprocess.run(
            ['/bin/sh', str(_SCRIPT), 'once'], env=env, capture_output=True, text=True, timeout=60, check=False
        )

    def runs(self) -> list[pathlib.Path]:
        """Complete runs. A `.part` directory is not one."""
        return sorted(p for p in self.dir.glob('igni-*') if p.is_dir() and not p.name.endswith('.part'))

    def partial(self) -> list[pathlib.Path]:
        return sorted(self.dir.glob('igni-*.part'))

    def asked_for(self) -> list[str]:
        return self.dumped.read_text().split()


@pytest.fixture
def backups(tmp_path: pathlib.Path) -> _Backups:
    return _Backups(tmp_path)


class TestEveryOrganisationIsBackedUp:
    def test_one_archive_for_the_shared_plane_and_one_per_organisation(self, backups):
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), (_ORG_B, 2, ACTIVE, 'igni_t2'))

        result = backups.run()

        assert result.returncode == 0, result.stdout + result.stderr
        assert backups.asked_for() == ['igni', 'igni_t1', 'igni_t2'], 'the shared plane first, then every organisation'
        [run] = backups.runs()
        assert sorted(p.name for p in run.iterdir()) == sorted(
            ['MANIFEST', 'shared-igni.dump', f'org-{_ORG_A}-igni_t1.dump', f'org-{_ORG_B}-igni_t2.dump']
        )

    def test_each_archive_holds_its_own_database_and_no_other(self, backups):
        """Isolation, in the backups as in the database."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), (_ORG_B, 2, ACTIVE, 'igni_t2'))

        assert backups.run().returncode == 0
        [run] = backups.runs()

        assert (run / f'org-{_ORG_A}-igni_t1.dump').read_text() == 'archive of igni_t1\n'
        assert (run / f'org-{_ORG_B}-igni_t2.dump').read_text() == 'archive of igni_t2\n'
        assert (run / 'shared-igni.dump').read_text() == 'archive of igni\n'

    def test_a_deleted_organisation_is_still_backed_up(self, backups):
        """Detached means its data is kept. Kept data is backed up."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), (_ORG_B, 2, DETACHED, 'igni_t2'))

        assert backups.run().returncode == 0
        [run] = backups.runs()

        assert 'igni_t2' in backups.asked_for()
        assert f'org\t{_ORG_B}\t2\t{DETACHED}\tigni_t2\torg-{_ORG_B}-igni_t2.dump' in (run / 'MANIFEST').read_text()

    def test_the_manifest_says_which_archive_is_whose(self, backups):
        """What a restore of one organisation starts from."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'))

        assert backups.run().returncode == 0
        [run] = backups.runs()
        lines = [line.split('\t') for line in (run / 'MANIFEST').read_text().splitlines() if not line.startswith('#')]

        assert lines == [
            ['shared', '-', '-', '-', 'igni', 'shared-igni.dump'],
            ['org', _ORG_A, '1', ACTIVE, 'igni_t1', f'org-{_ORG_A}-igni_t1.dump'],
        ]

    def test_the_template_is_never_dumped(self, backups):
        """It holds no data and is rebuilt from migrations. Even a registry
        row pointing at it — which the application never writes — does not
        get it dumped, and the log says so."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), ('stray', 2, ACTIVE, 'igni_tenant_template'))

        result = backups.run()

        assert result.returncode == 0, result.stdout + result.stderr
        assert 'igni_tenant_template' not in backups.asked_for()
        assert not list(backups.dir.rglob('*tenant_template*'))
        assert 'points at the template igni_tenant_template' in result.stdout

    def test_an_org_id_cannot_put_a_path_in_a_file_name(self, backups):
        """The name comes out of a database row."""
        backups.register(('../../etc/x', 1, ACTIVE, 'igni_t1'))

        assert backups.run().returncode == 0
        [run] = backups.runs()

        assert (run / 'org-.._.._etc_x-igni_t1.dump').exists(), sorted(p.name for p in run.iterdir())


class TestOneOrganisationFailingFailsTheRun:
    """Nine organisations out of ten is a run that lost the tenth."""

    def test_the_run_fails_and_does_not_count(self, backups):
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), (_ORG_B, 2, ACTIVE, 'igni_t2'))
        backups.break_database('igni_t1')
        (backups.dir / '.last-success').write_text('before\n')

        result = backups.run()

        assert result.returncode != 0
        assert backups.runs() == [], 'a run missing an organisation was promoted to a complete run'
        assert len(backups.partial()) == 1, 'the incomplete run is not left for inspection'
        assert (backups.dir / '.last-success').read_text() == 'before\n', 'the healthcheck would stay green'
        assert f'organisation {_ORG_A} (igni_t1, active) was not backed up' in result.stdout

    def test_the_rest_are_still_attempted(self, backups):
        """So the log names every organisation that failed, not only the first."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'), (_ORG_B, 2, ACTIVE, 'igni_t2'))
        backups.break_database('igni_t1')

        backups.run()

        assert 'igni_t2' in backups.asked_for()

    def test_a_registry_that_cannot_be_read_fails_the_run(self, backups):
        """Not a run with no organisations in it."""
        result = backups.run(FAKE_PSQL_FAILS='1')

        assert result.returncode != 0
        assert backups.runs() == []
        assert not (backups.dir / '.last-success').exists()

    def test_nothing_is_pruned_after_a_failed_run(self, backups):
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'))
        old_run = backups.dir / 'igni-20200101T030000Z'
        old_run.mkdir()
        (old_run / 'shared-igni.dump').write_text('archive of igni\n')
        legacy = backups.dir / 'igni-igni-20200101T030000Z.dump'
        legacy.write_text('archive of igni\n')
        month_ago = time.time() - 30 * 86400
        for path in (old_run, legacy):
            os.utime(path, (month_ago, month_ago))

        backups.break_database('igni_t1')
        assert backups.run().returncode != 0
        assert old_run.exists() and legacy.exists(), 'a failed run pruned the last good backups'

        backups.break_database('nothing')
        time.sleep(1.1)  # run directories are named to the second
        result = backups.run()
        assert result.returncode == 0, result.stdout + result.stderr
        assert not old_run.exists(), 'a run older than the retention window survived a good run'
        assert not legacy.exists(), 'the single-file dumps earlier versions wrote are never pruned'


class TestTwoRunsInOneSecond:
    def test_the_second_refuses_rather_than_landing_inside_the_first(self, backups):
        """Run directories are named to the second. A rename onto an
        existing directory moves the new run *into* the old one."""
        backups.register((_ORG_A, 1, ACTIVE, 'igni_t1'))
        assert backups.run().returncode == 0
        [run] = backups.runs()
        stamp = run.name.removeprefix('igni-')
        assert stamp

        # The same stamp again, whatever the clock now says.
        fake_date = backups.bin / 'date'
        fake_date.write_text(f'#!/bin/sh\ncase "$*" in *%Y%m%dT*) echo {stamp} ;; *) exec /bin/date "$@" ;; esac\n')
        fake_date.chmod(0o755)
        result = backups.run()

        assert result.returncode != 0
        assert 'already exists' in result.stdout
        assert sorted(p.name for p in run.iterdir()) == sorted(
            ['MANIFEST', 'shared-igni.dump', f'org-{_ORG_A}-igni_t1.dump']
        ), 'the second run landed inside the first'


class TestTheScriptAgreesWithTheApplication:
    """Read from the script, against the application's own names."""

    @pytest.fixture
    def script(self) -> str:
        return _SCRIPT.read_text()

    def test_it_enumerates_every_organisation_in_tenants(self, script):
        """Every state: a filter on `active` would drop deleted
        organisations' kept data out of the backups."""
        match = re.search(r"SELECT org_id, number, state, database FROM tenants([^']*)'", script)

        assert match, 'the script does not read the organisations from tenants'
        assert 'WHERE' not in match.group(1).upper(), f'the tenants query is filtered: {match.group(0)}'

    def test_the_template_it_excludes_is_the_one_the_application_makes(self, script):
        """The suffix, from `TenantNames` rather than a second copy."""
        names = TenantNames()
        suffix = names.template().removeprefix(names.base_name())

        assert f'TEMPLATE_DB="${{POSTGRES_DB}}{suffix}"' in script, suffix
        assert 'if [ "$database" = "$TEMPLATE_DB" ]; then' in script

    def test_organisation_databases_are_named_the_way_it_looks_for_them(self, script):
        """The unregistered-database warning looks for `<db>_t<n>`."""
        names = TenantNames()
        assert names.name(7) == f'{names.base_name()}_t7'
        assert "= :'db' || '_t'" in script
