"""`ChatCallerResolver`: who a chat request is for, and which model they get when they name none.

A machine's token is tried first, then a person's session in the
organisation the `x-org-id` header names. A space's usage is its app's;
an empty model is no model, and only a person is given their group's.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.requests import Request

from app.security.producer import ProducerCaller
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.schemas import ChatCaller
from app.services.auth.org_context import OrgContext


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({'type': 'http', 'method': 'POST', 'path': '/chat/completions', 'headers': raw})


def _resolver(*, machine: ProducerCaller | None = None, group_model: str | None = 'group-model'):
    orgs = SimpleNamespace(resolve=AsyncMock(return_value=OrgContext(user_id='ada', org_id='org-9')))
    group_models = SimpleNamespace(model_for_user=AsyncMock(return_value=group_model))
    resolver = ChatCallerResolver(
        producers=SimpleNamespace(machine=AsyncMock(return_value=machine)),  # type: ignore[arg-type]
        orgs=orgs,  # type: ignore[arg-type]
        group_models=group_models,  # type: ignore[arg-type]
    )
    return resolver, orgs, group_models


class TestResolvingTheCaller:
    async def test_a_space_with_an_app_is_billed_to_its_app(self):
        space = ProducerCaller(kind='space', id='sp-1', org_id='org-1', app_id='app-1')
        resolver, orgs, _ = _resolver(machine=space)

        caller = await resolver.resolve(request=_request(), token='t')

        assert caller == ChatCaller(user_id='space:sp-1', org_id='org-1', app_id='app-1', is_machine=True)
        orgs.resolve.assert_not_awaited()

    async def test_a_machine_with_no_app_of_its_own_is_its_own_app(self):
        app = ProducerCaller(kind='app', id='app-7', org_id='org-1')
        resolver, _, _ = _resolver(machine=app)

        caller = await resolver.resolve(request=_request(), token='t')

        assert caller == ChatCaller(user_id='app:app-7', org_id='org-1', app_id='app-7', is_machine=True)

    async def test_a_person_is_resolved_in_the_organisation_they_name(self):
        resolver, orgs, _ = _resolver()

        caller = await resolver.resolve(request=_request({'X-Org-Id': 'org-9'}), token='session')

        assert caller == ChatCaller(user_id='ada', org_id='org-9')
        orgs.resolve.assert_awaited_once_with('session', org_id='org-9')

    async def test_a_person_who_names_no_organisation_is_resolved_with_none(self):
        resolver, orgs, _ = _resolver()

        await resolver.resolve(request=_request(), token='session')

        orgs.resolve.assert_awaited_once_with('session', org_id=None)

    async def test_the_model_list_asserts_membership_of_the_named_organisation(self):
        resolver, orgs, _ = _resolver()

        await resolver.assert_current_member(request=_request({'x-org-id': 'org-3'}), token='session')

        orgs.resolve.assert_awaited_once_with('session', org_id='org-3')


class TestThePreferredModel:
    PERSON = ChatCaller(user_id='ada', org_id='org-1')
    MACHINE = ChatCaller(user_id='app:a', org_id='org-1', app_id='a', is_machine=True)

    async def test_a_machine_that_sends_an_empty_model_asks_for_none(self):
        resolver, _, group_models = _resolver()

        assert await resolver.preferred_model(caller=self.MACHINE, requested='') is None
        group_models.model_for_user.assert_not_awaited()

    async def test_a_person_who_sends_an_empty_model_gets_their_groups(self):
        resolver, _, group_models = _resolver()

        assert await resolver.preferred_model(caller=self.PERSON, requested='') == 'group-model'
        group_models.model_for_user.assert_awaited_once_with('ada')

    async def test_a_named_model_is_asked_for_as_named(self):
        resolver, _, group_models = _resolver()

        assert await resolver.preferred_model(caller=self.MACHINE, requested='m-2') == 'm-2'
        group_models.model_for_user.assert_not_awaited()
