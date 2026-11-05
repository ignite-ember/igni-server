"""Step 3: the igni CLI signs in through a browser and chats through the proxy."""

import json
import os
from pathlib import Path
import subprocess
import time

from tools.e2e_journey.harness import claims, HERE, Journey, Outcome, Step, StepFailed
from tools.e2e_journey.people import MAX


def _project(j: Journey) -> None:
    j.igni_home.mkdir(parents=True, exist_ok=True)
    (j.igni_home / 'config.yaml').write_text(f'api_url: {j.api}\nportal_url: {j.o.portal}\nupdate_check_interval: 0\n')
    (j.project / '.igni').mkdir(parents=True, exist_ok=True)
    (j.project / 'hello.py').write_text("print('hi')\n")
    subprocess.run(['git', 'init', '-q'], cwd=j.project, check=False)


def cli_login(j: Journey) -> str:
    """``/login`` with a real browser: the CLI opens the portal, Chromium signs in with SSO."""
    s = j.state
    _project(j)
    url_file = Path(j.o.work) / 'cli-login-url.txt'
    url_file.unlink(missing_ok=True)
    log = Path(j.o.out) / 'logs' / 'cli-login.log'
    env = j.igni_env(BROWSER=f'{HERE / "record_url.sh"} %s', CLI_LOGIN_URL_FILE=str(url_file))
    cmd = [j.o.igni_python, str(HERE / 'cli_login.py'), '--project', str(j.project)]
    with open(log, 'w') as out:
        proc = subprocess.Popen(cmd, cwd=j.project, env=env, stdout=out, stderr=subprocess.STDOUT)
    j.note(f"$ (cd project && HOME=… BROWSER='record_url.sh %s' python cli_login.py) pid={proc.pid}")
    try:
        deadline = time.time() + 60
        while not url_file.exists() and proc.poll() is None and time.time() < deadline:
            time.sleep(0.3)
        if not url_file.exists():
            raise StepFailed(f'the CLI never opened a browser (rc={proc.poll()}): {log.read_text()[-500:]}')
        login_url = url_file.read_text().strip()
        j.note(f'the CLI opened {login_url}')
        answer = j.browser(
            'cli-authorize',
            url=login_url,
            portal=j.o.portal,
            label=j.o.sso_label,
            email=s.max_email,
            password=MAX.password,
            shot=j.shot('cli-login'),
        )
        if not answer.get('ok'):
            raise StepFailed(f'browser: {json.dumps(answer)[:600]}')
        proc.wait(60)
    finally:
        if proc.poll() is None:
            proc.kill()
    lines = log.read_text().strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError) as exc:
        raise StepFailed(f'rc={proc.returncode}: {log.read_text()[-500:]}') from exc
    if not result.get('ok'):
        raise StepFailed(json.dumps(result))
    creds = json.loads((j.igni_home / 'credentials.json').read_text())
    s.max_cli = creds['access_token']
    token_org = claims(s.max_cli).get('org')
    if token_org != s.org:
        raise StepFailed(f'CLI token org={token_org} != {s.org}')
    policy = sorted(os.listdir(j.igni_home / 'group-policy'))[:6]
    return (
        f"portal asked to sign in ({answer['askedToSignIn'].split('?')[0]}), SSO, then {answer['callback']}; "
        f'credentials.json for {creds.get("email")}, org-scoped token; group-policy: {policy}'
    )


def models(j: Journey) -> str:
    return j.call('GET', '/v1/chat/models', token=j.state.max_cli).text


def chat(j: Journey) -> str:
    Path(j.o.stub_log).write_text('')
    cmd = [j.o.igni_bin, '-m', 'Say hello', '--no-web']
    p = subprocess.run(cmd, cwd=j.project, env=j.igni_env(), capture_output=True, text=True, timeout=240)
    out = p.stdout + p.stderr
    j.note(f"$ (cd project && HOME=… igni -m 'Say hello' --no-web) -> rc={p.returncode}: {out[-300:]!r}")
    hits = [json.loads(line) for line in Path(j.o.stub_log).read_text().splitlines() if line.strip()]
    posts = [h for h in hits if h['method'] == 'POST']
    if p.returncode != 0 or 'STUB-REPLY' not in out or not posts:
        raise StepFailed(f'rc={p.returncode}, stub saw {len(posts)} POSTs; output tail: {out[-500:]}')
    j.state.cli_warnings = [line for line in out.splitlines() if 'cannot resolve' in line or 'Error' in line]
    last = posts[-1]['body']
    return f"reply printed; stub got {len(posts)} completion(s), model={last.get('model')}, stream={last.get('stream')}"


def usage(j: Journey) -> Outcome | str:
    s = j.state
    team_usage = j.admin('GET', '/v1/portal/usage', params={'scope': 'team'}).json()
    mine = j.call('GET', '/v1/portal/usage', token=s.max, org=s.org).json()
    row = next(m for m in j.team() if m['user_id'] == s.max_id)
    if not team_usage.get('total_tokens') or not mine.get('total_tokens'):
        return Outcome(status='FAIL', detail=f'usage not recorded: team={team_usage} personal={mine}')
    if not row.get('total_tokens'):
        return Outcome(
            status='FAIL', detail=f'usage recorded but the Team row says total_tokens={row.get("total_tokens")}'
        )
    return f"team total={team_usage['total_tokens']}; Max row total_tokens={row['total_tokens']}"


def repository_linking(j: Journey) -> Outcome:
    return Outcome(
        status='N/A',
        detail="No repository/link endpoint exists in igni server (README: 'Repository integration "
        "(GitHub, GitLab, webhooks) belongs to CodeIndex') and the CLI calls none.",
    )


STEPS = [
    Step(
        name='3a igni CLI /login in Chromium: CLI opens /cli-auth -> sign in with SSO -> CLI callback -> credentials',
        area='cli',
        run=cli_login,
    ),
    Step(name="3b the CLI token lists the deployment's models (/v1/chat/models)", area='cli', run=models),
    Step(name='3c `igni -m` chat session through the chat proxy to the stub model', area='cli', run=chat),
    Step(name='3d usage of that session is recorded for the member', area='cli', run=usage),
    Step(name='3e repository linking', area='cli', run=repository_linking),
]
