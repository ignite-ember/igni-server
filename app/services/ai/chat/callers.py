"""Who is calling the chat proxy, and which model they get when they name none."""

from __future__ import annotations

from fastapi import Request

from app.security.producer import Producers
from app.services.ai.chat.schemas import ChatCaller
from app.services.auth.auth_service import AuthService
from app.services.portal.group_model_service import GroupModelService


class ChatCallerResolver:
    """A person's session, or an app's or a space's own token (docs/service-identity.md §11, §17)."""

    def __init__(
        self,
        *,
        producers: Producers,
        auth: type[AuthService],
        group_models: GroupModelService,
    ) -> None:
        self._producers = producers
        self._auth = auth
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
        user_id, org_id = await self.resolve_user_org(token=token, org_id=request.headers.get('x-org-id'))
        return ChatCaller(user_id=user_id, org_id=org_id)

    async def resolve_user_org(self, *, token: str, org_id: str | None) -> tuple[str, str | None]:
        """A person's `(user_id, org_id)`, asserting current membership."""
        return await self._auth.resolve_user_org(token, org_id=org_id)

    async def preferred_model(self, *, caller: ChatCaller, requested: str | None) -> str | None:
        """An explicit choice wins; otherwise a person's group's pin, if it set one."""
        if requested or caller.is_machine:
            return requested or None
        return await self._group_models.model_for_user(caller.user_id)
