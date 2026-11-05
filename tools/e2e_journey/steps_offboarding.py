"""Step 8: a member leaves — removal, export, erasure, and the trail of it."""

import re
import time

from tools.e2e_journey.harness import Journey, Outcome, short, Step, StepFailed
from tools.e2e_journey.steps_service import service_log_since, service_log_size


def remove_member(j: Journey) -> Outcome | str:
    s = j.state
    j.admin('DELETE', f'/v1/portal/team/{s.max_id}')
    still = [m for m in j.team() if m['user_id'] == s.max_id]
    listed = j.call('GET', '/v1/chat/models', token=s.max_cli, expect=())
    chat = j.call(
        'POST',
        '/v1/chat/completions',
        token=s.max_cli,
        expect=(),
        json={'model': 'e2e-stub-model', 'messages': [{'role': 'user', 'content': 'still here?'}]},
    )
    if still or chat.status_code == 200:
        return Outcome(status='FAIL', detail=f'after removal: team rows={still}; CLI token chat -> {chat.status_code}')
    return (
        f"gone from the team; the removed member's CLI token: "
        f'models -> {listed.status_code}, chat -> {chat.status_code}'
    )


def export_removed(j: Journey) -> str:
    s = j.state
    time.sleep(2)
    r = j.admin('POST', f'/v1/portal/orgs/{s.org}/producers/person/{s.max_id}/exports', json={}, expect=())
    if r.status_code != 202:
        raise StepFailed(short(r))
    export = r.json()
    listed = j.admin('GET', f'/v1/portal/orgs/{s.org}/exports').json()
    one = j.admin('GET', f"/v1/portal/orgs/{s.org}/exports/{export['export_id']}").json()
    return (
        f"export {export['export_id']} state={one.get('state') or one.get('status')} (fulfilled by a platform "
        f"client pulling /v1/platform/exports/pull; none in the free base); list has {len(listed.get('exports', []))}"
    )


def removal_without_a_drain(j: Journey) -> Outcome | str:
    s = j.state
    out = {}
    for who, drain_first in (('lin', False), ('kim', True)):
        email = f'{who}@{j.o.domain}'
        j.admin('POST', '/v1/portal/team/invite', json={'email': email, 'role': 'member'})
        token = j.sign_up(who.title(), 'E2e', email)
        uid = j.call('GET', '/v1/portal/me', token=token, org=s.org).json()['user_id']
        if drain_first:
            j.admin('GET', f'/v1/portal/orgs/{s.org}/erasures')
        j.admin('DELETE', f'/v1/portal/team/{uid}')
        j.admin('GET', f'/v1/portal/orgs/{s.org}/erasures')
        r = j.admin('POST', f'/v1/portal/orgs/{s.org}/producers/person/{uid}/exports', json={}, expect=())
        out[f'{who} (feed drained before removal: {drain_first})'] = r.status_code
    if any(code != 202 for code in out.values()):
        return Outcome(status='FAIL', detail=f'export after removal: {out}')
    return f'export after removal: {out}'


def erasure(j: Journey) -> Outcome | str:
    position = service_log_size(j)
    r = j.admin('POST', '/v1/portal/erasure', json={'email': j.state.max_email}, expect=())
    time.sleep(1)
    hits = re.findall(r'"DELETE /v1/igni/subjects/[^ ]+ HTTP/1.1" (\d+)', service_log_since(j, position))
    if r.status_code >= 400:
        return Outcome(status='FAIL', detail=f'erasure -> {short(r)}; service DELETE calls: {hits}')
    if j.state.svc and not hits:
        return Outcome(status='FAIL', detail=f'erasure -> {r.status_code} but the service saw no DELETE')
    return f'erasure -> {short(r, 200)}; service DELETE calls: {hits}'


def offboarding_trail(j: Journey) -> str:
    events = j.audit()
    actions = [f"{e.get('method')} {e.get('route')} {e.get('status_code')}" for e in events]
    return f'{len(events)} rows now (was {j.state.audit_count}); last actions: {actions[:12]}'


STEPS = [
    Step(name='8a remove the member from the organisation', area='offboarding', run=remove_member),
    Step(name="8b export a removed member's data (portal) and list it", area='offboarding', run=export_removed),
    Step(
        name='8c removal is recorded when nothing has read the producers feed since the person joined',
        area='offboarding',
        run=removal_without_a_drain,
    ),
    Step(name='8d erasure: igni asks every approved service to erase the person', area='offboarding', run=erasure),
    Step(name='8e the audit trail recorded the offboarding and the decisions', area='audit', run=offboarding_trail),
]
