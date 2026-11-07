"""OpenAI-compatible ``/chat/completions`` and ``/chat/models``, proxied to the customer's nodes.

Resolves the caller, admits the request to a node (or refuses it), and
delegates to the chat proxy in ``services/ai/chat``. Every refusal comes
back in the envelope an OpenAI SDK reads (``openai_errors``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer

from app.api.base_v1.endpoints.client.client_connection import ClientConnection
from app.api.base_v1.endpoints.client.openai_errors import OpenAICompatibleRoute
from app.api.base_v1.endpoints.client.sse_response import StreamThatEnds
from app.api.base_v1.schema.chat import ChatCompletionRequest, ChatCompletionResponse, ChatModelsResponse
from app.services.ai.chat.errors import ChatRefused
from app.services.ai.chat.schemas import UpstreamChatRequest
from app.services.ai.chat.services import chat_services, ChatServices

router = APIRouter(route_class=OpenAICompatibleRoute)

_oauth2 = OAuth2PasswordBearer(tokenUrl='token')


def _refusal(exc: ChatRefused) -> HTTPException:
    """A refusal as HTTP; the only place a `Retry-After` header is made."""
    headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after is not None else None
    return HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers)


@router.get(
    '/chat/models',
    response_model=ChatModelsResponse,
    status_code=status.HTTP_200_OK,
    description='List the model identifiers this deployment serves. Not per-org: there is no per-org override.',
)
async def list_chat_models(
    request: Request,
    token: str = Depends(_oauth2),
    services: ChatServices = Depends(chat_services),
) -> ChatModelsResponse:
    # Asserts a *current* member, though nothing per-org filters the list.
    await services.callers.resolve_user_org(token=token, org_id=request.headers.get('x-org-id'))
    return ChatModelsResponse(models=await services.catalog.models())


@router.post(
    '/chat/completions',
    response_model=ChatCompletionResponse,
    status_code=status.HTTP_200_OK,
    description='OpenAI-compatible chat completion (proxied via AI key pool)',
    responses={429: {'description': 'All keys for this pool are at capacity'}},
)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    token: str = Depends(_oauth2),
    services: ChatServices = Depends(chat_services),
) -> ChatCompletionResponse | Response:
    connection = ClientConnection(request)
    caller = await services.callers.resolve(request=request, token=token)
    model = await services.callers.preferred_model(caller=caller, requested=body.model)
    try:
        lease = await connection.unless_it_closes(services.admission().admit(caller=caller, model=model))
        if lease is None:
            return connection.closed_response()
        upstream_request = UpstreamChatRequest.for_upstream(body, model=model or lease.model)
        ctx = services.context(caller=caller, lease=lease, request=upstream_request)
        if body.stream:
            return StreamThatEnds(services.streamer.open(ctx).frames(), tails=services.tails)
        answer = await connection.unless_it_closes(services.completions.complete(ctx))
    except ChatRefused as exc:
        raise _refusal(exc) from exc
    if answer is None:
        return connection.closed_response()
    return answer
