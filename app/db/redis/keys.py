"""Redis keys this service owns.

Every key igni writes is prefixed with the service that owns it. The two
services can then share one Redis — one physical instance, and even one
logical database — without being able to read or clobber each other's
state.

This is not theoretical tidiness. Both services run an inference pool and
both call one of its pools ``codeindex``, so before this the in-flight
lease sets were *the same key*. Two services incrementing one concurrency
gauge makes every node look twice as busy as it is, and the pool
eventually decides a healthy cluster is full — a failure that shows up as
latency and capacity errors, with nothing anywhere naming the cause.

Separate logical databases (10 and 11 by default) already prevented it.
This makes the separation a property of the keys rather than of a setting
somebody could reasonably decide to consolidate.
"""

# Every Redis key this service writes starts with this.
NAMESPACE = 'igni:server:'


def key(*parts: str) -> str:
    """Build a namespaced key from its parts.

    ``key('backends', pool, 'inflight')`` → ``igni:server:backends:<pool>:inflight``
    """
    return NAMESPACE + ':'.join(parts)
