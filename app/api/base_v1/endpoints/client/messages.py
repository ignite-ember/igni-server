"""``POST /v1/messages``: Anthropic's Messages API, proxied to wherever Claude is served.

The endpoint igni's ``provider: anthropic`` points at with ``url: <igni
server>`` and ``api_key: cloud_token``. The caller signs in as it does on
``/chat/completions`` — igni server's own token — and never holds the
cloud's credentials: the backend's kind (``anthropic``, ``bedrock``,
``vertex_anthropic``, ``azure_anthropic``) decides how the server signs.

The token is read from ``Authorization: Bearer``, or from ``x-api-key``
when there is no Bearer — what an Anthropic SDK sends. igni's own client
sends both, the Bearer being the token.
"""

from __future__ import annotations

from typing import Any, assert_never

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from app.api.base_v1.endpoints.client.anthropic_errors import AnthropicCompatibleRoute
from app.api.base_v1.endpoints.client.client_connection import ClientConnection
from app.api.base_v1.endpoints.client.sse_response import StreamThatEnds
from app.api.base_v1.schema.messages import AnthropicErrorBody, MessagesRequest
from app.services.ai.chat.refusals import ChatRefusals
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.messages.calls import CallerHeaders
from app.services.ai.messages.proxy import CompletedMessage, MessagesCallerLeft, StreamedMessage
from app.services.auth.auth_refusals import AuthRefusals

router = APIRouter(route_class=AnthropicCompatibleRoute)

_REFUSALS: dict[int | str, dict[str, Any]] = {
    code: {'model': AnthropicErrorBody, 'description': description}
    for code, description in (
        (401, 'No token, or one that is not current'),
        (403, 'The caller may not use this model'),
        (404, 'No backend serves this model on this API'),
        (429, 'Every backend is busy; retry after Retry-After'),
        (502, 'The provider did not answer usefully'),
        (503, 'No backend is configured, or the server could not sign in to the provider'),
        (504, 'The provider stopped answering'),
    )
}


def caller_token(request: Request) -> str:
    """The caller's token: ``Authorization: Bearer``, else ``x-api-key``. A 401 when neither is there."""
    authorization = request.headers.get('authorization', '')
    scheme, _, bearer = authorization.partition(' ')
    if scheme.lower() == 'bearer' and bearer.strip():
        return bearer.strip()
    key = request.headers.get('x-api-key', '').strip()
    if key:
        return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Sign in to igni server: send its token as Authorization: Bearer, or as x-api-key',
        headers={'WWW-Authenticate': 'Bearer'},
    )


def caller_headers(request: Request) -> CallerHeaders:
    """The API version and beta features the caller asked for, passed on to the provider."""
    betas = [beta.strip() for beta in request.headers.get('anthropic-beta', '').split(',') if beta.strip()]
    return CallerHeaders(version=request.headers.get('anthropic-version') or None, betas=tuple(betas))


@router.post(
    '/messages',
    status_code=status.HTTP_200_OK,
    description="Anthropic's Messages API, proxied to the backends serving Claude: Anthropic, Bedrock, "
    'Vertex AI or Microsoft Foundry. Streams when the body says stream.',
    responses=_REFUSALS,
)
async def create_message(
    body: MessagesRequest,
    request: Request,
    token: str = Depends(caller_token),
    services: ChatServices = Depends(chat_services),
) -> Response:
    connection = ClientConnection(request)
    with AuthRefusals():
        caller = await services.callers.resolve(request=request, token=token)
    with ChatRefusals():
        answer = await services.messages.answer(
            caller=caller, body=body, asked=caller_headers(request), presence=connection
        )
    match answer:
        case StreamedMessage(stream=stream):
            return StreamThatEnds(stream.frames(), tails=services.tails)
        case CompletedMessage(message=message):
            return JSONResponse(message)
        case MessagesCallerLeft():
            return connection.closed_response()
        case _:
            assert_never(answer)
