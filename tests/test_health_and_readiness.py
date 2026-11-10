"""Liveness and readiness are different questions, and were the same answer.

``/health`` returned ``{"status": "ok"}`` unconditionally, and nothing
consumed it — no compose healthcheck, no deploy probe. Verified by running
the API with Postgres and Redis unreachable: it booted, ``/health``
answered 200, and every database-backed route answered 500 in about six
milliseconds. A load balancer reading ``/health`` would have kept sending
traffic to that instance forever.

The split matters in both directions:

* ``/health`` decides whether to **restart**, so it must not check
  dependencies. A liveness probe that fails on a database blip restarts
  every instance at once for a problem no restart fixes.
* ``/ready`` decides whether to **send traffic**, so it must check them.

The fast 500 is worth recording as the good news it is: no hang, no
connection pile-up, and the body is a bare ``Internal Server Error`` with
no host, port or traceback in it.
"""

from __future__ import annotations

import pytest


# The shared client fixture is based at http://test/v1; these two
# endpoints are mounted at the app root, so they are addressed absolutely.
class TestLivenessChecksNothing:
    async def test_health_is_ok(self, client):
        response = await client.get('http://test/health')

        assert response.status_code == 200
        assert response.json() == {'status': 'ok'}

    async def test_health_does_not_touch_a_dependency(self):
        """Asserted on the source, because the value of this endpoint is
        what it does *not* do — and a test with a working database cannot
        tell a check that passed from no check at all."""
        import inspect

        from app.api.health import health_check

        body = inspect.getsource(health_check)
        for forbidden in ('session_scope', 'execute', 'ping', 'redis'):
            assert forbidden not in body, f'liveness must not reach a dependency: found {forbidden!r}'


class TestReadinessChecksBoth:
    async def test_it_reports_ready_when_dependencies_are_up(self, client):
        """The test suite has a real Postgres and Redis, so this is the
        healthy path end to end."""
        response = await client.get('http://test/ready')

        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'ready'
        assert payload['checks']['postgres'] == 'ok'
        assert payload['checks']['redis'] == 'ok'

    async def test_a_dead_database_makes_it_503(self, client, monkeypatch):
        """503 rather than 500: the instance is alive and should be
        drained, not killed."""

        class _Boom:
            def session_scope(self):
                raise ConnectionRefusedError('nope')

        import app.db.sql as sql_module

        monkeypatch.setattr(sql_module, 'SQLAlchemySessionManager', lambda: _Boom())

        response = await client.get('http://test/ready')

        assert response.status_code == 503
        payload = response.json()
        assert payload['status'] == 'not ready'
        assert payload['checks']['postgres'].startswith('unavailable')

    async def test_a_dead_redis_makes_it_503(self, client, monkeypatch):
        import redis.asyncio as aioredis

        def _boom(*args, **kwargs):
            raise ConnectionError('no redis')

        monkeypatch.setattr(aioredis, 'from_url', _boom)

        response = await client.get('http://test/ready')

        assert response.status_code == 503
        assert response.json()['checks']['redis'].startswith('unavailable')

    async def test_the_failure_body_names_types_not_hosts(self, client, monkeypatch):
        """A connection error's message carries the host, the port and
        sometimes the user. This endpoint is reachable by anybody who can
        reach the API, so only the exception type goes in the payload.
        """
        import redis.asyncio as aioredis

        def _boom(*args, **kwargs):
            raise ConnectionError('Error connecting to redis-internal.prod:6379. Auth failed for user redis-admin.')

        monkeypatch.setattr(aioredis, 'from_url', _boom)

        response = await client.get('http://test/ready')

        body = response.text
        assert 'ConnectionError' in body
        assert 'redis-internal.prod' not in body
        assert 'redis-admin' not in body
        assert '6379' not in body


class TestTheProdComposeUsesThem:
    """A probe nothing consumes is a probe that does not work.

    ``restart: always`` without a healthcheck only catches the process
    exiting, so a hung API stayed "up" indefinitely. These assert the
    compose file wires them, and wires the *right* one to the right job.
    """

    @pytest.fixture
    def prod(self):
        import pathlib

        import yaml

        root = pathlib.Path(__file__).resolve().parent.parent
        return yaml.safe_load((root / 'containers' / 'docker-compose.prod.yml').read_text())

    def test_the_api_has_a_healthcheck(self, prod):
        assert prod['services']['igni_api'].get('healthcheck')

    def test_the_api_probes_liveness_not_readiness(self, prod):
        """Pointed at /ready, a database blip would restart every
        instance at once for something no restart fixes."""
        test = str(prod['services']['igni_api']['healthcheck']['test'])

        assert '/health' in test
        assert '/ready' not in test

    def test_every_long_running_service_of_ours_has_one(self, prod):
        """The rule, rather than one test per service.

        There was a second test here for the worker, which asked Celery
        `inspect ping` because it served no HTTP. DEC-13 removed it, and a
        rule stated over whatever the file declares is what stops the next
        service arriving without a healthcheck at all — `restart: always`
        cannot see a process that is up and doing nothing.

        `migrate` is excluded and separately asserted to have none: it
        runs once and exits, so a healthcheck on it is permanent
        unhealth.
        """
        ours = [
            name
            for name, definition in prod['services'].items()
            if 'IGNI_API_IMAGE' in str(definition.get('image') or '')
            or 'IGNI_PORTAL_IMAGE' in str(definition.get('image') or '')
        ]
        assert ours, 'found no service running an image we build, so this rule measured nothing'

        missing = [n for n in ours if n != 'migrate' and not prod['services'][n].get('healthcheck')]
        assert not missing, f'{missing} run continuously with no healthcheck'

    def test_the_migrate_job_has_none(self, prod):
        """It runs once and exits; a healthcheck on it would be a
        permanently unhealthy container."""
        assert not prod['services']['migrate'].get('healthcheck')
        assert prod['services']['migrate'].get('restart') == 'no'
