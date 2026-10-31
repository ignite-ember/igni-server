"""The live chat path: whose org is this request billed and routed as?

Chat is the production surface — the code-index endpoints exist but are
not what customers use — and it takes the org context from a caller
supplied header, ``X-Org-Id``. So the question is whether that header is
believed.

It is not, and that is right: ``resolve_user_org`` asserts active
membership of whatever org is named, header or token claim. Verified by
handing it another org's id and getting 403.

What this file also pins is the *timing* of that assertion, which turned
out to be the interesting part.
"""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.db.sql.team import TeamDB
from app.services.auth.auth_service import AuthService
from tests.conftest import add_member, create_org, create_user, make_token


@pytest.fixture
async def two_orgs(db):
    await create_user(db, user_id='alice', email='a@a.com')
    await create_user(db, user_id='bob', email='b@b.com')
    a = await create_org(db, owner_user_id='alice', slug='a', display_name='A')
    b = await create_org(db, owner_user_id='bob', slug='b', display_name='B')
    await add_member(db, org_id=str(a.id), user_id='alice', role='admin')
    await add_member(db, org_id=str(b.id), user_id='bob', role='admin')
    return {'a': str(a.id), 'b': str(b.id), 'alice': make_token('alice', org_id=str(a.id))}


class TestTheOrgHeaderIsNotBelieved:
    async def test_her_own_org_resolves(self, two_orgs):
        user_id, org_id = await AuthService.resolve_user_org(two_orgs['alice'], org_id=two_orgs['a'])

        assert user_id == 'alice'
        assert org_id == two_orgs['a']

    async def test_another_org_is_403(self, two_orgs):
        """``X-Org-Id`` reaches this straight from the request, and it
        flows on into usage accounting — so a believed header would mean
        billing somebody else's org for your inference."""
        with pytest.raises(HTTPException) as caught:
            await AuthService.resolve_user_org(two_orgs['alice'], org_id=two_orgs['b'])

        assert caught.value.status_code == 403


class TestRevocationTakesEffectAtOnce:
    """The reason the membership check is not cached.

    It used to carry ``@async_ttl_cache(ttl=600)`` and say "cached for 10
    minutes" — and it was never cached, because the decorator read a
    ``None`` result back as a miss. Fixing the decorator without thinking
    would have *introduced* a ten-minute window in which a removed member
    keeps chatting, on the one path that spends money per request.

    So the decorator is fixed and this guard is deliberately uncached.
    """

    async def test_a_removed_member_loses_access_immediately(self, db, two_orgs):
        await create_user(db, user_id='temp', email='t@a.com')
        await add_member(db, org_id=two_orgs['a'], user_id='temp', role='member')
        token = make_token('temp', org_id=two_orgs['a'])

        # In.
        assert (await AuthService.resolve_user_org(token, org_id=two_orgs['a']))[1] == two_orgs['a']

        await TeamDB().delete(org_id=two_orgs['a'], user_id='temp')

        # Out, with no window.
        with pytest.raises(HTTPException) as caught:
            await AuthService.resolve_user_org(token, org_id=two_orgs['a'])
        assert caught.value.status_code == 403

    async def test_the_guard_is_not_wrapped_in_a_cache(self):
        """Asserted on the object, because "immediately" is only true for
        as long as nobody re-adds the decorator for the throughput."""
        from app.services.auth import auth_service

        assert not hasattr(auth_service._assert_active_member, 'cache')

    async def test_a_denial_is_never_sticky(self, db, two_orgs):
        """The other direction: being granted membership has to work at
        once too. Under the old decorator this was accidentally fine —
        exceptions propagate before the cache write — and it should stay
        fine on purpose."""
        await create_user(db, user_id='joiner', email='j@a.com')
        token = make_token('joiner', org_id=two_orgs['a'])

        with pytest.raises(HTTPException):
            await AuthService.resolve_user_org(token, org_id=two_orgs['a'])

        await add_member(db, org_id=two_orgs['a'], user_id='joiner', role='member')

        # A session issued before the membership began is refused on purpose
        # (`MembershipSessions`), so reusing the first token passed only when
        # both landed in the same second. The joiner signs in again, as they
        # would; what must not be sticky is the denial for this person here.
        token = make_token('joiner', org_id=two_orgs['a'])
        assert (await AuthService.resolve_user_org(token, org_id=two_orgs['a']))[1] == two_orgs['a']


class TestTheCacheDecoratorItself:
    """It is a shared utility, so the trap is worth removing even though
    its only ``None``-returning caller is gone."""

    async def test_it_now_caches_a_none_result(self):
        from app.utils.cache import async_ttl_cache

        calls: list[int] = []

        @async_ttl_cache(maxsize=8, ttl=60)
        async def returns_none(x: int) -> None:
            calls.append(x)
            return None

        await returns_none(1)
        await returns_none(1)

        assert calls == [1], 'a None-returning function re-ran on a cache hit'

    async def test_falsey_results_were_always_fine(self):
        """``False``, ``0`` and ``[]`` are not ``None``, so they cached
        correctly even before — worth pinning so a future rewrite of the
        miss check does not break them."""
        from app.utils.cache import async_ttl_cache

        calls: list[str] = []

        @async_ttl_cache(maxsize=8, ttl=60)
        async def falsey(kind: str):
            calls.append(kind)
            return {'false': False, 'zero': 0, 'empty': []}[kind]

        for kind in ('false', 'zero', 'empty'):
            first = await falsey(kind)
            second = await falsey(kind)
            assert first == second

        assert calls == ['false', 'zero', 'empty']

    async def test_two_values_that_print_alike_are_still_two_entries(self):
        """The key was ``str((args, kwargs))`` — a repr.

        Anything whose ``__repr__`` does not distinguish it from a peer
        shared one cache entry, so the second caller got the first
        caller's answer. Hashing the arguments themselves uses the
        equality the type actually defines.
        """
        from app.utils.cache import async_ttl_cache

        class Opaque:
            """Identity equality, constant repr — the collision case."""

            def __repr__(self) -> str:
                return '<opaque>'

        calls: list[object] = []

        @async_ttl_cache(maxsize=8, ttl=60)
        async def lookup(subject: object) -> int:
            calls.append(subject)
            return len(calls)

        first, second = Opaque(), Opaque()
        assert await lookup(first) == 1
        assert await lookup(second) == 2, 'a second distinct object read the first one\'s entry'
        assert await lookup(first) == 1, 'the first object stopped hitting its own entry'

    async def test_an_unhashable_argument_is_refused_rather_than_mis_keyed(self):
        """Loud beats wrong. A repr key accepted a list and then keyed
        two different lists that print alike to the same entry."""
        from app.utils.cache import async_ttl_cache

        @async_ttl_cache(maxsize=8, ttl=60)
        async def lookup(items: list[str]) -> int:
            return len(items)

        with pytest.raises(TypeError):
            await lookup(['a'])

    async def test_each_decorated_function_gets_its_own_store(self):
        """Binding the decorator to a name and applying it twice used to
        hand both functions one cache — so two nullary functions served
        each other's answers."""
        from app.utils.cache import async_ttl_cache

        memoize = async_ttl_cache(maxsize=8, ttl=60)

        @memoize
        async def left() -> str:
            return 'left'

        @memoize
        async def right() -> str:
            return 'right'

        assert await left() == 'left'
        assert await right() == 'right'

    async def test_distinct_arguments_are_distinct_entries(self):
        from app.utils.cache import async_ttl_cache

        calls: list[tuple] = []

        @async_ttl_cache(maxsize=8, ttl=60)
        async def two_args(a: str, b: str) -> None:
            calls.append((a, b))

        await two_args('x', 'y')
        await two_args('y', 'x')
        await two_args('x', 'y')

        assert calls == [('x', 'y'), ('y', 'x')]
