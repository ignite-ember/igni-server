"""Every Redis key igni server writes, inside its own namespace.

igni server can share one physical Redis with other services, each
separated by a key prefix. A prefix is easy to get wrong silently: two
services writing one key corrupt each other's state with nothing naming
the cause. So every key is built here, under ``igni:server:``, and
``tests/test_redis_namespace.py`` holds the code to that.

    ``key('backends', 'inflight')`` → ``igni:server:backends:inflight``
"""

# Every Redis key this service writes starts with this.
NAMESPACE = 'igni:server:'


def key(*parts: str) -> str:
    """Build a namespaced key from its parts.

    ``key('backends', 'inflight')`` → ``igni:server:backends:inflight``
    """
    return NAMESPACE + ':'.join(parts)
