"""Step 7: a registered service is approved, its extension reaches the
member's igni, and igni calls it.

The service is the extension starter's Team Notes when that is checked
out beside this repository, and otherwise the journey's own fixture
(`service_fixture/`), built with igni-service from the igni checkout —
which is what CI runs, the starter having no remote to check out.
Skipped, each saying so, only when no service runs at all
(JOURNEY_SERVICE=none, or NO_SERVICE=1)."""

import json
from pathlib import Path
import re
import subprocess
import time

from tools.e2e_journey.harness import HERE, Journey, Outcome, Step, StepFailed

NO_SERVICE = Outcome(status='SKIP', detail='no service running (JOURNEY_SERVICE=none, or NO_SERVICE=1)')


def approve_service(j: Journey) -> Outcome | str:
    if not j.o.service_log:
        return NO_SERVICE
    listed = j.admin('GET', '/v1/portal/clients').json()
    items = listed.get('clients') if isinstance(listed, dict) else listed
    svc = next((c for c in items if c.get('name') == j.o.service_name), None)
    if not svc:
        return Outcome(
            status='FAIL', detail=f'no registered client named {j.o.service_name!r}: {[c.get("name") for c in items]}'
        )
    j.state.svc = svc['client_id']
    if svc.get('state') != 'approved':
        j.admin('POST', f"/v1/portal/clients/{svc['client_id']}/approve")
    after = j.admin('GET', f"/v1/portal/clients/{svc['client_id']}").json()
    extension = json.dumps(after.get('extension'))[:120] if after.get('extension') else None
    return (
        f"client {svc['client_id']} {svc.get('state')} -> {after.get('state')}; "
        f"base_url={after.get('base_url')}; extension={extension}"
    )


def enable_extension(j: Journey) -> Outcome | str:
    s = j.state
    if not s.svc:
        return NO_SERVICE
    j.admin('GET', f'/v1/portal/groups/{s.group}/extensions')
    j.admin('PUT', f'/v1/portal/groups/{s.group}/extensions/{j.o.extension_id}', json={'enabled': True})
    mine = j.call('GET', '/v1/extensions', token=s.max_cli).json()
    ids = [e.get('id') or e.get('extension_id') for e in (mine.get('extensions') or [])]
    if j.o.extension_id not in ids:
        return Outcome(status='FAIL', detail=f"member's /v1/extensions: {json.dumps(mine)[:300]}")
    return f'member sees {ids}'


def backend_installs_extension(j: Journey) -> Outcome | str:
    if not j.state.svc:
        return NO_SERVICE
    log = Path(j.o.out) / 'logs' / 'igni-backend.log'
    cmd = [j.o.igni_python, '-m', 'ember_code.backend', '--ws-port', '0']
    with open(log, 'w') as out:
        proc = subprocess.Popen(cmd, cwd=j.project, env=j.igni_env(), stdout=out, stderr=subprocess.STDOUT)
    j.note(f"$ (cd project && HOME=… {' '.join(cmd[1:])}) pid={proc.pid}, log {log}")
    listing, holder = '', None
    try:
        deadline = time.time() + 240
        while time.time() < deadline:
            if proc.poll() is not None:
                raise StepFailed(f'backend exited rc={proc.returncode}: {log.read_text()[-400:]}')
            found = re.search(r'"ws_url": "([^"]+)"', log.read_text(errors='replace'))
            if found and holder is None:
                # Extensions start once a client window attaches (BackendApp: wait_for_connection).
                holder = subprocess.Popen(
                    ['node', str(HERE / 'browser.mjs'), 'ws-hold', '--url', found.group(1), '--seconds', '230'],
                    stdout=open(Path(j.o.out) / 'logs' / 'ws-hold.log', 'w'),
                    stderr=subprocess.STDOUT,
                )
                j.note(f'$ node browser.mjs ws-hold --url {found.group(1)} (a client window attaches)')
            q = subprocess.run(
                [j.o.igni_bin, 'ext', 'list'],
                cwd=j.project,
                env=j.igni_env(),
                capture_output=True,
                text=True,
                timeout=60,
            )
            listing = q.stdout + q.stderr
            if j.o.extension_id in listing:
                break
            time.sleep(5)
    finally:
        if holder is not None:
            holder.kill()
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
    j.note(f'$ igni ext list -> {listing[-300:]!r}')
    if j.o.extension_id not in listing:
        hints = [line for line in log.read_text(errors='replace').splitlines() if 'xtension' in line][-6:]
        return Outcome(status='FAIL', detail=f'`igni ext list` never showed {j.o.extension_id}; backend log: {hints}')
    return f'`igni ext list` shows: {listing.strip()[-200:]}'


def service_log_since(j: Journey, position: int) -> str:
    return Path(j.o.service_log).read_text(errors='replace')[position:] if j.o.service_log else ''


def service_log_size(j: Journey) -> int:
    return Path(j.o.service_log).stat().st_size if j.o.service_log else 0


def subject_access(j: Journey) -> Outcome | str:
    if not j.state.svc:
        return NO_SERVICE
    position = service_log_size(j)
    r = j.admin('POST', '/v1/portal/subject-access', json={'email': j.state.max_email})
    time.sleep(1)
    hits = re.findall(r'"GET /v1/igni/subjects/[^ ]+/export[^"]*" (\d+)', service_log_since(j, position))
    report = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
    missing = report.get('missing') or (report.get('services') or {}).get('missing')
    if not hits:
        return Outcome(status='FAIL', detail=f'the service saw no export call; report missing={missing}')
    return f'the service answered export with {hits}; report missing={missing}'


STEPS = [
    Step(
        name='7a the service (Team Notes, or the journey fixture) registered itself; the super admin approves it',
        area='service',
        run=approve_service,
    ),
    Step(
        name="7b enable the service's extension for the member's group; the member's CLI token sees it",
        area='service',
        run=enable_extension,
    ),
    Step(
        name='7c the backend the web/desktop clients spawn installs the extension igni server distributes',
        area='service',
        run=backend_installs_extension,
    ),
    Step(
        name="7d subject access: igni calls the service's export hook (outbound callback)",
        area='service',
        run=subject_access,
    ),
]
