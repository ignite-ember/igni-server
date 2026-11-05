"""What every step of the journey shares: the stack's addresses, the people
signed in so far, and the record of each call and its answer."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback
from typing import Any, Literal

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from tools.e2e_journey.people import LOCAL_PASSWORD

HERE = Path(__file__).resolve().parent

Status = Literal['PASS', 'FAIL', 'SKIP', 'N/A']


class Options(BaseModel):
    """The command line: where the stack is, and where to write."""

    api: str
    portal: str
    portal_dir: str
    stub: str
    domain: str
    sso_label: str
    scim_token: str
    api_log: str
    stub_log: str
    service_log: str = ''
    service_name: str = 'Team Notes'
    extension_id: str = 'example.teamnotes'
    igni_python: str
    igni_bin: str
    work: str
    out: str


class StepFailed(Exception):
    """The step's check did not hold. The message is the evidence."""


class Outcome(BaseModel):
    """What a step returns when it is not a plain PASS."""

    status: Status
    detail: str


class StepRecord(BaseModel):
    step: str
    area: str
    status: Status = 'PASS'
    detail: str = ''
    calls: list[str] = Field(default_factory=list)
    seconds: float = 0.0


class State(BaseModel):
    """Who is signed in, and what the journey has made so far."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ada_email: str = ''
    ada: str = ''
    ada_id: str = ''
    org: str = ''
    max_email: str = ''
    max: str = ''
    max_id: str = ''
    max_cli: str = ''
    group: str = ''
    app_id: str = ''
    app_key: Any = None
    app_kid: str = ''
    app_tok: str = ''
    vmreq_id: str = ''
    svc: str = ''
    audit_count: int = 0
    cli_warnings: list[str] = Field(default_factory=list)


class Step(BaseModel):
    """One row of the results table."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    area: str
    run: Callable[[Journey], str | Outcome | None]


class Journey:
    """The stack, the state, and the record. One per run."""

    def __init__(self, options: Options) -> None:
        self.o = options
        self.api = options.api.rstrip('/')
        self.http = httpx.Client(base_url=self.api, timeout=30)
        self.state = State()
        self.results: list[StepRecord] = []
        self._current: StepRecord | None = None

    # ── Running and recording ─────────────────────────────────────────

    def run(self, steps: list[Step]) -> None:
        for step in steps:
            started = time.time()
            record = StepRecord(step=step.name, area=step.area)
            self._current = record
            try:
                out = step.run(self)
                if isinstance(out, Outcome):
                    record.status, record.detail = out.status, out.detail
                elif isinstance(out, str):
                    record.detail = out
            except StepFailed as exc:
                record.status, record.detail = 'FAIL', str(exc)
            except Exception as exc:  # noqa: BLE001 — a broken step is a FAIL row, not the end of the run
                record.status = 'FAIL'
                record.detail = f'{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}'
            record.seconds = round(time.time() - started, 2)
            self.results.append(record)
            print(f'[{record.status:4}] {record.step} — {record.detail[:200]}', flush=True)

    def note(self, line: str) -> None:
        """Add a line to the current step's evidence."""
        assert self._current is not None
        self._current.calls.append(line)

    def write_report(self) -> bool:
        """Write results.json and results.md; whether every step held."""
        out = Path(self.o.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'results.json').write_text(
            json.dumps(
                {
                    'domain': self.o.domain,
                    'cli_warnings': self.state.cli_warnings,
                    'results': [r.model_dump() for r in self.results],
                },
                indent=2,
            )
        )
        lines = ['| # | Step | Result | Detail |', '|---|---|---|---|']
        for r in self.results:
            number, _, title = r.step.partition(' ')
            detail = r.detail[:300].replace('|', '/').replace('\n', ' ')
            lines.append(f'| {number} | {title} | {r.status} | {detail} |')
        (out / 'results.md').write_text('\n'.join(lines) + '\n')
        print('\n'.join(lines))
        print(f'\nwrote {out / "results.json"}')
        return not any(r.status == 'FAIL' for r in self.results)

    # ── Talking to the API ────────────────────────────────────────────

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        org: str | None = None,
        expect: tuple[int, ...] = (200, 201, 202, 204),
        **kw: Any,
    ) -> httpx.Response:
        headers = kw.pop('headers', {})
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if org:
            headers['X-Org-Id'] = org
        r = self.http.request(method, path, headers=headers, **kw)
        body = kw.get('json') or kw.get('data')
        shown = f' {json.dumps(body)[:160]}' if body else ''
        self.note(f'{method} {path}{shown} -> {short(r, 160)}')
        if expect and r.status_code not in expect:
            raise StepFailed(f'{method} {path} -> {short(r)}')
        return r

    def admin(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """A call as the organisation's admin, in the organisation."""
        return self.call(method, path, token=self.state.ada, org=self.state.org, **kw)

    def audit(self) -> list[dict]:
        j = self.admin('GET', '/v1/portal/audit-events', params={'limit': 500}).json()
        return j.get('events') or j.get('items') or j

    def team(self) -> list[dict]:
        return self.admin('GET', '/v1/portal/team').json()['members']

    # ── One-time codes (LOG_SIGN_IN_CODES) ───────────────────────────

    def log_size(self) -> int:
        return Path(self.o.api_log).stat().st_size

    def code_from_log(self, kind: str, email: str, after: int, timeout: float = 10) -> str:
        pattern = re.compile(rf'{kind} code for {re.escape(email)} is (\d+)')
        end = time.time() + timeout
        while time.time() < end:
            found = pattern.findall(Path(self.o.api_log).read_text(errors='replace')[after:])
            if found:
                return found[-1]
            time.sleep(0.3)
        raise StepFailed(f"no '{kind} code for {email}' in {self.o.api_log}")

    def sign_up(self, first: str, last: str, email: str) -> str:
        """Email, a one-time code from the log, a password: a new account's token."""
        position = self.log_size()
        self.call('POST', '/v1/send-sign-up-code', json={'email': email})
        code = self.code_from_log('sign-up', email, position)
        r = self.call(
            'POST',
            '/v1/sign-up',
            json={'first_name': first, 'last_name': last, 'email': email, 'code': code, 'password': LOCAL_PASSWORD},
        )
        return r.json()['access_token']

    # ── The browser ──────────────────────────────────────────────────

    def browser(self, command: str, *, timeout: float = 150, **args: str) -> dict:
        """Run one ``browser.mjs`` command; its JSON answer."""
        cmd = ['node', str(HERE / 'browser.mjs'), command]
        for key, value in args.items():
            cmd += [f'--{key.replace("_", "-")}', value]
        p = subprocess.run(
            cmd, env={**os.environ, 'PORTAL_DIR': self.o.portal_dir}, capture_output=True, text=True, timeout=timeout
        )
        shown = ' '.join(c if not c.startswith('eyJ') else '<token>' for c in cmd[2:])
        last = (p.stdout.strip().splitlines() or [''])[-1]
        try:
            answer = json.loads(last)
        except ValueError:
            answer = {'ok': False, 'error': f'rc={p.returncode} stdout={p.stdout[-300:]} stderr={p.stderr[-300:]}'}
        logged = {k: ('<token>' if k == 'token' and v else v) for k, v in answer.items()}
        self.note(f'$ node browser.mjs {shown} -> rc={p.returncode} {json.dumps(logged)[:400]}')
        return answer

    def shot(self, name: str) -> str:
        return str(Path(self.o.out) / 'shots' / f'{name}.png')

    # ── The CLI ──────────────────────────────────────────────────────

    @property
    def project(self) -> Path:
        return Path(self.o.work) / 'project'

    @property
    def igni_home(self) -> Path:
        return Path(self.o.work) / 'home' / '.igni'

    def igni_env(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(('VIRTUAL_ENV', 'PYTHON'))}
        env.update(HOME=str(Path(self.o.work) / 'home'), HF_HUB_OFFLINE='1', NO_COLOR='1', **extra)
        return env


def short(r: httpx.Response | None, n: int = 300) -> str:
    if r is None:
        return ''
    return f'{r.status_code} {r.text[:n]}'


def claims(token: str) -> dict:
    return jwt.decode(token, options={'verify_signature': False})


# `Step.run` names Journey, which is defined after Step.
Step.model_rebuild()
