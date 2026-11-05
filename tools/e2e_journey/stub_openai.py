"""A minimal OpenAI-compatible model server for the journey.

Standard library only, so it runs under any Python. Serves
``GET /v1/models``, ``POST /v1/chat/completions`` (streamed and not; it
never produces a tool call) and ``GET /metrics`` (empty). Every request
is appended to ``$STUB_LOG`` as a JSON line, so the journey can assert
what igni's chat proxy forwarded.

    STUB_LOG=run/logs/stub.jsonl python stub_openai.py --port 18101 --model e2e-stub-model
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time
import uuid

REPLY = os.environ.get('STUB_REPLY', 'STUB-REPLY: hello from the e2e stub model')
USAGE = {'prompt_tokens': 7, 'completion_tokens': 9, 'total_tokens': 16}


class StubModel(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    model = 'e2e-stub-model'
    log_path = ''

    def _record(self, entry: dict) -> None:
        if self.log_path:
            with open(self.log_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), 'application/json')

    def do_GET(self) -> None:
        self._record({'method': 'GET', 'path': self.path, 'auth': self.headers.get('Authorization')})
        if self.path.rstrip('/').endswith('/models'):
            return self._json(
                200, {'object': 'list', 'data': [{'id': self.model, 'object': 'model', 'owned_by': 'e2e'}]}
            )
        if self.path.startswith('/metrics'):
            return self._send(200, b'# stub\n', 'text/plain')
        if self.path in ('/health', '/v1/health'):
            return self._json(200, {'ok': True})
        self._json(404, {'error': {'message': 'not found'}})

    def do_POST(self) -> None:
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = {'_raw': raw.decode(errors='replace')}
        self._record({'method': 'POST', 'path': self.path, 'auth': self.headers.get('Authorization'), 'body': body})
        if not self.path.rstrip('/').endswith('/chat/completions'):
            return self._json(404, {'error': {'message': 'not found'}})
        completion_id = 'chatcmpl-' + uuid.uuid4().hex[:12]
        now = int(time.time())
        model = body.get('model') or self.model
        if not body.get('stream'):
            return self._json(
                200,
                {
                    'id': completion_id,
                    'object': 'chat.completion',
                    'created': now,
                    'model': model,
                    'choices': [
                        {'index': 0, 'message': {'role': 'assistant', 'content': REPLY}, 'finish_reason': 'stop'}
                    ],
                    'usage': USAGE,
                },
            )
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()

        def event(payload: dict) -> None:
            self.wfile.write(f'data: {json.dumps(payload)}\n\n'.encode())
            self.wfile.flush()

        def chunk(delta: dict, finish: str | None = None) -> None:
            event(
                {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': now,
                    'model': model,
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                }
            )

        chunk({'role': 'assistant', 'content': ''})
        for word in REPLY.split(' '):
            chunk({'content': word + ' '})
        chunk({}, 'stop')
        if (body.get('stream_options') or {}).get('include_usage'):
            event(
                {
                    'id': completion_id,
                    'object': 'chat.completion.chunk',
                    'created': now,
                    'model': model,
                    'choices': [],
                    'usage': USAGE,
                }
            )
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18101)
    parser.add_argument('--model', default='e2e-stub-model')
    # 0.0.0.0 when it runs as a pod (tools/airgap-rehearsal.sh); loopback
    # everywhere else.
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    StubModel.model = args.model
    StubModel.log_path = os.environ.get('STUB_LOG', '')
    ThreadingHTTPServer((args.host, args.port), StubModel).serve_forever()


if __name__ == '__main__':
    main()
