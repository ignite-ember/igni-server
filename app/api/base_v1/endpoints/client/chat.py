"""OpenAI-compatible ``/chat/completions`` and ``/chat/models``, proxied to the organisation's inference nodes.

Resolves the caller, hands the request to `ChatProxy`, and turns its
answer into a response. A refusal becomes HTTP through `AuthRefusals`
(the caller) and `ChatRefusals` (the proxy),
and comes back in the envelope an OpenAI SDK reads (``openai_errors``).
"""

from __future__ import annotations

from typing import Any, assert_never

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response

from app.api.base_v1.endpoints.client.client_connection import ClientConnection
from app.api.base_v1.endpoints.client.openai_errors import OpenAICompatibleRoute
from app.api.base_v1.endpoints.client.sse_response import StreamThatEnds
from app.api.base_v1.schema.chat import ChatCompletionRequest, ChatCompletionResponse, ChatModelsResponse
from app.api.base_v1.schema.openai_error import OpenAIErrorBody
from app.security.auth import oauth2_scheme
from app.services.ai.chat.answers import CallerLeft, CompletedAnswer, StreamedAnswer
from app.services.ai.chat.refusals import ChatRefusals
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.auth.auth_refusals import AuthRefusals

router = APIRouter(route_class=OpenAICompatibleRoute)

#: The refusals a completion can answer with, each in the OpenAI envelope. A node's
#: 4xx is passed through as is, so any 4xx can reach the caller.
_REFUSALS: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {
        'model': OpenAIErrorBody,
        'description': 'Missing, invalid or expired token, or a session that has been withdrawn',
    },
    status.HTTP_403_FORBIDDEN: {'model': OpenAIErrorBody, 'description': 'Not a member of the named organisation'},
    status.HTTP_404_NOT_FOUND: {'model': OpenAIErrorBody, 'description': 'No inference node serves the model'},
    status.HTTP_429_TOO_MANY_REQUESTS: {
        'model': OpenAIErrorBody,
        'description': 'Every node is at capacity and the queue is full, or the wait timed out; see Retry-After',
    },
    status.HTTP_502_BAD_GATEWAY: {'model': OpenAIErrorBody, 'description': 'No node produced a usable answer'},
    status.HTTP_503_SERVICE_UNAVAILABLE: {'model': OpenAIErrorBody, 'description': 'No inference node is configured'},
    status.HTTP_504_GATEWAY_TIMEOUT: {
        'model': OpenAIErrorBody,
        'description': 'The node stopped sending, or timed out, before the answer was complete',
    },
    '4XX': {
        'model': OpenAIErrorBody,
        'description': "A node's own 4xx (other than the above) passes through: the model refused the request",
    },
}


@router.get(
    '/chat/models',
    response_model=ChatModelsResponse,
    status_code=status.HTTP_200_OK,
    description='List the model identifiers this deployment serves. Not per-org: there is no per-org override.',
)
async def list_chat_models(
    request: Request,
    token: str = Depends(oauth2_scheme),
    services: ChatServices = Depends(chat_services),
) -> ChatModelsResponse:
    with AuthRefusals():
        await services.callers.assert_current_member(request=request, token=token)
    return ChatModelsResponse(models=await services.catalog.models())


@router.post(
    '/chat/completions',
    response_model=ChatCompletionResponse,
    status_code=status.HTTP_200_OK,
    description="OpenAI-compatible chat completion, proxied to the organisation's inference nodes",
    responses=_REFUSALS,
)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    token: str = Depends(oauth2_scheme),
    services: ChatServices = Depends(chat_services),
) -> ChatCompletionResponse | Response:
    connection = ClientConnection(request)
    with AuthRefusals():
        caller = await services.callers.resolve(request=request, token=token)
    with ChatRefusals():
        answer = await services.proxy.answer(caller=caller, body=body, presence=connection)
    match answer:
        case StreamedAnswer(stream=stream):
            return StreamThatEnds(stream.frames(), tails=services.tails)
        case CompletedAnswer(response=response):
            return response
        case CallerLeft():
            return connection.closed_response()
        case _:
            assert_never(answer)
