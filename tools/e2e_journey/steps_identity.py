"""Steps 0–2: the stack answers, the first account and its organisation,
every way in (password, one-time code, SSO through the real IdP), a team."""

import json

import httpx

from tools.e2e_journey.harness import claims, Journey, Outcome, Step, StepFailed
from tools.e2e_journey.people import LOCAL_PASSWORD, MAX


def preflight(j: Journey) -> str:
    j.call('GET', '/health')
    r = j.call('GET', '/ready')
    if r.json().get('status') != 'ready':
        raise StepFailed(f'/ready says {r.text[:200]}')
    m = httpx.get(f'{j.o.stub}/v1/models', timeout=5)
    j.note(f'GET stub /v1/models -> {m.status_code} {m.text[:120]}')
    m.raise_for_status()
    methods = j.call('GET', '/v1/auth/methods').json()
    sso = methods.get('sso') or {}
    if not sso.get('enabled') or sso.get('label') != j.o.sso_label:
        raise StepFailed(f'SSO is not offered as {j.o.sso_label!r}: {methods}')
    return f'ready; auth methods {methods}'


def sign_up_first_account(j: Journey) -> str:
    s = j.state
    s.ada_email = f'ada@{j.o.domain}'
    token = j.sign_up('Ada', 'Lovelace', s.ada_email)
    me = j.call('GET', '/v1/portal/me', token=token).json()
    s.ada, s.ada_id = token, me['user_id']
    return f"user {me['user_id']} super_admin={me['is_super_admin']} orgs={len(me['organizations'])}"


def create_organisation(j: Journey) -> str:
    s = j.state
    r = j.call(
        'POST',
        '/v1/portal/organizations',
        token=s.ada,
        json={'display_name': f'Acme {j.o.domain}', 'domains': [j.o.domain]},
    )
    s.org = r.json()['id']
    me = j.admin('GET', '/v1/portal/me').json()
    if me.get('role') != 'admin':
        raise StepFailed(f'creator is not admin: {me}')
    return f"org {s.org} slug={r.json()['slug']}; creator role=admin"


def password_and_code(j: Journey) -> str:
    s = j.state
    first = j.call('POST', '/v1/sign-in', json={'email': s.ada_email, 'password': LOCAL_PASSWORD}).json()
    position = j.log_size()
    j.call('POST', '/v1/send-sign-in-code', json={'email': s.ada_email})
    code = j.code_from_log('sign-in', s.ada_email, position)
    second = j.call('POST', '/v1/sign-in-with-code', json={'email': s.ada_email, 'code': code}).json()
    j.call('GET', '/v1/portal/me', token=second['access_token'], org=s.org)
    j.call('POST', '/v1/sign-in', json={'email': s.ada_email, 'password': 'not-the-password'}, expect=(400, 401, 403))
    s.ada = first['access_token']
    return f"both sign-ins issued tokens (iss={claims(s.ada)['iss']}); a wrong password is refused"


def invite_member(j: Journey) -> str:
    s = j.state
    s.max_email = f'{MAX.local_part}@{j.o.domain}'
    invite = j.admin('POST', '/v1/portal/team/invite', json={'email': s.max_email, 'role': 'member'}).json()
    row = next((m for m in j.team() if m['email'] == s.max_email), None)
    if not row or row['status'] != 'invited':
        raise StepFailed(f'team row after invite: {row}')
    return f"invite_url={invite.get('invite_url')} emailed={invite.get('emailed')}; team row status=invited"


def sso_wrong_password(j: Journey) -> Outcome | str:
    s = j.state
    answer = j.browser(
        'sso-wrong-password',
        portal=j.o.portal,
        label=j.o.sso_label,
        email=s.max_email,
        password='not-' + MAX.password,
        shot=j.shot('sso-wrong-password'),
    )
    row = next((m for m in j.team() if m['email'] == s.max_email), None)
    if not answer.get('ok'):
        return Outcome(status='FAIL', detail=f'browser: {json.dumps(answer)[:400]}')
    if not row or row['status'] != 'invited':
        return Outcome(status='FAIL', detail=f'Dex refused, but the team row is now {row}')
    return f"Dex said {answer['dexSays']!r}; the portal sent the browser to {answer['portal']}; no token; still invited"


def sso_sign_in(j: Journey) -> str:
    s = j.state
    answer = j.browser(
        'sso-signin',
        portal=j.o.portal,
        label=j.o.sso_label,
        email=s.max_email,
        password=MAX.password,
        shot=j.shot('sso-signin'),
    )
    if not answer.get('ok') or not answer.get('token'):
        raise StepFailed(f'browser: {json.dumps({k: v for k, v in answer.items() if k != "token"})[:600]}')
    token = answer['token']
    me = j.call('GET', '/v1/portal/me', token=token, org=s.org).json()
    s.max, s.max_id = token, me['user_id']
    row = next((m for m in j.team() if m['email'] == s.max_email), None)
    if me.get('role') != 'member' or not row or row['status'] != 'active':
        raise StepFailed(f'after SSO: me.role={me.get("role")} team row={row}')
    return (
        f"Dex at {answer['dex']}; landed on {answer['url']}; {me.get('email')} is an active member "
        f"(name {me.get('full_name') or me.get('first_name')!r}); trail: {' > '.join(answer.get('trail', [])[:8])}"
    )


def group(j: Journey) -> str:
    s = j.state
    g = j.admin('POST', '/v1/portal/groups', json={'name': 'Engineering', 'description': 'e2e'}).json()
    s.group = g['id']
    j.admin('POST', f"/v1/portal/groups/{g['id']}/members", json={'user_id': s.max_id})
    mine = j.call('GET', '/v1/portal/me/group', token=s.max, org=s.org).json()
    if (mine.get('id') or mine.get('group', {}).get('id')) != g['id']:
        raise StepFailed(f'/me/group says {json.dumps(mine)[:200]}')
    return f"group {g['id']}; Max's /me/group is Engineering"


STEPS = [
    Step(name='0 preflight: API /health, /ready, stub model, SSO offered', area='setup', run=preflight),
    Step(
        name='1a sign up the first account (email + one-time code from LOG_SIGN_IN_CODES)',
        area='identity',
        run=sign_up_first_account,
    ),
    Step(name='1b create an organisation (with a claimed domain)', area='identity', run=create_organisation),
    Step(name='1c sign in with password, and with a one-time code', area='identity', run=password_and_code),
    Step(name='2a invite a member', area='team', run=invite_member),
    Step(
        name='2b SSO with the wrong IdP password: portal -> Dex refuses -> not signed in',
        area='identity',
        run=sso_wrong_password,
    ),
    Step(
        name='2c SSO sign-in in Chromium: portal -> "Acme SSO" -> Dex login -> callback -> signed in, invite claimed',
        area='identity',
        run=sso_sign_in,
    ),
    Step(name='2d create a group and move the member into it', area='team', run=group),
]
