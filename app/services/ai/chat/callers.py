"""Who is calling the chat proxy, and which model they get when they name none."""

from __future__ import annotations

from fastapi import Request

from app.security.producer import Producers
from app.services.ai.chat.schemas import ChatCaller
from app.services.auth.org_context import OrgContexts
from app.services.portal.group_model_service import GroupModelService


class ChatCallerResolver:
    """A person's session, or an app's or a space's own token (docs/service-identity.md §11, §17)."""

    def __init__(
        self,
        *,
        producers: Producers,
        orgs: OrgContexts,
        group_models: GroupModelService,
    ) -> None:
        self._producers = producers
        self._orgs = orgs
        self._group_models = group_models

    async def resolve(self, *, request: Request, token: str) -> ChatCaller:
        """The caller; a producer's usage is its organisation's, as `app:<id>` / `space:<id>` with the app."""
        producer = await self._producers.machine(request, token)
        if producer is not None:
            return ChatCaller(
                user_id=f'{producer.kind}:{producer.id}',
                org_id=producer.org_id,
                app_id=producer.app_id or producer.id,
                is_machine=True,
            )
        return await self._person(token=token, org_id=self._org_header(request))

    async def assert_current_member(self, *, request: Request, token: str) -> None:
        """Refuse anyone who is not a current member of the organisation they name.

        The model list is deployment-wide, not per-org filtered, so nothing
        here is used: the call only asserts current membership, so that the
        list needs a current member rather than anybody holding a token.
        """
        await self._person(token=token, org_id=self._org_header(request))

    async def _person(self, *, token: str, org_id: str | None) -> ChatCaller:
        """A person, asserting current membership of *org_id*; raises `AuthRefused` otherwise."""
        context = await self._orgs.resolve(token, org_id=org_id)
        return ChatCaller(user_id=context.user_id, org_id=context.org_id)

    @staticmethod
    def _org_header(request: Request) -> str | None:
        """The organisation the caller names, if any."""
        return request.headers.get('x-org-id')

    async def preferred_model(self, *, caller: ChatCaller, requested: str | None) -> str | None:
        """An explicit choice wins; otherwise a person's group's pin, if it set one."""
        if requested or caller.is_machine:
            return requested or None
        return await self._group_models.model_for_user(caller.user_id)
