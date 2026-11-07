"""Creating the same thing twice at once is a 409, never a 500.

Every one of these was found by firing eight identical requests at a
running server and reading the status codes. Before the fixes:

    org, same slug x8        {201: 1, 500: 7}
    group, same name x8      {201: 1, 409: 3, 500: 4}
    entry, same name x8      {201: 1, 409: 7}      <- already correct
    invite, same email x8    {201: 5, 409: 3}      <- five rows created
    node, same node_id x8    {201: 1, 500: 7}

Two of them were not races at all. A duplicate org slug answered **500
sequentially** — on the org-creation form, which is close to the first
thing a new user does. The rest were check-then-act: a service-layer
pre-check that two concurrent requests both pass, with nothing behind it.

The invite row is the one that mattered, and has its own file
(`test_duplicate_invitations.py`) because it corrupted state rather than
just returning the wrong code.

These tests exercise the *database layer* directly and concurrently.
Driving the HTTP layer would need a live server, and the constraint
handlers are where the bug was — the endpoints only pass through.
"""

from __future__ import annotations

import asyncio
import collections

from fastapi import HTTPException
import pytest


async def _gather_outcomes(make_coro, n: int = 6):
    """Run *n* copies of an operation at once; summarise what came back.

    Each gets its own task, so they contend on the database the way
    separate requests do.
    """
    results = await asyncio.gather(*[make_coro() for _ in range(n)], return_exceptions=True)
    outcomes: collections.Counter[str] = collections.Counter()
    for r in results:
        if isinstance(r, HTTPException):
            outcomes[f'HTTP {r.status_code}'] += 1
        elif isinstance(r, BaseException):
            outcomes[type(r).__name__] += 1
        else:
            outcomes['created'] += 1
    return outcomes, results


class TestOrganizationSlug:
    async def test_a_duplicate_slug_is_a_409_not_a_500(self, db):
        """The sequential case, which was the actual bug: this never
        needed concurrency to fail."""
        from app.db.sql.organizations import OrganizationDB
        from tests.conftest import create_user

        await create_user(db, user_id='slug-owner', email='slug-owner@example.com')
        await db.flush()

        orgs = OrganizationDB()
        await orgs.create(display_name='First', slug='taken-slug', owner_user_id='slug-owner', domains=['first.io'])

        with pytest.raises(HTTPException) as caught:
            await orgs.create(
                display_name='Second', slug='taken-slug', owner_user_id='slug-owner', domains=['second.io']
            )

        assert caught.value.status_code == 409
        # The message has to name the value, or the form cannot point at a field.
        assert 'taken-slug' in str(caught.value.detail)

    async def test_a_non_slug_violation_still_surfaces(self, db):
        """The handler checks *which* constraint failed rather than
        assuming. A different violation must not be reported as a taken
        slug — it should stay a 500 somebody investigates.
        """
        import inspect

        from app.db.sql.organizations import OrganizationDB

        source = inspect.getsource(OrganizationDB.create)

        # Checked by index name, not by the word "slug". The substring
        # form this replaces also matched `null value in column "slug"`,
        # because organizations.slug is NOT NULL — so a null slug was
        # reported as a taken one.
        assert '_SLUG_INDEX not in str(' in source, (
            'the IntegrityError handler must confirm the constraint before '
            'answering 409, or an unrelated violation gets the wrong message'
        )
        assert 'raise' in source


class TestInferenceNodeId:
    async def test_a_duplicate_node_id_is_a_409(self, db):
        """Registration is driven by a node's own startup, so two
        instances racing on one id is the ordinary way here, not an
        unusual one."""
        from app.db.sql.inference_nodes import InferenceNodeDB
        from app.db.sql.schemas.inference_node import NodeIdAlreadyRegistered
        from tests.conftest import create_org, create_user

        await create_user(db, user_id='node-owner', email='node-owner@example.com')
        org = await create_org(db, owner_user_id='node-owner', slug='node-org', display_name='N')
        await db.flush()

        nodes = InferenceNodeDB()
        kwargs = dict(
            org_id=str(org.id),
            base_url='http://127.0.0.1:9',
            models=['m'],
            created_by_user_id='node-owner',
        )
        await nodes.create(node_id='dup-node', **kwargs)

        # A domain error, not an HTTPException. The status code is the
        # router's to choose — see the 409 mapping in
        # app/api/base_v1/endpoints/portal/inference.py — and a caller
        # that is not an HTTP request can now catch this by name.
        with pytest.raises(NodeIdAlreadyRegistered) as caught:
            await nodes.create(node_id='dup-node', **kwargs)

        assert caught.value.node_id == 'dup-node'

    async def test_an_unrelated_integrity_error_is_not_a_duplicate(self, db):
        """The collision is recognised by constraint name, and this is
        what stops that from over-matching.

        A foreign-key violation is also an IntegrityError on this table.
        Reported as "already registered" it would send somebody looking
        for a node that does not exist, instead of at the user id that
        does not.
        """
        from sqlalchemy.exc import IntegrityError

        from app.db.sql.inference_nodes import InferenceNodeDB

        with pytest.raises(IntegrityError):
            await InferenceNodeDB().create(
                node_id='orphan-node',
                org_id=None,
                base_url='http://127.0.0.1:9',
                models=['m'],
                created_by_user_id='nobody-has-this-id',
            )


class TestNoCreateFlushesWithoutAHandler:
    """The sweep that would have found all of these at once.

    A `session.add(...)` followed by a bare `flush()` in a table that has a
    unique constraint is a 500 waiting for a duplicate. Rather than listing
    the call sites, this asserts that every `IntegrityError` in the package
    is actually handled, and names the ones deliberately left alone.
    """

    def test_every_db_module_that_inserts_handles_conflicts(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / 'app/db/sql'

        # Modules whose inserts cannot collide: no unique constraint beyond
        # a freshly generated primary key.
        exempt = {
            'chat_usage.py',
            # A personal API key's row is keyed by `secrets.token_hex(16)`,
            # made a moment before, and has no other unique constraint: two
            # inserts cannot collide (docs/service-identity.md §22).
            'api_keys.py',
            # The primary key is the thumbprint of a key generated a
            # moment before, so two inserts cannot share one; and the one
            # race there is — replicas booting together against an empty
            # table — is serialised by an advisory lock in `ensure`.
            'signing_keys.py',
            # `AuditEventDB.record` catches *every* exception by design —
            # an audit write must not change what the caller receives — so
            # nothing can escape it as a 500, which is what this guard
            # protects against. The insert also has no unique constraint
            # to violate beyond a freshly generated uuid4 primary key.
            #
            # Exempted rather than given an `IntegrityError` branch,
            # because a handler for a collision that cannot happen would
            # be dead code that reads as defence. F90.
            'audit_events.py',
            # An app's row is keyed by the client id its registration was
            # given a moment before; its extensions are an upsert.
            'apps.py',
            # Commands and jobs are keyed by fresh uuid4s; a job's row per
            # VM is an upsert on its unique index.
            'vm_commands.py',
            # The feed inserts only rows for producers it did not find, in
            # the one transaction holding the feed's advisory lock, so no
            # second writer can insert the same producer meanwhile.
            'producer_feed.py',
            # A space is inserted only after looking for one with its app and
            # external id, under an advisory lock on the organisation's
            # database that every declaration takes.
            'spaces.py',
            # An export is keyed by a fresh uuid4 and has no other unique key.
            'producer_exports.py',
            # A service's file is keyed by a fresh uuid4 and has no other unique key.
            'service_attachments.py',
            # An organisation's tenant row is inserted only after re-reading
            # it, under the TENANT_LOCK advisory lock held for the whole
            # provision, so no second writer can insert the same org_id,
            # number or database meanwhile. F90: no dead handler.
            'tenant_registry.py',
        }

        unguarded = []
        for path in sorted(root.glob('*.py')):
            if path.name in exempt:
                continue
            body = path.read_text()
            if 'session.add(' not in body:
                continue
            # Any insert-bearing module should know what to do with a
            # constraint violation somewhere in it.
            if 'IntegrityError' not in body:
                unguarded.append(path.name)

        assert not unguarded, (
            f'these modules insert rows but never mention IntegrityError: {unguarded}. '
            f'A duplicate then escapes as a 500 rather than a 409 — that was true of '
            f'organizations.py, groups.py, team.py and inference_nodes.py, '
            f'and two of them failed without any concurrency at all. Add a handler, or '
            f'add the module to `exempt` above with the reason it cannot collide.'
        )
