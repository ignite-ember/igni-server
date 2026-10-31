"""Whether this process can actually serve a request.

Lifted out of the `/ready` route, which had grown the dependency probes,
a saturation-versus-outage decision and the dependency gauge into a
single handler.

Two dependencies: Postgres and Redis, the stores everything here reads.
The probes for the artifact graph and the embedder are gone with the
clients they belonged to — nothing in this server queried either, both
live in igni__artifacts, and an embedder outage could drain every API
replica for a feature this process did not serve.

Which states drain an instance, and why only `unavailable` does, is
recorded on `DependencyState.is_draining`.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
import logging

from pydantic import BaseModel
import redis.asyncio as aioredis

from app.config import settings
from app.db.sql import PoolTimeout, SQLAlchemySessionManager
from app.monitoring import set_dependency

logger = logging.getLogger(__name__)

#: How long `/ready` waits for a database connection. Deliberately
#: shorter than `DB_POOL_TIMEOUT`: the check has to return before a
#: queueing request would, or it cannot tell "saturated" from "gone".
#: Well under the probe timeout in every artefact we ship (10s in the
#: chart).
READY_DB_TIMEOUT = 2.0


class DependencyState(StrEnum):
    """How a dependency says whether it can be used right now.

    The checks used to be a `dict[str, str]` whose values carried the
    state as prose, and the readiness decision was
    `startswith('unavailable')` over those strings — a dependency that
    ever reported something like `'unavailable_but_fine'` would have
    pulled the pod out of rotation. The states are named here instead,
    and `DependencyCheck.wire` renders the published string.
    """

    OK = 'ok'
    BUSY = 'busy'
    UNAVAILABLE = 'unavailable'

    @property
    def is_draining(self) -> bool:
        """Whether this state should take the instance out of rotation.

        Only `unavailable`. `busy` means the pool is saturated: the
        instance is working, and removing a working replica from a load
        balancer is the one action guaranteed to make a capacity problem
        worse, because its traffic lands on the sibling about to
        saturate too.
        """
        return self is DependencyState.UNAVAILABLE


class DependencyCheck(BaseModel):
    """One dependency's answer, and the reason when there is one."""

    state: DependencyState
    #: Never an exception *message* — a connection error carries the
    #: host, the port and sometimes the user, and `/ready` is reachable
    #: by whoever can reach the API. Exception types only.
    reason: str | None = None

    @property
    def wire(self) -> str:
        """The string `/ready` publishes for this check.

        Unchanged on purpose: `/ready` is parsed by load balancers and by
        the Helm chart's probes, and its body is a contract with things
        outside this repository.
        """
        return self.state.value if self.reason is None else f'{self.state.value}: {self.reason}'

    @classmethod
    def unavailable(cls, exc: BaseException) -> DependencyCheck:
        """The failure answer: the exception's *type*, never its message."""
        return cls(state=DependencyState.UNAVAILABLE, reason=type(exc).__name__)


class LivenessReport(BaseModel):
    """The `/health` body. Says nothing about dependencies, on purpose."""

    status: str


class VersionReport(BaseModel):
    """The `/version` body."""

    version: str


class ReadinessReport(BaseModel):
    """The `/ready` body.

    `checks` stays a plain `dict[str, str]` — `postgres` and `redis` —
    because its values are the published strings. The states behind them
    are typed; this is the rendering.
    """

    status: str
    checks: dict[str, str]

    @property
    def ready(self) -> bool:
        return self.status == 'ready'


class ReadinessProbe:
    """Asks each dependency the cheapest question that proves it answers."""

    def __init__(self, *, db: SQLAlchemySessionManager | None = None) -> None:
        self._db = db or SQLAlchemySessionManager()

    async def postgres(self) -> DependencyCheck:
        """`SELECT 1`, with saturation told apart from an outage.

        A timeout while the pool is full is `busy` and stays ready; the
        same timeout with the pool *not* full means nothing is answering.
        F116.
        """
        try:
            await asyncio.wait_for(self._db.ping(), timeout=READY_DB_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError, PoolTimeout):
            status_now = SQLAlchemySessionManager.pool_status()
            if status_now is not None and status_now[0] >= status_now[1]:
                return DependencyCheck(
                    state=DependencyState.BUSY,
                    reason=f'connection pool saturated ({status_now[0]}/{status_now[1]})',
                )
            return DependencyCheck(state=DependencyState.UNAVAILABLE, reason='TimeoutError')
        except Exception as exc:  # noqa: BLE001 — the type is the payload
            return DependencyCheck.unavailable(exc)
        return DependencyCheck(state=DependencyState.OK)

    async def redis(self) -> DependencyCheck:
        try:
            client = aioredis.from_url(settings.redis_url)
            try:
                await client.ping()
            finally:
                await client.aclose()
        except Exception as exc:  # noqa: BLE001
            return DependencyCheck.unavailable(exc)
        return DependencyCheck(state=DependencyState.OK)

    async def run(self) -> ReadinessReport:
        """Every probe, the gauge, and the verdict.

        Probes run in the order they are published, which is also the
        order of the dict `/ready` returns.
        """
        checks = {
            'postgres': await self.postgres(),
            'redis': await self.redis(),
        }

        # The gauge mirrors this check rather than running its own: two
        # health checks that can disagree is a support conversation
        # nobody wins. It is therefore only as fresh as the last probe.
        #
        # `busy` counts as up. Postgres answered the last time anything
        # got through to it; what is exhausted is this process's pool,
        # and a dependency gauge reading 0 would send an operator to the
        # database.
        for dependency, check in checks.items():
            set_dependency(dependency, not check.state.is_draining)

        ready = not any(check.state.is_draining for check in checks.values())
        return ReadinessReport(
            status='ready' if ready else 'not ready',
            checks={name: check.wire for name, check in checks.items()},
        )
