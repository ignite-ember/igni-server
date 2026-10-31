"""The systems igni talks to, played by real HTTP servers on 127.0.0.1.

The rest of the suite stubs these at the client — `httpx.MockTransport`,
a patched `AsyncClient.get` — which is right for asserting what igni
sends and wrong for asking where it connects, because a mocked client
opens no socket at all. These peers are reached the way the real ones
are: name resolution, a TCP connection, HTTP. So a test that drives igni
against them exercises the same network path a deployment does, and the
egress guard sees every connection that path makes.

Each peer runs in its own thread so it answers whichever event loop the
app happens to be on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

from jose import jwt
from pydantic import BaseModel, ConfigDict

from app.services.identity.jwk import RsaKeyPair


class PeerRequest(BaseModel):
    """One request a peer received."""

    model_config = ConfigDict(frozen=True)

    method: str
    path: str
    body: bytes = b''


class PeerReply(BaseModel):
    status: int = 200
    body: bytes = b''
    content_type: str = 'application/json'

    @classmethod
    def json_of(cls, payload: object, *, status: int = 200) -> PeerReply:
        return cls(status=status, body=json.dumps(payload).encode())


class LoopbackPeer:
    """An HTTP server on an ephemeral loopback port. Subclasses answer."""

    def __init__(self) -> None:
        self.requests: list[PeerRequest] = []
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name=type(self).__name__, daemon=True)

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self._server.server_address[1]}'

    def __enter__(self) -> LoopbackPeer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def paths(self) -> list[str]:
        return [r.path for r in self.requests]

    def answer(self, request: PeerRequest) -> PeerReply:
        raise NotImplementedError

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get('content-length') or 0)
                request = PeerRequest(method=self.command, path=self.path, body=self.rfile.read(length))
                peer.requests.append(request)
                reply = peer.answer(request)
                self.send_response(reply.status)
                self.send_header('content-type', reply.content_type)
                self.send_header('content-length', str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            do_GET = do_POST = do_DELETE = do_PUT = _serve

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the base class's name
                return

        return Handler


class StubIdentityProvider(LoopbackPeer):
    """An OIDC provider: discovery, JWKS, and a token endpoint.

    The browser leg (`authorize`) is the test's to play: it reads the
    nonce out of the redirect igni issued and hands it to `will_sign_in`.
    """

    CLIENT_ID = 'igni'
    CLIENT_SECRET = 'loopback-secret'

    def __init__(self, *, email: str) -> None:
        super().__init__()
        self._email = email
        self._pair = RsaKeyPair.generate()
        self._nonce = ''

    def will_sign_in(self, *, nonce: str) -> str:
        """Primes the token endpoint and returns the code to present."""
        self._nonce = nonce
        return 'loopback-code'

    def answer(self, request: PeerRequest) -> PeerReply:
        path = urlsplit(request.path).path
        if path == '/.well-known/openid-configuration':
            return PeerReply.json_of(
                {
                    'issuer': self.url,
                    'authorization_endpoint': f'{self.url}/authorize',
                    'token_endpoint': f'{self.url}/token',
                    'jwks_uri': f'{self.url}/jwks',
                    'token_endpoint_auth_methods_supported': ['client_secret_post'],
                }
            )
        if path == '/jwks':
            return PeerReply.json_of({'keys': [self._pair.public_jwk(kid='idp-1').as_dict()]})
        if path == '/token' and parse_qs(request.body.decode()).get('code') == ['loopback-code']:
            return PeerReply.json_of({'access_token': 'unused', 'token_type': 'Bearer', 'id_token': self._id_token()})
        return PeerReply(status=404)

    def _id_token(self) -> str:
        now = datetime.now(timezone.utc)
        claims = {
            'iss': self.url,
            'aud': self.CLIENT_ID,
            'sub': 'loopback-person',
            'email': self._email,
            'email_verified': True,
            'name': 'Loopback Person',
            'nonce': self._nonce,
            'iat': now,
            'exp': now + timedelta(minutes=5),
        }
        return str(jwt.encode(claims, self._pair.private_pem(), algorithm='RS256', headers={'kid': 'idp-1'}))


class StubModelNode(LoopbackPeer):
    """An OpenAI-compatible inference node, as vLLM serves one."""

    MODEL = 'loopback-model'

    def answer(self, request: PeerRequest) -> PeerReply:
        path = urlsplit(request.path).path
        if path == '/v1/models':
            return PeerReply.json_of({'object': 'list', 'data': [{'id': self.MODEL, 'object': 'model'}]})
        if path == '/metrics':
            return PeerReply(body=b'vllm:num_requests_running 0\n', content_type='text/plain')
        if path == '/v1/chat/completions':
            if json.loads(request.body).get('stream'):
                return PeerReply(body=self._stream(), content_type='text/event-stream')
            return PeerReply.json_of(self._completion())
        return PeerReply(status=404)

    def _completion(self) -> dict[str, object]:
        return {
            'id': 'chatcmpl-loopback',
            'object': 'chat.completion',
            'model': self.MODEL,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4},
        }

    def _stream(self) -> bytes:
        chunks = [
            {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': 'hel'}}]},
            {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': 'lo'}}]},
            {
                'id': 'c',
                'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
            },
        ]
        return ''.join(f'data: {json.dumps(c)}\n\n' for c in chunks).encode() + b'data: [DONE]\n\n'


class StubModuleService(LoopbackPeer):
    """A paid module (CodeIndex, Artifacts) on igni's side of the wire.

    Accepts the erasure and organisation-deletion calls igni makes to a
    registered service, and nothing else.
    """

    def answer(self, request: PeerRequest) -> PeerReply:
        if urlsplit(request.path).path.startswith('/v1/igni/'):
            return PeerReply(status=204)
        return PeerReply(status=404)
