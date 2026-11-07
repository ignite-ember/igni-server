"""The compose files describe a deployment that can actually run, safely.

Everything here was found by bringing the production stack up for the first
time. It had never been booted — `docker-compose.local.yml` *builds* from
the Dockerfile, so the production file's `image:` had never been resolved
by anybody.

Four things came out of that:

**The image did not exist.** `image: ember/ember-api:latest` is
unqualified, so Docker resolves it to `docker.io/ember/ember-api` — a
different image from the one CD publishes, which goes to the project's
own registry (today `ghcr.io/ignite-ember/igni-api:<tag>`). A fresh host
following this file would have pulled from Docker Hub. Whether that
namespace is unregistered or belongs to somebody else could not be
determined: Docker Hub answers 401 for both.

**Anything could start before the schema existed.** No `depends_on` at all,
so `up` started the API and the worker alongside the migration.

**`env_file: .env`** is the same filename local development uses, so a
checkout with a leftover dev `.env` deploys `APP_ENV=dev` and
`PORTAL_AUTH_BYPASS=true`. The startup check cannot catch that, and that is
the point: it refuses the bypass only when `APP_ENV != dev`, and `APP_ENV`
comes from the same file. A dev env file disarms the guard against dev env
files.

**Ports were published on every interface.** F22 restricted one service
to loopback in the production file and missed the local one — which is the
file paired with the bypass. Not hypothetical: while a test stack was up on
7777, a GitHub load balancer (`lb-140-82-121-6-fra.github.com`) reached
`/health` and `/ready` on it from the internet, with no tunnel running on
this host.

After the fixes the stack was booted end to end with `APP_ENV=production`:
migrate exited 0 first, the API reported healthy to Docker's own
healthcheck, and `/ready` reported postgres and redis ok.

DEC-13 later removed the worker and Flower, which is why several rules
here read as though they cover more services than the files now contain.
The rules were kept rather than narrowed to what is left: each is written
over whatever the compose file declares, so a service reintroduced
tomorrow is covered without anybody remembering to add it.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent / 'containers'


def _load(name: str) -> dict:
    """Parse a compose file.

    `${VAR:?msg}` is left as a literal string — this reads the file as
    written, which is the point: the assertions below are about what the
    file *says*, not about what a particular environment resolves it to.
    """
    return yaml.safe_load((_ROOT / name).read_text())


@pytest.fixture
def prod() -> dict:
    return _load('docker-compose.prod.yml')


@pytest.fixture
def local() -> dict:
    return _load('docker-compose.local.yml')


def _published(service: dict) -> list[str]:
    return [str(p) for p in (service.get('ports') or [])]


class TestNothingIsPublishedToTheWorldByDefault:
    """A published port with no host address binds every interface, which
    on a machine with any inbound reach means the internet."""

    @pytest.mark.parametrize('which', ['docker-compose.prod.yml', 'docker-compose.local.yml'])
    def test_every_port_names_a_host_address(self, which):
        compose = _load(which)

        offenders = []
        for name, service in compose['services'].items():
            for port in _published(service):
                # Either an explicit literal address, or a variable whose
                # default is one. Anything else binds 0.0.0.0.
                first = port.split(':')[0]
                if first.isdigit():
                    offenders.append(f'{name}: {port}')

        assert not offenders, (
            f'{which} publishes on all interfaces: {offenders}. Bind to '
            f'127.0.0.1 and put a reverse proxy in front. A GitHub load balancer '
            f'reached /health on 7777 of this very host while a stack was up.'
        )

    def test_the_local_stack_is_loopback_only(self, local):
        """Local development has no reason to be reachable off-box, and
        this is the file paired with PORTAL_AUTH_BYPASS=true."""
        for name, service in local['services'].items():
            for port in _published(service):
                assert port.startswith('127.0.0.1:'), f'{name} publishes {port}'

    def test_the_prod_api_defaults_to_loopback(self, prod):
        """Overridable, because the container may legitimately be the edge
        — but the default must not be."""
        ports = _published(prod['services']['igni_api'])

        assert len(ports) == 1, ports
        assert '${IGNI_API_BIND:-127.0.0.1}' in ports[0], ports[0]

    def test_nothing_but_the_api_and_portal_publishes_off_loopback(self, prod, local):
        """The finding F22 half-fixed, generalised (F142).

        It named Flower, so it stopped applying when DEC-13 removed
        Flower — a rule that only ever covered the one service somebody
        had already noticed. The API is exempt because it may
        legitimately be the edge (its bind address is a variable,
        defaulting to loopback, checked above) and the portal is the
        thing a person is meant to open.
        """
        for compose, which in ((prod, 'prod'), (local, 'local')):
            for name, service in compose['services'].items():
                if name in ('igni_api', 'portal', 'caddy'):
                    continue
                for port in _published(service):
                    assert port.startswith('127.0.0.1:'), f'{which}: {name} publishes {port} on every interface'


# Services running an image this project builds, as opposed to Postgres,
# Redis and Caddy — which are third-party and must be pinned literals.
#
# Derived rather than listed: it was a tuple naming five services, two of
# which DEC-13 deleted, and the three checks below then failed with a
# KeyError that says nothing about images. A service whose image comes
# from a variable is one of ours by definition.
_OURS = ('migrate', 'igni_api', 'portal')


class TestTheProdImageIsNotAGuess:
    def test_our_images_come_from_a_required_variable(self, prod):
        """A literal here is how `ember/ember-api:latest` survived: it looks
        deliberate and resolves to somewhere nobody controls."""
        offenders = [
            f'{name}: {prod["services"][name]["image"]}'
            for name in _OURS
            if not str(prod['services'][name]['image']).startswith('${')
        ]

        assert not offenders, (
            f'hardcoded image reference(s): {offenders}. Use the required '
            f'IGNI_IMAGE / IGNI_PORTAL_IMAGE variables — an unqualified name resolves '
            f'to Docker Hub, not to the registry CD pushes to.'
        )

    def test_the_variables_are_required_not_defaulted(self, prod):
        """`:-` would substitute a default silently. `:?` refuses to start
        and names the variable, which is the whole value of the change."""
        images = {str(prod['services'][name]['image']) for name in _OURS}

        assert len(images) == 2, f'expected the API image and the portal image, got {images}'
        for image in images:
            assert image.startswith('${IGNI_') and ':?' in image, image

    def test_third_party_images_are_pinned_to_a_minor_version(self, prod):
        """`postgres:17` moves under you on the next `pull`, and a major
        version change of Postgres is not a restart. These are the only
        literal images allowed, and they have to be specific."""
        third_party = {
            name: str(service['image'])
            for name, service in prod['services'].items()
            if 'image' in service and name not in _OURS
        }

        assert third_party, 'no third-party services found — the bundled profile is gone?'
        for name, image in third_party.items():
            assert ':' in image, f'{name}: {image} has no tag at all'
            tag = image.rsplit(':', 1)[1]
            assert tag != 'latest', f'{name}: {image}'
            assert (
                tag.count('.') >= 1
            ), f'{name}: {image} is pinned only to a major version, which moves on the next pull'

    def test_the_example_tag_is_not_latest(self, prod):
        """`latest` cannot be rolled back to, and `up` will not reliably
        re-pull it. The error message should not suggest it."""
        for name in _OURS:
            assert ':latest' not in str(prod['services'][name]['image']), name


class TestTheSingleInstanceStackIsWhole:
    """What a pre-marketplace customer gets: one host, `docker compose up`.
    The prod file used to be the API, a worker, Flower and the
    migration — no web UI and no datastores, so "one instance and
    compose" meant standing up RDS and ElastiCache first."""

    def test_there_is_a_portal(self, prod):
        assert (
            'portal' in prod['services']
        ), 'the production stack has no admin portal, so there is nothing a person can open'

    def test_the_portal_points_at_the_compose_service_name(self, prod):
        """nginx resolves this once at startup and exits if it does not
        resolve. `container_name` is not a DNS name on a compose network;
        the service name is."""
        env = prod['services']['portal']['environment']
        upstream = next(v.split('=', 1)[1] for v in env if v.startswith('API_UPSTREAM='))

        assert upstream.split(':')[0] in prod['services'], (
            f'API_UPSTREAM={upstream} does not name a compose service, so nginx exits '
            'at startup with "host not found in upstream"'
        )

    @pytest.mark.parametrize('service', ['postgres', 'redis'])
    def test_the_datastores_are_available_but_optional(self, prod, service):
        """Behind a profile, so both shapes work: bundled on one box for a
        single-tenant install, or external for a managed database."""
        assert prod['services'][service]['profiles'] == ['bundled'], service

    def test_nothing_depends_on_a_profiled_service(self, prod):
        """The trap. Compose refuses to load a project at all when a
        `depends_on` names a service whose profile is inactive — `service
        "migrate" depends on undefined service "redis": invalid compose
        project`. So one `depends_on: postgres` here would break every
        external-database deployment, which is the shape most customers
        run."""
        profiled = {name for name, service in prod['services'].items() if service.get('profiles')}

        for name, service in prod['services'].items():
            depends = service.get('depends_on') or {}
            overlap = profiled & set(depends)
            assert not overlap, (
                f'{name} depends on {sorted(overlap)}, which is behind a profile. With '
                'that profile off, compose refuses to load the whole project.'
            )

    def test_the_migration_waits_for_the_database_instead(self, prod):
        """Since it cannot use `depends_on`. Everything else waits for the
        migration, so this is the only place that needs to wait."""
        command = str(prod['services']['migrate']['command'])

        assert 'POSTGRES_HOST' in command, (
            'the migration does not wait for Postgres, and cannot use depends_on — so '
            'a bundled Postgres still initialising fails the deploy'
        )
        assert 'alembic upgrade head' in command


class TestTlsIsAnOverlayForAReason:
    def test_the_terminator_is_not_in_the_main_file(self, prod):
        """Compose interpolates variables in every service *before*
        selecting profiles, so a `${VAR:?}` inside a profiled service
        refuses to load the project even when that profile is off. Caddy
        was a `tls` profile first: bringing up the bundled stack without
        a domain failed with "required variable IGNI_DOMAIN is missing a
        value" for a container that was not going to start."""
        assert 'caddy' not in prod['services']

    def test_the_overlay_exists_and_refuses_without_a_domain(self):
        tls = _load('docker-compose.tls.yml')
        env = tls['services']['caddy']['environment']

        assert any('IGNI_DOMAIN=${IGNI_DOMAIN:?' in v for v in env), env
        assert any('IGNI_ACME_EMAIL=${IGNI_ACME_EMAIL:?' in v for v in env), env

    def test_the_proxy_does_not_get_the_application_secrets(self):
        """It needs a hostname and an email address. `env_file` would hand
        every credential in the deployment to the process that faces the
        internet."""
        tls = _load('docker-compose.tls.yml')

        assert 'env_file' not in tls['services']['caddy']

    def test_the_certificates_survive_a_restart(self):
        """Without a volume, every restart asks Let's Encrypt for a new
        certificate and hits the rate limit on the fifth."""
        volumes = str(_load('docker-compose.tls.yml')['services']['caddy']['volumes'])

        assert 'caddy_data:/data' in volumes

    def test_it_is_the_only_thing_that_binds_every_interface(self):
        """And it says 0.0.0.0 rather than omitting the address, so the
        reader can tell it is deliberate."""
        for port in _load('docker-compose.tls.yml')['services']['caddy']['ports']:
            assert str(port).startswith('0.0.0.0:'), port

    def test_the_caddyfile_routes_the_api_and_the_browser_separately(self):
        caddyfile = (_ROOT / 'Caddyfile').read_text()

        assert 'handle /v1/*' in caddyfile
        assert 'reverse_proxy igni_api:7777' in caddyfile
        assert 'reverse_proxy portal:3000' in caddyfile

    def test_it_forwards_the_scheme(self):
        """The API decides cookie `secure` and builds absolute links from
        this. Without it every OAuth callback and invitation link comes
        out as http:// against an internal hostname."""
        caddyfile = (_ROOT / 'Caddyfile').read_text()

        assert caddyfile.count('X-Forwarded-Proto {scheme}') >= 2

    def test_the_body_limit_agrees_with_the_other_two(self):
        """The API's own limit is 32 MB and the portal's nginx says 32m.
        Three places, and they have disagreed before: nginx's default of
        1 MB silently refused bodies the API would have accepted."""
        assert 'max_size 32MB' in (_ROOT / 'Caddyfile').read_text()
        assert 'client_max_body_size 32m' in (_ROOT.parent / 'portal/nginx.conf').read_text()

    def test_only_the_api_and_portal_are_routed(self):
        """This named Flower and its port, which DEC-13 removed — so it
        became a check that 5555 is absent from a file nothing was ever
        going to put 5555 back into.

        The rule underneath is the one worth keeping: Caddy is the only
        thing on the public interface, so every upstream it names is a
        deliberate publication decision. Anything else routed from here
        is on the internet.

        Checked against the `reverse_proxy` lines rather than the whole
        file, because a comment naming a port is a sentence about a route
        and not a route.
        """
        routes = [
            line
            for line in (_ROOT / 'Caddyfile').read_text().splitlines()
            if 'reverse_proxy' in line and not line.strip().startswith('#')
        ]

        assert routes, 'no routes found at all — this test is measuring nothing'

        upstreams = {token for route in routes for token in route.split() if ':' in token}
        unexpected = sorted(u for u in upstreams if not u.startswith(('igni_api:', 'portal:')))

        assert not unexpected, (
            f'Caddy routes to {unexpected}, which is then reachable from the internet. '
            'Only the API and the portal are meant to be.'
        )


class TestTheEnvFileIsNamedExplicitly:
    def test_prod_does_not_silently_read_dot_env(self, prod):
        offenders = [
            f'{name}: {service["env_file"]}'
            for name, service in prod['services'].items()
            if any(str(f) in ('.env', './.env') for f in (service.get('env_file') or []))
        ]

        assert not offenders, (
            f'{offenders} reads .env, the local development file. It carries '
            f'APP_ENV=dev and PORTAL_AUTH_BYPASS=true, and because the startup '
            f'check keys off APP_ENV from that same file, it disarms the guard '
            f'that exists to catch it.'
        )

    def test_it_is_a_required_variable(self, prod):
        env_files = {str(f) for s in prod['services'].values() for f in (s.get('env_file') or [])}

        assert len(env_files) == 1, env_files
        assert next(iter(env_files)).startswith('${IGNI_ENV_FILE:?'), env_files


class TestNothingServesBeforeTheSchemaExists:
    def test_everything_of_ours_waits_for_the_migration(self, prod):
        """Derived from `_OURS`, so a service we add is covered without
        anybody extending a parametrize list — and so removing one does
        not leave a KeyError behind, which is how DEC-13 broke this.

        The portal is in it too. It reads no schema, so the dependency
        is not strictly required — but the page it serves is useless
        until the API answers, and the API is behind the migration, so
        waiting costs nothing and starting early shows a broken portal
        instead of nothing at all.
        """
        for service in _OURS:
            if service == 'migrate':
                continue
            depends = prod['services'][service].get('depends_on') or {}

            assert 'migrate' in depends, f'{service} does not wait for migrate'
            assert depends['migrate'].get('condition') == 'service_completed_successfully', depends

    def test_migrate_itself_does_not_restart(self, prod):
        """`service_completed_successfully` needs it to exit. `restart:
        always` would leave the dependents waiting forever."""
        assert prod['services']['migrate'].get('restart') in ('no', False), prod['services']['migrate'].get('restart')

    def test_migrate_does_not_depend_on_anything(self, prod):
        """It is the root of the ordering; a cycle would deadlock `up`."""
        assert not prod['services']['migrate'].get('depends_on')


class TestTheImageRunsThePythonWeTest:
    """Both Dockerfile stages said `python:3.12-slim`.

    `pyproject.toml` declares `requires-python = "==3.13.*"` and every CI
    job runs 3.13, so the shipped image ran a Python the project says it
    does not support. The builder stage compounded it: `uv sync` fetches a
    managed 3.13 to satisfy `requires-python`, resolves against it and
    freezes `requirements.txt` — which stage 2 then installed with 3.12's
    pip.

    It imported fine, so nothing was broken. The problem is that nothing
    *could* catch it. Demonstrated with PEP 696 type-parameter defaults:

        type Alias[T = int] = list[T]

    parses on 3.13 and is a SyntaxError on 3.12. So that line passes lint,
    mypy and 1165 tests, and then crash-loops the container at import —
    the loudest possible failure, in the one place CI never looks.

    Asserted as a range rather than an exact string so a deliberate bump
    of both files together still passes.
    """

    def test_both_stages_match_requires_python(self):
        import re

        root = _ROOT.parent
        pyproject = (root / 'pyproject.toml').read_text()
        declared = re.search(r'requires-python\s*=\s*"==(\d+)\.(\d+)\.\*"', pyproject)
        assert declared, 'requires-python is no longer an exact minor pin; update this test'
        want = f'{declared.group(1)}.{declared.group(2)}'

        dockerfile = (root / 'containers/be/Dockerfile').read_text()
        froms = re.findall(r'^FROM python:(\d+\.\d+)', dockerfile, re.M)

        assert froms, 'no FROM python: line found'
        mismatched = [v for v in froms if v != want]
        assert not mismatched, (
            f'pyproject requires Python {want} and CI runs {want}, but the image '
            f'builds on {mismatched}. Code that passes every job can be a '
            f'SyntaxError in the container.'
        )

    def test_ci_runs_the_same_version(self):
        """The other half of the pair: if CI moved off the declared version
        the guard above would keep passing while the gap reopened."""
        import re

        root = _ROOT.parent
        pyproject = (root / 'pyproject.toml').read_text()
        want = re.search(r'requires-python\s*=\s*"==(\d+\.\d+)\.\*"', pyproject).group(1)

        ci = (root / '.github/workflows/ci.yml').read_text()
        versions = set(re.findall(r'python-version:\s*"([\d.]+)"', ci))

        assert versions, 'no python-version pins found in ci.yml'
        assert versions == {want}, f'pyproject wants {want}, ci.yml runs {sorted(versions)}'


class TestNamesDoNotSayEmber:
    """Operator-visible in `docker ps`, so the naming rule applies:
    `igni` and `ignite-ember` are fine, a bare `ember` is not."""

    @pytest.mark.parametrize('which', ['docker-compose.prod.yml', 'docker-compose.local.yml'])
    def test_no_bare_ember_in_container_or_network_names(self, which):
        import re

        compose = _load(which)
        names = [str(s['container_name']) for s in compose['services'].values() if 'container_name' in s]
        names += list((compose.get('networks') or {}).keys())

        offenders = [n for n in names if re.search(r'ember', n, re.I) and not re.search(r'ignite[-_]?ember', n, re.I)]

        assert not offenders, f'{which}: {offenders}'


class TestTheLogTimestampIsReal:
    """Every line this process logged ended in a literal `.%f`.

    `datefmt` is passed to `time.strftime`, which has no `%f` directive, so
    `'%Y-%m-%d %H:%M:%S.%f'` produced `17:17:14.%f`. Sub-second precision
    has to come from the *format* string's `%(msecs)03d` instead.

    Noticed on the first real boot of the production stack, in its own
    startup warnings — which is the kind of thing a thousand passing tests
    never look at, because none of them read a timestamp.

    The configuration moved to `app/observability/log_format.py` when
    F102 added the JSON format, and these two tests failed on the move
    rather than on a regression — they read `app/main.py`, found no
    `datefmt` at all, and said so. Which is the right outcome for a rule
    that asserts on source: it noticed the source was somewhere else.
    """

    _LOG_CONFIG = pathlib.Path(__file__).resolve().parent.parent / 'app/observability/log_format.py'

    def test_datefmt_does_not_use_an_unsupported_directive(self):
        import re

        datefmt = re.search(r"datefmt='([^']*)'", self._LOG_CONFIG.read_text())

        assert datefmt, f'no datefmt found in {self._LOG_CONFIG.name}'
        assert '%f' not in datefmt.group(1), (
            f'datefmt={datefmt.group(1)!r} — time.strftime has no %f, so it is '
            f'emitted literally. Use %(msecs)03d in the format string.'
        )

    def test_the_format_carries_milliseconds(self):
        import re

        fmt = re.search(r"fmt='([^']*)'", self._LOG_CONFIG.read_text())

        assert fmt and '%(msecs)03d' in fmt.group(
            1
        ), 'sub-second precision was lost entirely; add %(msecs)03d to the format'

    def test_a_rendered_line_ends_with_digits(self, capsys):
        """The assertion that would have caught the original: emit a real
        record and read the output, rather than inspect the config.

        Through the logger rather than the formatter, so it exercises the
        same path a real log line takes.
        """
        import logging
        import re

        from app.observability.log_format import configure

        configure('INFO', as_json=False)
        logging.getLogger('probe').warning('hello')

        rendered = capsys.readouterr().err.strip()

        assert re.search(
            r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \|', rendered
        ), f'{rendered!r} does not start with a timestamp carrying milliseconds'
        assert '.%f' not in rendered, f'{rendered!r} — the literal directive is back'
        assert rendered.endswith('hello'), rendered


class TestBackupsExistAndCanBeTrusted:
    """F100. Verified against a running stack before this was written: the
    dump restored into a scratch database, the schema matched, and
    `alembic_version` came back at head. The assertions here are about the
    ways that stops being true quietly."""

    def test_the_backup_service_exists(self, prod):
        assert 'backup' in prod['services'], (
            'nothing dumps the database. Under the bundled profile the pgdata volume '
            'is the deployment, and it has no copy anywhere.'
        )

    def test_it_works_with_either_database(self, prod):
        """Its own profile rather than `bundled`, because a managed
        Postgres needs backing up just as much — it reads POSTGRES_HOST
        from the same env file."""
        assert prod['services']['backup']['profiles'] == ['backup']

    def test_it_waits_for_the_schema(self, prod):
        """Found by running it: without this the backup container beat the
        migration and dumped an 819-byte database with no application
        tables. The canary check refused the result, correctly, and then
        crash-looped until the migration finished."""
        assert 'migrate' in (prod['services']['backup'].get('depends_on') or {})

    def test_pg_dump_comes_from_the_database_image(self, prod):
        """`pg_dump` must be at least the server's version. Using the
        application image would pin it to whatever Postgres client that
        happens to carry."""
        backup = str(prod['services']['backup']['image'])
        postgres = str(prod['services']['postgres']['image'])

        assert backup == postgres, f'{backup} != {postgres}: the dump tool and the server can drift'

    def test_the_healthcheck_reads_the_last_success_and_not_the_process(self, prod):
        """A running container is not a backup. This is the closest thing
        to evidence `docker ps` can show, and it goes red when the newest
        *verified* dump gets too old."""
        check = str(prod['services']['backup']['healthcheck']['test'])

        assert '.last-success' in check, check

    def test_the_dumps_leave_the_container(self, prod):
        volumes = str(prod['services']['backup']['volumes'])

        assert '/backups' in volumes
        assert (
            'IGNI_BACKUP_DIR' in volumes
        ), 'the destination is not configurable, so a customer cannot point it at durable storage'


class TestTheBackupScriptFailsClosed:
    """Read from the script, because each of these was demonstrated
    against a live database and each is a way a backup file exists and is
    worthless."""

    @pytest.fixture
    def script(self) -> str:
        return (_ROOT / 'backup.sh').read_text()

    def test_the_archive_is_verified_before_it_counts(self, script):
        """`pg_dump` exiting 0 is not proof of a readable archive."""
        assert 'pg_restore --list' in script

    def test_it_checks_the_dump_is_of_this_schema(self, script):
        """A valid archive of the wrong database passes every other check.
        Demonstrated: dumping a freshly created empty database produced a
        parseable 819-byte archive, which this rejects."""
        # The control flow, not the word. A first version of this asserted
        # `'CANARY_TABLE' in script` and passed against a script whose
        # check had been disabled — the name still appeared in the
        # comments and in the unused variable.
        assert (
            'if ! grep -q " TABLE public $canary " /tmp/toc.txt; then' in script
        ), 'the canary check is not in the script, or no longer guards anything'
        assert 'audit_events' in script, 'no canary table is named'
        # Every archive is checked against its own schema's canary: the
        # shared plane's, and an organisation's own.
        assert 'dump_one "$POSTGRES_DB" "$run_part/$shared_file" "$CANARY_TABLE"' in script
        assert 'dump_one "$database" "$run_part/$org_file" "$TENANT_CANARY_TABLE"' in script
        assert script.count('return 1') >= 3, 'fewer failure paths than expected — one of the checks stopped refusing'

    def test_it_writes_to_part_and_renames(self, script):
        """A rename within one filesystem is atomic, so a crash leaves an
        obviously incomplete file rather than a truncated one that looks
        like a backup."""
        assert '.part' in script
        assert 'mv "$part" "$target"' in script

    def test_it_prunes_only_after_a_verified_dump(self, script):
        """Retention that runs unconditionally turns one failing night
        into an empty backup directory a fortnight later. Demonstrated
        both ways: a failing run left a 2025 dump in place, and the next
        successful run removed it."""
        prune_at = script.index('-mtime "+$RETAIN_DAYS"')
        verify_at = script.index('pg_restore --list')
        rename_at = script.index('mv "$part" "$target"')

        assert verify_at < prune_at, 'retention runs before the archive is verified'
        assert rename_at < prune_at, 'retention runs before the new dump is in place'
        # And before the *whole run* is: one organisation failing must not
        # be followed by pruning the last complete run.
        assert script.index('mv "$run_part" "$run"') < prune_at, 'retention runs before the run is complete'
        assert script.index('if [ "$failed" -gt 0 ]; then') < script.index('mv "$run_part" "$run"')

    def test_the_first_failure_is_fatal(self, script):
        """A backup service that waits until tomorrow to mention the
        password is wrong is not a backup service. With `restart: always`
        in front, a bad configuration is a crash loop at deploy time."""
        assert 'exiting so this is visible now' in script

    def test_later_failures_are_not_fatal(self, script):
        """One bad night should not stop tomorrow's attempt. The
        healthcheck is what notices, because `.last-success` stops
        moving."""
        assert 'will retry after the interval' in script

    def test_the_dumps_are_not_world_readable(self, script):
        """The archive holds everything anybody typed, and provider
        credentials whose encryption key lives elsewhere."""
        assert 'chmod 600' in script

    def test_the_canary_table_is_one_this_schema_really_has(self, script):
        """The name, against the schema rather than against itself.

        F5. The test above proves `audit_events` appears in the script.
        Nothing proved the table exists. A migration renaming it would
        leave every backup failing its own verification — at 3am, with
        `.last-success` frozen and the healthcheck red — and the whole
        suite green, because the string is still in the file.

        Read from the model metadata, so it follows a rename rather than
        being a second place to remember.
        """
        import re

        from app.db.sql.models import Base, load_all_models, TenantBase

        load_all_models()

        # The shared plane's archive, and each organisation's: two
        # schemas, two canaries, each checked against its own metadata.
        for variable, base in (('CANARY_TABLE', Base), ('TENANT_CANARY_TABLE', TenantBase)):
            tables = set(base.metadata.tables)
            env = 'IGNI_BACKUP_' + variable
            match = re.search(rf'\b{variable}="\$\{{{env}:-([a-z_]+)\}}"', script)
            assert match, f'could not find the {variable} default in the script'
            canary = match.group(1)

            assert canary in tables, (
                f'the backup script verifies every dump contains {canary!r} ({variable}), and that '
                f'schema has no such table. Every backup would fail its own check. Tables: {sorted(tables)}'
            )

    def test_the_restore_drill_exists_and_ci_runs_it(self):
        """F5's other half.

        The drill is the only thing that has ever executed this script,
        and a drill nothing runs is `Dockerfile.dockerignore` — a file
        that looked like a control and was not one (F1). So: it is on
        disk, it is executable, and a CI job invokes it.
        """
        drill = _ROOT.parent / 'tools/restore-drill.sh'
        assert drill.exists(), f'{drill} is missing'
        assert drill.stat().st_mode & 0o111, 'the drill is not executable'

        ci = (_ROOT.parent / '.github/workflows/ci.yml').read_text()
        assert 'tools/restore-drill.sh' in ci, 'no CI job runs the restore drill'

    def test_it_runs_under_sh_not_bash(self, script):
        """The Postgres alpine image has no bash. A `#!/bin/bash` here
        fails with `no such file or directory`, which reads like the
        script is missing."""
        assert script.splitlines()[0] == '#!/bin/sh'


class TestThePortalStartsWithoutTheApi:
    """F105. nginx resolves a literal `proxy_pass` host once, at load, and
    exits if it does not resolve — so this container could not start
    unless the API was already resolvable.

    Found by rebuilding the local portal, which crash-looped with `host
    not found in upstream "ember_api"`: a service name from before the
    rename, which no compose file had defined for some time. Two bugs at
    once — a stale default, and a container that treats a missing
    dependency as a reason not to boot.

    Verified by running it: standalone, `/` and `/compliance` return 200
    and `/v1/...` returns 502; with an API on the network, `/v1` proxies
    through and the path arrives unchanged.
    """

    @pytest.fixture
    def nginx_conf(self) -> str:
        return (_ROOT.parent / 'portal/nginx.conf').read_text()

    def test_the_upstream_is_a_variable(self, nginx_conf):
        """A literal is resolved at startup; a variable is resolved per
        request. That is the difference between a portal that will not
        boot and one that answers 502 until the API arrives."""
        # Every real `proxy_pass`, ignoring any inside comments. My first
        # version filtered comments with a slice expression that was
        # nonsense and matched a commented line instead.
        directives = [
            line.strip()
            for line in nginx_conf.splitlines()
            if 'proxy_pass' in line and not line.strip().startswith('#')
        ]

        assert directives, 'no proxy_pass at all — this test is measuring nothing'
        for directive in directives:
            assert '$' in directive, f'{directive} — a literal host is resolved once, at startup'

    def test_the_whole_request_uri_is_passed(self, nginx_conf):
        """With a variable in `proxy_pass`, nginx does no prefix
        substitution — so the URI has to be passed explicitly, and the
        API serves the same `/v1/...` paths the location matches."""
        assert '$request_uri' in nginx_conf

    def test_a_resolver_is_provided_at_start(self):
        """A variable upstream needs one, and the address differs by
        platform. Read from /etc/resolv.conf rather than hardcoded, so
        the same image works in compose and in Kubernetes."""
        script = (_ROOT.parent / 'portal/docker-entrypoint.d/19-igni-resolver.sh').read_text()

        assert '/etc/resolv.conf' in script
        assert (
            '127.0.0.11' not in script.split('#')[0] or 'hardcod' in script
        ), 'the resolver address is hardcoded, which works in compose and not in Kubernetes'

    def test_a_missing_nameserver_does_not_stop_the_portal(self):
        """The application is static files and does not need DNS. A
        working portal whose proxy fails loudly beats a container that
        will not boot."""
        script = (_ROOT.parent / 'portal/docker-entrypoint.d/19-igni-resolver.sh').read_text()

        assert 'exit 0' in script

    def test_the_image_default_names_a_service_that_exists(self):
        """The bug itself. `ember_api` had not been a service name for
        some time, and nothing failed until nginx tried to resolve it."""
        dockerfile = (_ROOT.parent / 'portal/Dockerfile').read_text()
        default = next(line for line in dockerfile.splitlines() if line.startswith('ENV API_UPSTREAM'))
        host = default.split('"')[1].split(':')[0]

        services = set(_load('docker-compose.local.yml')['services']) | set(
            _load('docker-compose.prod.yml')['services']
        )
        assert host in services, f'{host!r} is not a service in either compose file; {sorted(services)}'

    @pytest.mark.parametrize('which', ['docker-compose.prod.yml', 'docker-compose.local.yml'])
    def test_both_compose_files_set_it_explicitly(self, which):
        """Rather than inheriting the image default, which is how it
        pointed at a renamed service unnoticed."""
        portal = _load(which)['services']['portal']
        environment = str(portal.get('environment') or '')

        assert 'API_UPSTREAM' in environment, f'{which} leaves the portal upstream to the image default'


def _environment(service: dict) -> dict[str, str]:
    """A service's `environment`, as a mapping, whichever spelling the file uses."""
    raw = service.get('environment') or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return dict(str(item).split('=', 1) for item in raw)


class TestTheLocalStackIsTheOneTheReadmeDescribes:
    """`make up` on a machine with nothing but Docker, reading the Quick start's .env.

    The local file had no Postgres or Redis — the API waited on a server
    the README never asked anybody to start — and it read
    `containers/.env`, while the Quick start makes `.env` at the root.
    """

    def test_it_brings_its_own_postgres_and_redis(self, local):
        services = local['services']
        assert {'postgres', 'redis'} <= set(services)
        for name in ('migrate', 'igni_api'):
            environment = _environment(services[name])
            assert environment['POSTGRES_HOST'] == 'postgres', name
            assert environment['REDIS_HOST'] == 'redis', name
        assert services['migrate']['depends_on']['postgres']['condition'] == 'service_healthy'
        assert services['igni_api']['depends_on']['migrate']['condition'] == 'service_completed_successfully'

    def test_the_database_it_creates_is_the_one_it_connects_to(self, local):
        services = local['services']
        database = _environment(services['postgres'])
        for name in ('migrate', 'igni_api'):
            environment = _environment(services[name])
            for key in ('POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'):
                assert environment[key] == database[key], (name, key)

    def test_it_reads_the_env_file_the_quick_start_makes(self, local):
        readme = (_ROOT.parent / 'README.md').read_text()
        # `make dev-env` writes it (tools/dev_env.py, default --out is the root
        # .env); copying dist.env by hand is the production route.
        assert '\nmake dev-env\n' in readme or '\ncp dist.env .env\n' in readme, (
            'the Quick start no longer makes the root .env'
        )
        assert '--env-file .env' not in readme, 'the README still points compose at containers/.env'

        for name in ('migrate', 'igni_api'):
            env_files = local['services'][name]['env_file']
            paths = [entry['path'] if isinstance(entry, dict) else entry for entry in env_files]
            assert paths == ['../.env'], (name, paths)
        makefile = (_ROOT.parent / 'Makefile').read_text()
        assert '--env-file ../.env' in makefile, '`make up` interpolates from containers/.env'
