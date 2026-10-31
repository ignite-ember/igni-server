"""Two extensions that want one thing: the higher in priority has it, and nothing waits.

Two extensions may both want the same prompt section, the same whole
prompt, a slash command or an anchor. Neither gets it by arriving first,
and nobody decides it case by case: a super admin keeps one priority order
of extensions on the Services page, the higher one wins every collision,
and every version is distributed as soon as its service is approved. The
order travels with the distribution listing, because every machine
settles collisions by it.
"""

from __future__ import annotations

import pytest

from app.db.sql.user import UserDB
from tests.conftest import auth_header, create_user, make_token
from tests.entitled_person import EntitledPerson
from tests.registered_service import ExtensionBundle, RegisteredService

pytestmark = pytest.mark.anyio


def _manifest(extension_id: str, version: str = '1.0.0', **contributes: object) -> dict[str, object]:
    return {'id': extension_id, 'version': version, 'name': extension_id, 'contributes': contributes}


WORKFLOW = {'edits': [{'target': 'main', 'replace': 'workflow', 'source': 'file', 'from': 'prompts/workflow.md'}]}


async def _upload(client, service: RegisteredService, manifest: dict[str, object]):
    bundle = ExtensionBundle.of(manifest)
    return await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': bundle.signed_by(
                service, extension=str(manifest['id']), version=str(manifest['version'])
            ),
        },
    )


async def _ship(client, service: RegisteredService, manifest: dict[str, object]):
    response = await _upload(client, service, manifest)
    assert response.status_code == 200, response.text
    return response


async def _service(name: str, *, state: str = 'approved') -> RegisteredService:
    service = RegisteredService(name=name, base_url=f'https://{name.lower()}.example.com')
    await service.approve(state=state)
    return service


async def _admin(db) -> dict[str, str]:
    user = await create_user(db, email='owner@example.com', is_super_admin=True)
    return auth_header(make_token(str(user.user_id)))


#: Every extension these tests ship, on for the person's group.
EVERY_EXTENSION = ('acme.review', 'acme.lint', 'acme.docs')


async def _person(db) -> dict[str, str]:
    return await EntitledPerson(db).headers(*EVERY_EXTENSION)


async def _listed(client, headers) -> dict[str, object]:
    return (await client.get('/extensions', headers=headers)).json()


async def _review(client, service: RegisteredService, admin: dict[str, str]) -> dict[str, object]:
    detail = (await client.get(f'/portal/clients/{service.client_id}', headers=admin)).json()
    return detail['extension_detail']['review']


class TestNothingWaits:
    async def test_a_colliding_upload_is_distributed_at_once(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review', prompt=WORKFLOW))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))

        listed = await _listed(client, await _person(db))
        assert [e['extension_id'] for e in listed['extensions']] == ['acme.lint', 'acme.review']
        assert listed['priority'] == ['acme.review', 'acme.lint']

    async def test_a_new_version_replaces_the_one_before(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review', prompt=WORKFLOW))
        # The same grants in both versions: a version asking for more is
        # held for another reason (`test_a_wider_update_waits_for_a_super_admin.py`).
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))
        await _ship(client, second, _manifest('acme.lint', '1.1.0', prompt=WORKFLOW))

        listed = await _listed(client, await _person(db))
        assert {e['extension_id']: e['version'] for e in listed['extensions']} == {
            'acme.lint': '1.1.0',
            'acme.review': '1.0.0',
        }

    async def test_approving_a_colliding_service_distributes_its_extension(self, client, db):
        first, second = await _service('Review'), await _service('Lint', state='pending')
        await _ship(client, first, _manifest('acme.review', prompt=WORKFLOW))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))

        assert (
            await client.post(f'/portal/clients/{second.client_id}/approve', headers=await _admin(db))
        ).status_code == 200
        listed = await _listed(client, await _person(db))
        assert [e['extension_id'] for e in listed['extensions']] == ['acme.lint', 'acme.review']


class TestTheServicesPageShowsWhoWins:
    async def test_the_higher_priority_wins_and_the_page_says_so_on_both_sides(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review', prompt=WORKFLOW))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))
        admin = await _admin(db)

        lint = await _review(client, second, admin)
        assert lint['collisions'] == [
            {
                'claim': 'prompt:main#workflow',
                'extensions': ['acme.lint', 'acme.review'],
                'winner': 'acme.review',
                'losers': ['acme.lint'],
            }
        ]
        [lost] = lint['switched_off']
        assert lost['claim'] == 'prompt:main#workflow'
        assert lost['reason'].startswith('lower priority than acme.review')
        assert (await _review(client, first, admin))['switched_off'] == []

    async def test_reordering_moves_the_win(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review', commands=[{'command': '/review', 'desc': 'x'}]))
        await _ship(client, second, _manifest('acme.lint', commands=[{'command': '/review', 'desc': 'x'}]))
        admin = await _admin(db)

        await client.put('/portal/extension-priority', headers=admin, json={'order': ['acme.lint', 'acme.review']})

        [collision] = (await _review(client, first, admin))['collisions']
        assert (collision['claim'], collision['winner'], collision['losers']) == (
            'command:/review',
            'acme.lint',
            ['acme.review'],
        )

    async def test_a_pending_service_s_collision_is_shown_before_it_reaches_anybody(self, client, db):
        first, second = await _service('Review'), await _service('Lint', state='pending')
        await _ship(client, first, _manifest('acme.review', prompt=WORKFLOW))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))

        [collision] = (await _review(client, second, await _admin(db)))['collisions']
        assert collision['winner'] == 'acme.review'

    async def test_an_exclusive_override_above_an_editor_switches_its_edits_off(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        exclusive = {
            'overrides': [
                {'target': 'main', 'sections': ['identity'], 'exclusive': True, 'source': 'file', 'from': 'main.md'}
            ]
        }
        await _ship(client, first, _manifest('acme.review', prompt=exclusive))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))
        admin = await _admin(db)

        [collision] = (await _review(client, second, admin))['collisions']
        assert (collision['claim'], collision['winner'], collision['losers']) == (
            'prompt:main',
            'acme.review',
            ['acme.lint'],
        )

        await client.put('/portal/extension-priority', headers=admin, json={'order': ['acme.lint', 'acme.review']})
        review = await _review(client, first, admin)
        [off] = review['switched_off']
        assert off['claim'] == 'prompt:main'
        assert off['reason'].startswith('lower priority than acme.lint, which edits main')

    async def test_additions_never_collide(self, client, db):
        added = {
            'edits': [{'target': 'main', 'insert': 'notes', 'after': 'workflow', 'source': 'file', 'from': 'a.md'}]
        }
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review', prompt=added))
        await _ship(client, second, _manifest('acme.lint', prompt=added))

        assert (await _review(client, second, await _admin(db)))['collisions'] == []

    async def test_a_target_this_igni_does_not_have_is_pointed_out(self, client, db):
        service = await _service('Review')
        nowhere = {'edits': [{'target': 'main', 'replace': 'nonesuch', 'source': 'file', 'from': 'a.md'}]}
        await _ship(client, service, _manifest('acme.review', prompt=nowhere))

        assert (await _review(client, service, await _admin(db)))['unknown_targets'] == ['main#nonesuch']

    async def test_an_override_s_drops_are_shown_on_both_sides(self, client, db):
        """Not a collision, but a super admin sees which edits an override
        would leave out, on the override's page and the edit's."""
        first, second = await _service('Review'), await _service('Lint')
        override = {'overrides': [{'target': 'main', 'sections': ['identity'], 'source': 'file', 'from': 'main.md'}]}
        await _ship(client, first, _manifest('acme.review', prompt=override))
        await _ship(client, second, _manifest('acme.lint', prompt=WORKFLOW))
        admin = await _admin(db)

        [dropped] = (await _review(client, second, admin))['dropped']
        assert dropped['claim'] == 'prompt:main#workflow' and dropped['by'] == 'acme.review'
        assert list((await _review(client, first, admin))['drops_others']) == ['acme.lint']


class TestRefusedAtUpload:
    async def test_one_of_igni_s_own_commands_is_refused(self, client, db):
        """Not a collision between extensions: igni answers `/clear` first, so
        the extension's would never run."""
        service = await _service('Review')
        response = await _upload(
            client, service, _manifest('acme.review', commands=[{'command': '/clear', 'desc': 'x'}])
        )
        assert response.status_code == 400
        assert "/clear is igni's own" in response.json()['detail']

    async def test_hooks_without_a_process_are_refused(self, client, db):
        """An endpoint is never asked, and its silence would block every
        fail-closed event."""
        service = await _service('Review')
        manifest = {
            **_manifest('acme.review'),
            'endpoint': {'url': 'https://review.example.com/mcp'},
            'permissions': {'hooks': ['PreToolUse']},
        }
        response = await _upload(client, service, manifest)
        assert response.status_code == 400
        assert 'needs a process igni starts' in response.json()['detail']


class TestThePriorityOrder:
    async def test_unset_it_is_the_order_services_were_approved_in(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, second, _manifest('acme.lint'))
        await _ship(client, first, _manifest('acme.review'))

        assert (await _listed(client, await _person(db)))['priority'] == ['acme.review', 'acme.lint']

    async def test_a_super_admin_sets_it_and_every_machine_receives_it(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review'))
        await _ship(client, second, _manifest('acme.lint'))
        admin = await _admin(db)

        response = await client.put(
            '/portal/extension-priority', headers=admin, json={'order': ['acme.lint', 'acme.review']}
        )

        assert response.status_code == 200, response.text
        view = response.json()
        assert [e['extension_id'] for e in view['entries']] == ['acme.lint', 'acme.review']
        assert view['set_by'] == 'owner@example.com'
        assert (await _listed(client, await _person(db)))['priority'] == ['acme.lint', 'acme.review']

    async def test_an_order_names_every_extension_once(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review'))
        await _ship(client, second, _manifest('acme.lint'))
        admin = await _admin(db)

        for order in (['acme.lint'], ['acme.lint', 'acme.lint', 'acme.review'], ['acme.lint', 'x.y']):
            response = await client.put('/portal/extension-priority', headers=admin, json={'order': order})
            assert response.status_code == 400, order

    async def test_one_set_before_an_extension_arrived_puts_it_last(self, client, db):
        first, second = await _service('Review'), await _service('Lint')
        await _ship(client, first, _manifest('acme.review'))
        await _ship(client, second, _manifest('acme.lint'))
        admin = await _admin(db)
        await client.put('/portal/extension-priority', headers=admin, json={'order': ['acme.lint', 'acme.review']})

        third = await _service('Docs')
        await _ship(client, third, _manifest('acme.docs'))

        assert (await _listed(client, await _person(db)))['priority'] == ['acme.lint', 'acme.review', 'acme.docs']

    async def test_who_set_it_is_read_from_their_account_and_gone_with_it(self, client, db):
        """Their address is shown with the order, and never copied."""
        first = await _service('Review')
        await _ship(client, first, _manifest('acme.review'))
        boss = await create_user(db, email='boss@example.com', is_super_admin=True)
        boss_headers = auth_header(make_token(str(boss.user_id)))
        await client.put('/portal/extension-priority', headers=boss_headers, json={'order': ['acme.review']})
        admin = await _admin(db)
        assert (await client.get('/portal/extension-priority', headers=admin)).json()['set_by'] == 'boss@example.com'

        await UserDB().delete(str(boss.user_id))

        assert (await client.get('/portal/extension-priority', headers=admin)).json()['set_by'] == (
            'a former super admin'
        )

    async def test_only_a_super_admin_sets_it(self, client, db):
        response = await client.put('/portal/extension-priority', headers=await _person(db), json={'order': []})
        assert response.status_code == 403
